"""Run the reused Core Analyst workflow and read its latest GeoTIFF."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform

from core_analyst.data_sources import RasterGrid, write_raster
from core_analyst.tools.classification import HazardClassifier
from core_analyst.tools.factor_analyzers import (
    ElevationRiskAnalyzer,
    FlowAccumulationAnalyzer,
    ImperviousnessAnalyzer,
    RainfallAnalyzer,
    SlopeRiskAnalyzer,
)
from core_analyst.tools.weighted_overlay import WeightedOverlayAnalyzer
from core_analyst.utils.config import load_config
from core_analyst.validators.raster_validator import RasterValidator
from core_analyst.workflows.oasis_real_data import build_oasis_input_sources

from oasis.domain.hazard import interpret_hazard_class
from oasis.models.current_hazard import CurrentHazardSnapshot
from oasis.models.hazard import HazardLookupResult


WARNINGS = [
    "Latest calculated prototype snapshot, not a live observation, forecast, or operational flood warning.",
    "SEPA rain-gauge observations are interpolated across Glasgow using inverse-distance weighting.",
    "Terrain/runoff weights and thresholds are MVP values and require scientific validation.",
]


class CoreAnalystCurrentHazard:
    def __init__(
        self,
        *,
        input_dir: Path,
        config_path: Path,
        output_dir: Path,
        raster_path: Path,
        wms_url: str,
        layer: str,
    ) -> None:
        self._input_dir = input_dir
        self._config_path = config_path
        self._output_dir = output_dir
        self._raster_path = raster_path
        self._metadata_path = output_dir / "analysis_metadata.json"
        self._wms_url = wms_url
        self._layer = layer

    async def refresh(self) -> CurrentHazardSnapshot:
        return await asyncio.to_thread(self._refresh_sync)

    async def status(self) -> CurrentHazardSnapshot:
        return self._snapshot()

    async def lookup(self, latitude: float, longitude: float) -> HazardLookupResult:
        snapshot = self._snapshot()
        if not snapshot.available:
            raise FileNotFoundError("No current hazard snapshot has been generated.")
        return await asyncio.to_thread(self._lookup_sync, latitude, longitude, snapshot)

    def _refresh_sync(self) -> CurrentHazardSnapshot:
        config = load_config(self._config_path)
        sources = build_oasis_input_sources(
            self._input_dir,
            rainfall_source="sepa",
            sepa_station_numbers=["auto"],
        )
        grids = {"dem": sources["dem"].get_data()}
        reference = grids["dem"]
        for name in ("slope", "flow_accumulation", "imperviousness"):
            grids[name] = sources[name].get_data(reference=reference)
        RasterValidator().validate_grids(grids).raise_if_invalid()

        observed = sources["rainfall"].get_data(reference=reference)
        factors = {
            "elevation": ElevationRiskAnalyzer().analyze(grids["dem"].data),
            "slope": SlopeRiskAnalyzer().analyze(grids["slope"].data),
            "flow_accumulation": FlowAccumulationAnalyzer().analyze(
                grids["flow_accumulation"].data
            ),
            "imperviousness": ImperviousnessAnalyzer().analyze(
                grids["imperviousness"].data
            ),
        }
        overlay = WeightedOverlayAnalyzer()
        static_susceptibility = overlay.analyze(factors, config["static_weights"])
        rainfall_risk = RainfallAnalyzer(
            thresholds=config["rainfall_thresholds"]
        ).analyze(observed.data)
        current_index = overlay.analyze(
            {
                "static_susceptibility": static_susceptibility,
                "observed_rainfall": rainfall_risk,
            },
            config["current_weights"],
        )
        core_classes = HazardClassifier().classify(
            current_index,
            config["classification"],
        )
        core_classes[~np.isfinite(current_index)] = 0
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._raster_path.parent.mkdir(parents=True, exist_ok=True)
        write_raster(
            self._output_dir / "current_hazard_index.tif",
            RasterGrid(
                "current_hazard_index",
                current_index,
                reference.profile.copy(),
                "analysis_output",
            ),
        )
        profile = reference.profile.copy()
        profile.update(dtype="uint8", nodata=0)
        temporary_raster = self._raster_path.with_suffix(".new.tif")
        write_raster(
            temporary_raster,
            RasterGrid(
                "current_hazard_class",
                core_classes,
                profile,
                "analysis_output",
            ),
            dtype="uint8",
        )
        temporary_raster.replace(self._raster_path)

        observations = observed.metadata["observations"]
        station_times = [
            station["timestamp_utc"] for station in observations["stations"]
        ]
        metadata = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "observation_start": min(station_times),
            "observation_end": max(station_times),
            "station_count": len(station_times),
            "dataset": self._layer,
            "analysis_method": "Core Analyst current pluvial weighted-overlay MVP",
            "weights": {
                "static": config["static_weights"],
                "current": config["current_weights"],
            },
            "classification": {
                "source": "Core Analyst 1=Low, 2=Medium, 3=High",
                "published": "Core Analyst native 1=Low, 2=Medium, 3=High, 0=NoData",
            },
            "rainfall": observations,
            "warnings": WARNINGS,
        }
        temporary_metadata = self._metadata_path.with_suffix(".new.json")
        temporary_metadata.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        temporary_metadata.replace(self._metadata_path)
        return self._snapshot()

    def _snapshot(self) -> CurrentHazardSnapshot:
        if not self._raster_path.is_file() or not self._metadata_path.is_file():
            return CurrentHazardSnapshot(available=False, dataset=self._layer, warnings=WARNINGS)
        metadata = json.loads(self._metadata_path.read_text(encoding="utf-8"))
        return CurrentHazardSnapshot(
            available=True,
            generated_at=metadata["generated_at"],
            observation_start=metadata["observation_start"],
            observation_end=metadata["observation_end"],
            station_count=metadata["station_count"],
            dataset=self._layer,
            warnings=metadata["warnings"],
        )

    def _lookup_sync(
        self,
        latitude: float,
        longitude: float,
        snapshot: CurrentHazardSnapshot,
    ) -> HazardLookupResult:
        with rasterio.open(self._raster_path) as dataset:
            xs, ys = transform("EPSG:4326", dataset.crs, [longitude], [latitude])
            value = next(dataset.sample([(xs[0], ys[0])], masked=True))
            class_value = None if bool(value.mask[0]) else int(value[0])
        risk_level, risk_label = interpret_hazard_class(class_value)
        return HazardLookupResult(
            latitude=latitude,
            longitude=longitude,
            class_value=class_value,
            risk_level=risk_level,
            risk_label=risk_label,
            provider="OASIS Core Analyst with latest SEPA rainfall",
            dataset=self._layer,
            source_url=self._wms_url,
            retrieved_at=datetime.now(timezone.utc),
            snapshot_time=snapshot.generated_at,
            warnings=snapshot.warnings,
        )
