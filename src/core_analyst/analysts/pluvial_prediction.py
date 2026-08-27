from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from core_analyst.data_sources import DataSource, RasterGrid, write_raster
from core_analyst.tools.classification import HazardClassifier
from core_analyst.tools.factor_analyzers import (
    ElevationRiskAnalyzer,
    FlowAccumulationAnalyzer,
    ImperviousnessAnalyzer,
    RainfallAnalyzer,
    SlopeRiskAnalyzer,
)
from core_analyst.tools.random_forest_prediction import RandomForestRiskPredictor
from core_analyst.tools.weighted_overlay import WeightedOverlayAnalyzer
from core_analyst.validators.raster_validator import RasterValidator


class PluvialPredictionAnalyst:
    """State-aware pluvial current/future prediction workflow.

    Static/baseline conditions and observations produce H(t0). H(t0) plus future
    rainfall forcing produce H(t0 + delta).
    """

    required_static_inputs = ("dem", "slope", "flow_accumulation", "imperviousness")

    def __init__(self, config: dict[str, Any], output_dir: str | Path):
        self.config = config
        self.output_dir = Path(output_dir)
        self.validator = RasterValidator()
        self.overlay = WeightedOverlayAnalyzer()
        self.classifier = HazardClassifier()

    def run(
        self,
        static_sources: dict[str, DataSource],
        observed_rainfall_source: DataSource,
        forecast_rainfall_source: DataSource,
        baseline_sources: dict[str, DataSource] | None = None,
        prediction_horizon_hours: int | None = None,
    ) -> dict[str, Any]:
        grids: dict[str, RasterGrid] = {"dem": static_sources["dem"].get_data()}
        reference = grids["dem"]
        for name in ("slope", "flow_accumulation", "imperviousness"):
            grids[name] = static_sources[name].get_data(reference=reference)
        self.validator.validate_grids(grids).raise_if_invalid()

        observed = observed_rainfall_source.get_data(reference=reference)
        forecast = forecast_rainfall_source.get_data(reference=reference)
        rainfall_analyzer = RainfallAnalyzer(thresholds=self.config.get("rainfall_thresholds"))

        static_factors = {
            "elevation": ElevationRiskAnalyzer().analyze(grids["dem"].data),
            "slope": SlopeRiskAnalyzer().analyze(grids["slope"].data),
            "flow_accumulation": FlowAccumulationAnalyzer().analyze(grids["flow_accumulation"].data),
            "imperviousness": ImperviousnessAnalyzer().analyze(grids["imperviousness"].data),
        }
        static_susceptibility = self.overlay.analyze(static_factors, self.config["static_weights"])
        observed_rainfall_risk = rainfall_analyzer.analyze(observed.data)
        forecast_rainfall_risk = rainfall_analyzer.analyze(forecast.data)

        current = self._weighted_sum(
            {
                "static_susceptibility": static_susceptibility,
                "observed_rainfall": observed_rainfall_risk,
            },
            self.config["current_weights"],
        )
        future_result = self._predict_future_with_random_forest(
            static_factors,
            static_susceptibility,
            observed_rainfall_risk,
            forecast_rainfall_risk,
            current,
        )
        future = future_result.risk

        baseline_metadata = self._read_baseline_metadata(baseline_sources, reference)
        outputs = self._write_outputs(
            reference,
            static_factors,
            static_susceptibility,
            observed_rainfall_risk,
            forecast_rainfall_risk,
            current,
            future,
        )
        metadata = self._metadata(
            grids,
            observed,
            forecast,
            baseline_metadata,
            outputs,
            prediction_horizon_hours,
            future_result.metadata,
        )
        metadata_path = self.output_dir / "analysis_metadata.json"
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        (self.output_dir / "risk_logic.md").write_text(self._risk_logic_text(), encoding="utf-8")
        outputs["metadata"] = str(metadata_path)
        outputs["risk_logic"] = str(self.output_dir / "risk_logic.md")
        return {"metadata": metadata, "output_paths": outputs}

    def _weighted_sum(self, layers: dict[str, np.ndarray], weights: dict[str, float]) -> np.ndarray:
        return self.overlay.analyze(layers, weights)

    def _predict_future_with_random_forest(
        self,
        static_factors: dict[str, np.ndarray],
        static_susceptibility: np.ndarray,
        observed_rainfall_risk: np.ndarray,
        forecast_rainfall_risk: np.ndarray,
        current: np.ndarray,
    ):
        observed_interaction = np.clip(current * observed_rainfall_risk, 0.0, 1.0)
        forecast_interaction = np.clip(current * forecast_rainfall_risk, 0.0, 1.0)
        training_features = {
            **static_factors,
            "static_susceptibility": static_susceptibility,
            "rainfall_risk": observed_rainfall_risk,
            "current_hazard": current,
            "rainfall_change": np.zeros_like(current, dtype="float32"),
            "hazard_rainfall_interaction": observed_interaction,
        }
        prediction_features = {
            **static_factors,
            "static_susceptibility": static_susceptibility,
            "rainfall_risk": forecast_rainfall_risk,
            "current_hazard": current,
            "rainfall_change": (forecast_rainfall_risk - observed_rainfall_risk).astype("float32"),
            "hazard_rainfall_interaction": forecast_interaction,
        }
        predictor = RandomForestRiskPredictor(**self.config.get("random_forest", {}))
        return predictor.predict(training_features, current, prediction_features)

    def _read_baseline_metadata(
        self,
        baseline_sources: dict[str, DataSource] | None,
        reference: RasterGrid,
    ) -> dict[str, Any]:
        if not baseline_sources:
            return {"status": "not_used", "note": "No SEPA baseline map supplied."}
        metadata: dict[str, Any] = {}
        for name, source in baseline_sources.items():
            try:
                grid = source.get_data(reference=reference)
                values = grid.data
                metadata[name] = {
                    "source_type": grid.source_type,
                    "metadata": grid.metadata,
                    "classes_present": sorted(int(v) for v in np.unique(values[np.isfinite(values)])[:20]),
                    "role": "static/baseline reference, calibration or validation; not a dynamic forcing input",
                }
            except Exception as exc:
                metadata[name] = {"status": "unavailable", "error": str(exc)}
        return metadata

    def _write_outputs(
        self,
        reference: RasterGrid,
        static_factors: dict[str, np.ndarray],
        static_susceptibility: np.ndarray,
        observed_rainfall_risk: np.ndarray,
        forecast_rainfall_risk: np.ndarray,
        current: np.ndarray,
        future: np.ndarray,
    ) -> dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        paths: dict[str, Any] = {}
        outputs = {
            "static_susceptibility": static_susceptibility,
            "observed_rainfall_risk": observed_rainfall_risk,
            "forecast_rainfall_risk": forecast_rainfall_risk,
            "current_hazard_index": current,
            "future_hazard_index": future,
        }
        outputs.update({f"{name}_risk": value for name, value in static_factors.items()})
        for name, array in outputs.items():
            path = self.output_dir / f"{name}.tif"
            write_raster(path, RasterGrid(name, array, reference.profile.copy(), "analysis_output"))
            paths[name] = str(path)

        for name, array in {"current_hazard_class": current, "future_hazard_class": future}.items():
            classes = self.classifier.classify(array, self.config["classification"])
            classes[~np.isfinite(array)] = 0
            profile = reference.profile.copy()
            profile.update(dtype="uint8", nodata=0)
            path = self.output_dir / f"{name}.tif"
            write_raster(path, RasterGrid(name, classes, profile, "analysis_output"), dtype="uint8")
            paths[name] = str(path)
        return paths

    def _metadata(
        self,
        grids: dict[str, RasterGrid],
        observed: RasterGrid,
        forecast: RasterGrid,
        baseline_metadata: dict[str, Any],
        outputs: dict[str, Any],
        prediction_horizon_hours: int | None,
        future_model_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "hazard_type": "pluvial",
            "analysis_method": "state_aware_random_forest_prediction_mvp",
            "risk_framework": {
                "static_baseline": [
                    "DEM",
                    "Slope",
                    "Flow Accumulation / proxy",
                    "Imperviousness",
                    "Land Cover",
                    "SEPA Flood Maps as baseline/reference",
                ],
                "observations": [
                    "SEPA latest rainfall observations",
                    "SEPA hourly rainfall history optional",
                    "Radar observations placeholder",
                ],
                "forecast": ["Met Office rainfall forecast", "River forecast placeholder"],
                "outputs": ["Current hazard H(t0)", "Predicted hazard H(t0+delta)"],
            },
            "prediction_horizon_hours": prediction_horizon_hours,
            "static_sources": {name: {"source_type": grid.source_type, "metadata": grid.metadata} for name, grid in grids.items()},
            "observed_rainfall_source": {"source_type": observed.source_type, "metadata": observed.metadata},
            "forecast_rainfall_source": {"source_type": forecast.source_type, "metadata": forecast.metadata},
            "baseline_reference_sources": baseline_metadata,
            "current_hazard_weights": {
                "static_weights": self.config["static_weights"],
                "current_weights": self.config["current_weights"],
            },
            "future_prediction_model": future_model_metadata,
            "classification": self.config["classification"],
            "outputs": outputs,
            "prototype_limitation": (
                "MVP random forest prediction model trained from the current hazard proxy because labelled "
                "historical inundation outcomes are not yet wired in. Radar observations, river "
                "observations/forecasts, drainage capacity, and formal hydrodynamic routing remain "
                "placeholders until data are available."
            ),
        }

    def _risk_logic_text(self) -> str:
        return """# Pluvial Flood Risk Prediction Logic

```text
FLOOD RISK PREDICTION
  STATIC / BASELINE:
    DEM, slope, flow accumulation/proxy, imperviousness, land cover, SEPA flood maps
  DYNAMIC / TEMPORAL:
    OBSERVATIONS: SEPA latest rainfall observations, SEPA hourly rainfall history optional, radar observations placeholder
    FORECAST: Met Office rainfall forecast, river forecast placeholder
```

## Current Hazard

```text
S_static = weighted terrain/runoff susceptibility
R_obs    = observed rainfall risk

H(t0) = 0.70 * S_static + 0.30 * R_obs
```

## Predicted Hazard

```text
Random forest training target:
  y = H(t0)

Training features:
  elevation, slope, flow accumulation, imperviousness, S_static,
  observed rainfall risk, H(t0), rainfall change = 0,
  H(t0) * observed rainfall risk

Prediction features:
  same static and current-state features, but rainfall risk is replaced by
  Met Office forecast rainfall risk, rainfall change is R_forecast - R_obs,
  and the interaction is H(t0) * R_forecast.

H(t0+delta) = RandomForestRegressor(prediction features)
```

SEPA flood maps are static/baseline reference layers for calibration/validation, not dynamic rainfall forcing.
"""
