from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.errors import RasterioError
from rasterio.warp import reproject

from core_analyst.data_sources import DataSource, DynamicDataError, RasterGrid
from core_analyst.validators.raster_validator import RasterValidator


# Reasons a primary dataset can genuinely be unusable: missing/unreadable files and live
# feed failures. Anything else is a bug and must not be hidden behind a silent downgrade.
PRIMARY_SOURCE_ERRORS = (DynamicDataError, OSError, RasterioError, ValueError, KeyError)


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
            source_signature = {
                "crs": str(dataset.crs),
                "resolution": tuple(float(value) for value in dataset.res),
                "transform": str(dataset.transform),
                "shape": [dataset.height, dataset.width],
                "bounds": [float(value) for value in dataset.bounds],
                "dtype": dataset.dtypes[0],
                "nodata": None if dataset.nodata is None else str(dataset.nodata),
            }
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

        grid = RasterGrid(
            name=self.name,
            data=data,
            profile=profile,
            source_type="static",
            metadata={
                "path": str(self.path),
                "alignment": "native_grid" if reference is None else "aligned_to_reference_grid",
                "spatial_qa": {
                    "source_grid": source_signature,
                    "reference_grid": None if reference is None else RasterValidator().grid_signature(reference),
                    "correction_operation": None if reference is None else "reproject_resample_align_to_reference_grid",
                    "resampling_method": self.resampling.name,
                    "nodata": RasterValidator().nodata_status(RasterGrid(self.name, data, profile, "static")),
                },
            },
        )
        if reference is not None:
            comparison = RasterValidator().compare_grid_to_reference(grid, reference)
            grid.metadata["spatial_qa"]["diagnostics"] = comparison["diagnostics"]
        return grid


class DerivedSlopeSource(DataSource):
    """Derive slope in degrees from a DEM when no slope raster is available."""

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
                "prototype_note": "Derived from the DEM because a slope raster was unavailable.",
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


class FallbackDataSource(DataSource):
    """Try a primary source and retain an explicit fallback with provenance."""

    def __init__(self, name: str, primary: DataSource, fallback: DataSource):
        self.name = name
        self.primary = primary
        self.fallback = fallback

    def get_data(self, reference: RasterGrid | None = None) -> RasterGrid:
        try:
            grid = self.primary.get_data(reference=reference)
            grid.name = self.name
            grid.metadata = {
                **grid.metadata,
                "data_selection": {
                    "selected": "primary",
                    "fallback_available": True,
                    "reason": "Primary real dataset was available.",
                },
            }
            return grid
        except PRIMARY_SOURCE_ERRORS as exc:
            grid = self.fallback.get_data(reference=reference)
            grid.name = self.name
            grid.metadata = {
                **grid.metadata,
                "data_selection": {
                    "selected": "fallback",
                    "fallback_reason": str(exc),
                    "primary_source": getattr(self.primary, "path", repr(self.primary)),
                },
            }
            return grid


class RiverNetworkPresenceSource(DataSource):
    """Convert a standardized river network raster to a normalized fluvial context layer."""

    def __init__(self, rivers_source: DataSource):
        self.rivers_source = rivers_source

    def get_data(self, reference: RasterGrid | None = None) -> RasterGrid:
        rivers = self.rivers_source.get_data(reference=reference)
        valid = np.isfinite(rivers.data)
        if reference is not None:
            valid &= np.isfinite(reference.data)
        river_network = np.where(rivers.data > 0, 1.0, 0.0).astype("float32")
        river_network[~valid] = np.nan
        return RasterGrid(
            "river_network",
            river_network,
            rivers.profile.copy(),
            "static_reference",
            {
                "derived_from": rivers.metadata,
                "interpretation": (
                    "Binary river/water-network context from the configured source raster. This is a first fluvial static factor; "
                    "future versions should replace or augment it with river distance, catchment topology, "
                    "and flow routing."
                ),
            },
        )


LANDCOVER_RUNOFF_SCORE = {
    1: 0.30,   # Broadleaved woodland
    2: 0.30,   # Coniferous woodland
    3: 0.60,   # Arable and horticulture
    4: 0.45,   # Improved grassland
    5: 0.30,   # Neutral grassland
    6: 0.30,   # Calcareous grassland
    7: 0.30,   # Acid grassland
    8: 0.15,   # Fen, marsh and swamp
    9: 0.15,   # Heather
    10: 0.15,  # Heather grassland
    11: 0.15,  # Bog
    12: 0.15,  # Inland rock
    13: 0.15,  # Saltwater
    14: 0.15,  # Freshwater
    15: 0.15,  # Supralittoral rock
    16: 0.15,  # Supralittoral sediment
    17: 0.15,  # Littoral rock
    18: 0.15,  # Littoral sediment
    19: 0.15,  # Saltmarsh
    20: 0.95,  # Urban
    21: 0.70,  # Suburban
}


class ImperviousnessCompositeSource(DataSource):
    """Build a normalized imperviousness-based runoff susceptibility proxy.

    The imperviousness layer is a normalized proxy used to represent
    relative surface-runoff susceptibility. It is derived from built-up
    extent, greenspace adjustment, and land-cover characteristics and
    should not be interpreted as a direct measurement of the percentage
    of physically impervious surface.
    """

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
        valid_mask = np.isfinite(built.data)
        if reference is not None:
            valid_mask &= np.isfinite(reference.data)

        impervious = np.where(built.data > 0, 0.95, 0.25).astype("float32")
        impervious[~valid_mask] = np.nan
        metadata: dict[str, Any] = {
            "built_up": built.metadata,
            "unit": "normalized_index",
            "score_range": [0.0, 1.0],
            "built_up_baseline": {
                "built_up_present": 0.95,
                "built_up_absent": 0.25,
            },
            "interpretation": (
                "Normalized imperviousness-based runoff susceptibility proxy; values are heuristic "
                "relative scores, not measured impervious-surface percentages."
            ),
        }
        if self.greenspace_source is not None:
            green = self.greenspace_source.get_data(reference=built)
            green_valid = np.isfinite(green.data)
            valid_mask &= green_valid
            # Greenspace is a runoff-reducing modifier, not a measured 15% imperviousness estimate.
            impervious = np.where(green.data > 0, np.minimum(impervious, 0.15), impervious).astype("float32")
            impervious[~valid_mask] = np.nan
            metadata["greenspace"] = green.metadata
            metadata["greenspace_adjustment"] = {"greenspace_present_maximum": 0.15}
        if self.landcover_source is not None:
            landcover = self.landcover_source.get_data(reference=built)
            landcover_runoff = landcover_to_runoff_score(landcover.data)
            valid_mask &= np.isfinite(landcover_runoff)
            impervious = np.maximum(impervious, landcover_runoff).astype("float32")
            impervious[~valid_mask] = np.nan
            metadata["landcover"] = landcover.metadata
            metadata["landcover_runoff_score"] = LANDCOVER_RUNOFF_SCORE
        metadata["prototype_note"] = (
            "Normalized imperviousness-based runoff susceptibility proxy from the available built-up areas, "
            "greenspace, and optional land-cover classes. Replace or calibrate with authoritative "
            "imperviousness/runoff observations when available."
        )
        finite = np.isfinite(impervious)
        if finite.any():
            if float(np.nanmin(impervious[finite])) < 0.0 or float(np.nanmax(impervious[finite])) > 1.0:
                raise ValueError("Imperviousness proxy values must remain within [0, 1].")
        return RasterGrid("imperviousness", impervious, built.profile.copy(), "static_proxy", metadata)


def landcover_to_runoff_score(landcover: np.ndarray) -> np.ndarray:
    """Map project UKCEH land-cover class codes to normalized runoff scores."""

    scores = np.full_like(landcover, np.nan, dtype="float32")
    for code, score in LANDCOVER_RUNOFF_SCORE.items():
        scores = np.where(landcover == code, score, scores)
    return scores.astype("float32")
