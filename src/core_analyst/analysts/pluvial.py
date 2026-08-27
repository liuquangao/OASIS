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
from core_analyst.tools.weighted_overlay import WeightedOverlayAnalyzer
from core_analyst.validators.raster_validator import RasterValidator


class PluvialFloodAnalyst:
    required_inputs = ("dem", "slope", "flow_accumulation", "imperviousness", "rainfall")

    def __init__(self, config: dict[str, Any], output_dir: str | Path = "outputs"):
        self.config = config
        self.output_dir = Path(output_dir)
        self.validator = RasterValidator()
        self.classifier = HazardClassifier()
        self.overlay = WeightedOverlayAnalyzer()

    def run(self, sources: dict[str, DataSource]) -> dict[str, Any]:
        missing = sorted(set(self.required_inputs) - set(sources))
        if missing:
            raise ValueError(f"Missing required pluvial input sources: {missing}")

        grids: dict[str, RasterGrid] = {"dem": sources["dem"].get_data()}
        reference = grids["dem"]
        for name in ("slope", "flow_accumulation", "imperviousness"):
            grids[name] = sources[name].get_data(reference=reference)

        static_validation = self.validator.validate_grids(grids)
        static_validation.raise_if_invalid()

        grids["rainfall"] = sources["rainfall"].get_data(reference=reference)
        all_validation = self.validator.validate_grids(grids)
        all_validation.raise_if_invalid()

        factors = {
            "elevation": ElevationRiskAnalyzer().analyze(grids["dem"].data),
            "slope": SlopeRiskAnalyzer().analyze(grids["slope"].data),
            "flow_accumulation": FlowAccumulationAnalyzer().analyze(grids["flow_accumulation"].data),
            "imperviousness": ImperviousnessAnalyzer().analyze(grids["imperviousness"].data),
            "rainfall": RainfallAnalyzer(thresholds=self.config.get("rainfall_thresholds")).analyze(grids["rainfall"].data),
        }

        hazard_index = self.overlay.analyze(factors, self.config["weights"])
        hazard_class = self.classifier.classify(hazard_index, self.config["classification"])

        self.output_dir.mkdir(parents=True, exist_ok=True)
        index_grid = RasterGrid("hazard_index", hazard_index, reference.profile.copy(), "analysis_output")
        class_profile = reference.profile.copy()
        class_profile.update(dtype="uint8", nodata=0)
        class_grid = RasterGrid("hazard_class", hazard_class, class_profile, "analysis_output")

        write_raster(self.output_dir / "hazard_index.tif", index_grid, dtype="float32")
        write_raster(self.output_dir / "hazard_class.tif", class_grid, dtype="uint8")
        factor_paths = self._write_normalized_factors(factors, reference)
        metadata = self._metadata(grids, factor_paths)
        metadata_path = self.output_dir / "analysis_metadata.json"
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        (self.output_dir / "risk_logic.md").write_text(self._risk_logic_text(), encoding="utf-8")

        return {
            "hazard_index": hazard_index,
            "hazard_class": hazard_class,
            "metadata": metadata,
            "output_paths": {
                "hazard_index": str(self.output_dir / "hazard_index.tif"),
                "hazard_class": str(self.output_dir / "hazard_class.tif"),
                "metadata": str(metadata_path),
                "normalized_factors": factor_paths,
            },
        }

    def _write_normalized_factors(self, factors: dict[str, np.ndarray], reference: RasterGrid) -> dict[str, str]:
        paths: dict[str, str] = {}
        factor_dir = self.output_dir / "normalized_factors"
        for name, values in factors.items():
            grid = RasterGrid(name, values, reference.profile.copy(), "normalized_factor")
            path = factor_dir / f"{name}_risk.tif"
            write_raster(path, grid, dtype="float32")
            paths[name] = str(path)
        return paths

    def _metadata(self, grids: dict[str, RasterGrid], factor_paths: dict[str, str]) -> dict[str, Any]:
        return {
            "hazard_type": "pluvial",
            "analysis_method": "weighted_overlay",
            "prototype_limitation": (
                "Demonstration weighted-overlay model only; weights and transformations are not scientifically validated."
            ),
            "inputs": list(self.required_inputs),
            "input_sources": {
                name: {"source_type": grid.source_type, "metadata": grid.metadata}
                for name, grid in grids.items()
            },
            "weights": self.config["weights"],
            "classification": self.config["classification"],
            "outputs": {
                "hazard_index": "hazard_index.tif",
                "hazard_class": "hazard_class.tif",
                "normalized_factors": factor_paths,
            },
        }

    def _risk_logic_text(self) -> str:
        weights = self.config["weights"]
        classification = self.config["classification"]
        return f"""# Pluvial Flood Risk Logic

This output is a static pluvial flood susceptibility analysis unless a real rainfall field is supplied.

## Formula

Hazard Index =

```text
{weights["elevation"]:.2f} * elevation_risk
+ {weights["slope"]:.2f} * slope_risk
+ {weights["flow_accumulation"]:.2f} * flow_accumulation_risk
+ {weights["imperviousness"]:.2f} * runoff_or_imperviousness_risk
+ {weights["rainfall"]:.2f} * rainfall_risk
```

Weights are normalized internally if their total is positive.

## Factor Logic

- `elevation_risk`: lower DTM elevation means higher susceptibility.
- `slope_risk`: flatter terrain means higher surface-water ponding susceptibility.
- `flow_accumulation_risk`: temporary topographic convergence proxy from DTM; replace with Component 1 hydrological flow accumulation.
- `runoff_or_imperviousness_risk`: proxy from built-up areas, greenspace, and UKCEH land-cover runoff scores.
- `rainfall_risk`: weighted as `{weights["rainfall"]:.2f}` in the current config, so it does not affect the static susceptibility result unless enabled.

## Classification

- Low: `{classification["low"][0]}` to `{classification["low"][1]}`
- Medium: `{classification["medium"][0]}` to `{classification["medium"][1]}`
- High: `{classification["high"][0]}` to `{classification["high"][1]}`

## Current Caveats

- This is not a calibrated hydrological model.
- SEPA river/coastal flood maps are reference layers, not direct pluvial inputs.
- The flow factor is a proxy until formal sink-filled flow accumulation is available.
- Thresholds and weights need calibration against observed surface-water flooding or expert judgement.
"""
