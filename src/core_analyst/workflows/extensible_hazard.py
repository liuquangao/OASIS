"""Configuration-driven hazard extension using normalized raster factors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from rasterio.enums import Resampling

from core_analyst.data_adapters import AlignedRasterSource
from core_analyst.data_sources import RasterGrid, write_raster
from core_analyst.tools.classification import HazardClassifier
from core_analyst.tools.weighted_overlay import WeightedOverlayAnalyzer


def run_extensible_hazard(
    spec: dict[str, Any],
    factor_paths: dict[str, str],
    output_dir: str | Path,
    area: str,
) -> dict[str, Any]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    first = spec["factors"][0]["name"]
    reference = AlignedRasterSource(first, factor_paths[first], Resampling.bilinear).get_data()
    normalized = {}
    weights = {}
    for factor in spec["factors"]:
        name = factor["name"]
        grid = reference if name == first else AlignedRasterSource(name, factor_paths[name], Resampling.bilinear).get_data(reference)
        values = grid.data.astype("float32")
        finite = np.isfinite(values)
        low, high = np.nanpercentile(values[finite], [2, 98])
        score = np.clip((values - low) / (high - low), 0, 1)
        if factor["direction"] == "lower":
            score = 1 - score
        score[~finite] = np.nan
        normalized[name] = score.astype("float32")
        weights[name] = factor["weight"]
    total = sum(weights.values())
    weights = {name: value / total for name, value in weights.items()}
    index = WeightedOverlayAnalyzer().analyze(normalized, weights)
    classes = HazardClassifier().classify(index, {
        "low": [0.0, spec["medium_threshold"]],
        "medium": [spec["medium_threshold"], spec["high_threshold"]],
        "high": [spec["high_threshold"], 1.0],
    })
    index_path = root / "hazard_index.tif"
    class_path = root / "hazard_class.tif"
    write_raster(index_path, RasterGrid("hazard_index", index, reference.profile.copy(), "extension"))
    class_profile = reference.profile.copy()
    class_profile.update(dtype="uint8", nodata=0)
    write_raster(class_path, RasterGrid("hazard_class", classes, class_profile, "extension"), dtype="uint8")
    metadata = {"analysis_method": "registered_normalized_weighted_overlay", "area": area, "spec": spec, "inputs": factor_paths}
    metadata_path = root / "analysis_metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return {
        "status": "success",
        "hazard_type": spec["hazard_type"],
        "scenario": "custom",
        "summary": metadata,
        "outputs": {"hazard_index": str(index_path), "hazard_class": str(class_path), "metadata": str(metadata_path)},
        "provenance": metadata,
        "warnings": [],
    }
