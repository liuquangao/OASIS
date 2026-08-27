from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from core_analyst.data_sources import DataSource, RasterGrid, write_raster
from core_analyst.tools.classification import HazardClassifier
from core_analyst.tools.weighted_overlay import WeightedOverlayAnalyzer


class TemporalReferenceFloodAnalyst:
    """State-aware fluvial/coastal MVP based on SEPA baseline maps plus available forcings."""

    def __init__(
        self,
        hazard_type: str,
        config: dict[str, Any],
        output_dir: str | Path,
    ):
        self.hazard_type = hazard_type
        self.config = config
        self.output_dir = Path(output_dir)
        self.classifier = HazardClassifier()
        self.overlay = WeightedOverlayAnalyzer()

    def run(
        self,
        dem_source: DataSource,
        baseline_high_source: DataSource,
        baseline_medium_source: DataSource,
        baseline_low_source: DataSource,
        static_forcings: dict[str, DataSource] | None,
        current_forcings: dict[str, DataSource],
        future_forcings: dict[str, DataSource],
        unavailable: dict[str, str] | None = None,
        dynamic_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        dem = dem_source.get_data()
        valid_mask = np.isfinite(dem.data)
        baseline_high = self._reference_to_index(baseline_high_source.get_data(reference=dem).data, valid_mask)
        baseline_medium = self._reference_to_index(baseline_medium_source.get_data(reference=dem).data, valid_mask)
        baseline_low = self._reference_to_index(baseline_low_source.get_data(reference=dem).data, valid_mask)

        static_layers, static_meta = self._read_forcings(static_forcings or {}, dem)
        current_layers, current_meta = self._read_forcings(current_forcings, dem)
        future_layers, future_meta = self._read_forcings(future_forcings, dem)

        current_hazard = self._combine(
            {
                "baseline_high": baseline_high,
                "baseline_medium": baseline_medium,
                **static_layers,
                **current_layers,
            },
            self.config["current_weights"],
        )
        future_hazard = self._combine(
            {
                "baseline_low": baseline_low,
                "baseline_medium": baseline_medium,
                "current_state": current_hazard,
                **static_layers,
                **future_layers,
            },
            self.config["future_weights"],
        )

        outputs = self._write_outputs(dem, current_hazard, future_hazard)
        metadata = {
            "hazard_type": self.hazard_type,
            "analysis_method": "baseline_plus_temporal_forcing_mvp",
            "static_baseline": {
                "baseline_high": "SEPA high-likelihood flood map, frequent baseline.",
                "baseline_medium": "SEPA medium-likelihood flood map, central planning/reference baseline.",
                "baseline_low": "SEPA low-likelihood flood map, rare/extreme envelope.",
            },
            "static_forcings": static_meta,
            "current_forcings": current_meta,
            "future_forcings": future_meta,
            "dynamic_evidence": dynamic_evidence or {},
            "unavailable_inputs": unavailable or {},
            "weights": {
                "current_weights": self.config["current_weights"],
                "future_weights": self.config["future_weights"],
            },
            "classification": self.config["classification"],
            "outputs": outputs,
            "prototype_limitation": (
                "MVP only. Station-specific river/tide thresholds, hydraulic forecasts, surge forecasts, "
                "defence information, and hydrodynamic routing are not yet available."
            ),
        }
        metadata_path = self.output_dir / "analysis_metadata.json"
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        (self.output_dir / "risk_logic.md").write_text(self._risk_logic_text(), encoding="utf-8")
        outputs["metadata"] = str(metadata_path)
        return {"metadata": metadata, "output_paths": outputs}

    def _read_forcings(self, sources: dict[str, DataSource], reference: RasterGrid) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
        layers: dict[str, np.ndarray] = {}
        metadata: dict[str, Any] = {}
        for name, source in sources.items():
            try:
                grid = source.get_data(reference=reference)
                layers[name] = grid.data
                metadata[name] = {"status": "available", "source_type": grid.source_type, "metadata": grid.metadata}
            except Exception as exc:
                metadata[name] = {"status": "unavailable", "error": str(exc)}
        return layers, metadata

    def _reference_to_index(self, values: np.ndarray, valid_mask: np.ndarray) -> np.ndarray:
        hazard = np.zeros(values.shape, dtype="float32")
        hazard[values == 1] = 0.40
        hazard[values == 2] = 0.70
        hazard[values == 3] = 1.00
        hazard[values == 999] = np.nan
        hazard[~valid_mask] = np.nan
        return hazard

    def _combine(self, layers: dict[str, np.ndarray], weights: dict[str, float]) -> np.ndarray:
        return self.overlay.analyze(layers, weights, require_all_weights=False)

    def _write_outputs(self, reference: RasterGrid, current: np.ndarray, future: np.ndarray) -> dict[str, str]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        outputs = {}
        for name, array in {"current_hazard_index": current, "future_hazard_index": future}.items():
            path = self.output_dir / f"{name}.tif"
            write_raster(path, RasterGrid(name, array, reference.profile.copy(), "analysis_output"))
            outputs[name] = str(path)
        for name, array in {"current_hazard_class": current, "future_hazard_class": future}.items():
            classes = self.classifier.classify(array, self.config["classification"])
            classes[~np.isfinite(array)] = 0
            profile = reference.profile.copy()
            profile.update(dtype="uint8", nodata=0)
            path = self.output_dir / f"{name}.tif"
            write_raster(path, RasterGrid(name, classes, profile, "analysis_output"), dtype="uint8")
            outputs[name] = str(path)
        return outputs

    def _risk_logic_text(self) -> str:
        return f"""# {self.hazard_type.title()} Hazard Prediction MVP

```text
Current hazard:
  SEPA baseline map + available current forcing observations

Future hazard:
  SEPA future/potential baseline + current state + available forecast forcing
```

This follows the common flood hazard prediction framework, but unavailable dynamic inputs are explicitly left blank in metadata rather than fabricated.
"""
