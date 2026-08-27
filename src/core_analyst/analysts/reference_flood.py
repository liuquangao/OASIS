from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from core_analyst.data_sources import DataSource, RasterGrid, write_raster
from core_analyst.tools.classification import HazardClassifier


class ReferenceFloodAnalyst:
    """Reference-map analyst for fluvial/coastal MVP workflows.

    This analyst uses official SEPA mapped flood extents as the base hazard
    where dynamic process inputs are not yet available.
    """

    def __init__(
        self,
        hazard_type: str,
        scenario: str,
        config: dict[str, Any],
        output_dir: str | Path,
    ):
        self.hazard_type = hazard_type
        self.scenario = scenario
        self.config = config
        self.output_dir = Path(output_dir)

    def run(self, sources: dict[str, DataSource]) -> dict[str, Any]:
        required = {"dem", "reference_flood"}
        missing = sorted(required - set(sources))
        if missing:
            raise ValueError(f"Missing required reference-flood inputs: {missing}")

        dem = sources["dem"].get_data()
        reference = sources["reference_flood"].get_data(reference=dem)
        hazard_index = self._reference_to_hazard_index(reference.data, np.isfinite(dem.data))
        hazard_class = HazardClassifier().classify(hazard_index, self.config["classification"])
        hazard_class[~np.isfinite(hazard_index)] = 0

        self.output_dir.mkdir(parents=True, exist_ok=True)
        index_profile = dem.profile.copy()
        index_profile.update(dtype="float32", nodata=np.nan)
        class_profile = dem.profile.copy()
        class_profile.update(dtype="uint8", nodata=0)

        index_grid = RasterGrid("hazard_index", hazard_index, index_profile, "analysis_output")
        class_grid = RasterGrid("hazard_class", hazard_class, class_profile, "analysis_output")
        write_raster(self.output_dir / "hazard_index.tif", index_grid)
        write_raster(self.output_dir / "hazard_class.tif", class_grid, dtype="uint8")

        metadata = self._metadata(reference)
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
            },
        }

    def _reference_to_hazard_index(self, values: np.ndarray, valid_mask: np.ndarray) -> np.ndarray:
        hazard = np.zeros(values.shape, dtype="float32")
        hazard[values == 1] = 0.40
        hazard[values == 2] = 0.70
        hazard[values == 3] = 1.00
        hazard[values == 999] = np.nan
        hazard[~valid_mask] = np.nan
        return hazard

    def _metadata(self, reference: RasterGrid) -> dict[str, Any]:
        return {
            "hazard_type": self.hazard_type,
            "scenario": self.scenario,
            "analysis_method": "sepa_reference_map_reclassification",
            "reference_source": reference.metadata,
            "classification": self.config["classification"],
            "value_mapping": {
                "SEPA class 1": 0.40,
                "SEPA class 2": 0.70,
                "SEPA class 3": 1.00,
                "SEPA class 999": "NoData/excluded",
                "outside mapped flood extent": 0.0,
            },
            "prototype_limitation": (
                "This is a reference-based MVP output. It does not use real-time river level, "
                "river discharge, tide, surge, wave, defence, or hydraulic model data."
            ),
        }

    def _risk_logic_text(self) -> str:
        return f"""# {self.hazard_type.title()} {self.scenario.title()} Risk Logic

This output is currently SEPA reference-map based.

## Mapping

```text
SEPA flood depth/class 1 -> hazard_index 0.40
SEPA flood depth/class 2 -> hazard_index 0.70
SEPA flood depth/class 3 -> hazard_index 1.00
SEPA class 999           -> NoData / excluded
Outside mapped extent    -> 0.00 within the valid DTM mask
```

## Limitation

This is not a dynamic {self.hazard_type} forecast. Dynamic modelling would require additional data such as river level/discharge for fluvial flooding or tide/surge/wave/sea-level scenario data for coastal flooding.
"""
