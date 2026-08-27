from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject

from core_analyst.data_sources import DataSource, RasterGrid


class AlignedRasterSource(DataSource):
    """Read a raster and align it to a reference grid at source-boundary time."""

    def __init__(
        self,
        name: str,
        path: str | Path,
        resampling: Resampling = Resampling.nearest,
        fill_value: float = 0.0,
        use_source_mask: bool = False,
    ):
        self.name = name
        self.path = str(path)
        self.resampling = resampling
        self.fill_value = fill_value
        self.use_source_mask = use_source_mask

    def get_data(self, reference: RasterGrid | None = None) -> RasterGrid:
        if not self.path.startswith("OpenFileGDB:") and not Path(self.path).exists():
            raise FileNotFoundError(f"Raster not found for {self.name}: {self.path}")

        with rasterio.open(self.path) as dataset:
            if reference is None:
                if self.use_source_mask or dataset.nodata is not None:
                    masked = dataset.read(1, masked=True).astype("float32")
                    data = masked.filled(np.nan).astype("float32")
                else:
                    data = dataset.read(1).astype("float32")
                profile = dataset.profile.copy()
                profile.update(nodata=np.nan)
            else:
                profile = reference.profile.copy()
                profile.update(dtype="float32", count=1)
                data = np.full(reference.data.shape, self.fill_value, dtype="float32")
                reproject(
                    source=rasterio.band(dataset, 1),
                    destination=data,
                    src_transform=dataset.transform,
                    src_crs=dataset.crs,
                    src_nodata=dataset.nodata,
                    dst_transform=profile["transform"],
                    dst_crs=profile["crs"],
                    dst_nodata=self.fill_value,
                    resampling=self.resampling,
                )

        return RasterGrid(
            name=self.name,
            data=data,
            profile=profile,
            source_type="static",
            metadata={
                "path": str(self.path),
                "alignment": "native_grid" if reference is None else "aligned_to_reference_grid",
            },
        )


class DerivedSlopeSource(DataSource):
    """Temporary slope source derived from DTM when Component 1 slope is absent."""

    def __init__(self, dem_source: DataSource):
        self.dem_source = dem_source

    def get_data(self, reference: RasterGrid | None = None) -> RasterGrid:
        dem = self.dem_source.get_data(reference=reference)
        transform = dem.profile["transform"]
        pixel_x = abs(float(transform.a))
        pixel_y = abs(float(transform.e))
        gy, gx = np.gradient(dem.data.astype("float32"), pixel_y, pixel_x)
        slope_degrees = np.degrees(np.arctan(np.hypot(gx, gy))).astype("float32")
        return RasterGrid(
            name="slope",
            data=slope_degrees,
            profile=dem.profile.copy(),
            source_type="derived_static",
            metadata={
                "derived_from": dem.metadata,
                "prototype_note": "Derived inside MVP because a Component 1 slope raster is not present.",
                "unit": "degrees",
            },
        )


class TopographicFlowProxySource(DataSource):
    """Prototype flow-accumulation proxy used until Component 1 supplies hydrology output."""

    def __init__(self, dem_source: DataSource):
        self.dem_source = dem_source

    def get_data(self, reference: RasterGrid | None = None) -> RasterGrid:
        dem = reference if reference is not None and reference.name == "dem" else self.dem_source.get_data(reference=reference)
        values = dem.data.astype("float32")
        finite = np.isfinite(values)
        lowness = np.zeros_like(values, dtype="float32")
        if finite.any():
            minimum = float(np.nanmin(values[finite]))
            maximum = float(np.nanmax(values[finite]))
            if maximum > minimum:
                lowness = 1.0 - ((values - minimum) / (maximum - minimum))

        gy, gx = np.gradient(values)
        flatness = 1.0 / (1.0 + np.hypot(gx, gy))
        proxy = np.clip(lowness * flatness * 1000.0, 0.0, 1000.0).astype("float32")
        return RasterGrid(
            name="flow_accumulation",
            data=proxy,
            profile=dem.profile.copy(),
            source_type="derived_static_proxy",
            metadata={
                "derived_from": dem.metadata,
                "prototype_note": (
                    "Topographic convergence proxy only. Replace with Component 1 hydrological "
                    "flow accumulation after sink filling and flow routing."
                ),
            },
        )


class ImperviousnessCompositeSource(DataSource):
    """Build a 0-100 imperviousness estimate from available urban/green rasters."""

    def __init__(
        self,
        built_up_source: DataSource,
        greenspace_source: DataSource | None = None,
        landcover_source: DataSource | None = None,
    ):
        self.built_up_source = built_up_source
        self.greenspace_source = greenspace_source
        self.landcover_source = landcover_source

    def get_data(self, reference: RasterGrid | None = None) -> RasterGrid:
        built = self.built_up_source.get_data(reference=reference)
        impervious = np.where(built.data > 0, 95.0, 25.0).astype("float32")
        metadata: dict[str, Any] = {
            "built_up": built.metadata,
            "unit": "percent_proxy",
            "prototype_note": "Built-up presence converted to a runoff/imperviousness proxy.",
        }
        if self.greenspace_source is not None:
            green = self.greenspace_source.get_data(reference=built)
            impervious = np.where(green.data > 0, np.minimum(impervious, 15.0), impervious).astype("float32")
            metadata["greenspace"] = green.metadata
        if self.landcover_source is not None:
            landcover = self.landcover_source.get_data(reference=built)
            landcover_runoff = landcover_to_runoff_score(landcover.data)
            impervious = np.maximum(impervious, landcover_runoff).astype("float32")
            metadata["landcover"] = landcover.metadata
        metadata["prototype_note"] = (
            "Runoff proxy from built-up areas, greenspace, and UKCEH land-cover classes. "
            "Replace with a calibrated imperviousness/runoff coefficient layer when available."
        )
        return RasterGrid("imperviousness", impervious, built.profile.copy(), "static_proxy", metadata)


def landcover_to_runoff_score(landcover: np.ndarray) -> np.ndarray:
    """Map UKCEH land-cover classes to a simple 0-100 pluvial runoff proxy."""

    scores = np.full_like(landcover, 35.0, dtype="float32")
    mapping = {
        1: 25.0,   # broadleaved woodland
        2: 30.0,   # coniferous woodland
        3: 60.0,   # arable and horticulture
        4: 45.0,   # improved grassland
        5: 35.0,
        6: 30.0,
        7: 30.0,
        8: 20.0,
        9: 20.0,
        10: 25.0,
        11: 20.0,
        12: 10.0,
        13: 10.0,
        14: 10.0,
        15: 10.0,
        16: 10.0,
        17: 10.0,
        18: 10.0,
        19: 10.0,
        20: 95.0,  # urban
        21: 70.0,  # suburban
    }
    for code, score in mapping.items():
        scores = np.where(landcover == code, score, scores)
    scores = np.where(np.isfinite(landcover), scores, np.nan)
    return scores.astype("float32")
