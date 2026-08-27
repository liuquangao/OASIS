"""Combine aligned hazard outputs without double-counting overlaps."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import rasterio


HAZARD_FLAG_VALUES = {"pluvial": 1, "fluvial": 2, "coastal": 4}


def combine_hazard_maps(
    hazard_results: dict[str, dict[str, Any]],
    output_dir: str | Path,
    *,
    scenario: str,
    exposure_threshold: int = 2,
) -> dict[str, Any]:
    """Create pixelwise-maximum combined hazard and source-flag rasters."""

    required = tuple(HAZARD_FLAG_VALUES)
    if set(hazard_results) != set(required):
        raise ValueError(f"hazard_results must contain exactly {required}")
    if scenario not in {"current", "future"}:
        raise ValueError("scenario must be current or future")
    if exposure_threshold not in {1, 2, 3}:
        raise ValueError("exposure_threshold must be 1, 2, or 3")

    index_arrays: list[np.ndarray] = []
    class_arrays: list[np.ndarray] = []
    index_profile: dict[str, Any] | None = None
    class_profile: dict[str, Any] | None = None
    reference_signature: tuple[Any, ...] | None = None
    for hazard_type in required:
        result = hazard_results[hazard_type]
        if result.get("scenario") != scenario:
            raise ValueError(f"{hazard_type} run does not match scenario {scenario}")
        outputs = result.get("outputs", {})
        with rasterio.open(outputs["hazard_index"]) as dataset:
            signature = (dataset.crs, dataset.transform, dataset.width, dataset.height)
            reference_signature = reference_signature or signature
            if signature != reference_signature:
                raise ValueError("Hazard index rasters are not spatially aligned")
            index_arrays.append(dataset.read(1).astype("float32"))
            index_profile = index_profile or dataset.profile.copy()
        with rasterio.open(outputs["hazard_class"]) as dataset:
            signature = (dataset.crs, dataset.transform, dataset.width, dataset.height)
            if signature != reference_signature:
                raise ValueError("Hazard class rasters are not spatially aligned")
            class_arrays.append(dataset.read(1).astype("uint8"))
            class_profile = class_profile or dataset.profile.copy()

    index_stack = np.stack(index_arrays)
    finite_any = np.isfinite(index_stack).any(axis=0)
    combined_index = np.max(
        np.where(np.isfinite(index_stack), index_stack, -np.inf), axis=0
    ).astype("float32")
    combined_index[~finite_any] = np.nan
    class_stack = np.stack(class_arrays)
    combined_class = np.max(class_stack, axis=0).astype("uint8")
    source_flags = np.zeros(combined_class.shape, dtype="uint8")
    for hazard_type, class_array in zip(required, class_arrays):
        source_flags = np.where(
            class_array >= exposure_threshold,
            source_flags | HAZARD_FLAG_VALUES[hazard_type],
            source_flags,
        ).astype("uint8")

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    index_path = root / "hazard_index.tif"
    class_path = root / "hazard_class.tif"
    flags_path = root / "hazard_source_flags.tif"
    metadata_path = root / "analysis_metadata.json"
    assert index_profile is not None and class_profile is not None
    index_profile.update(driver="GTiff", dtype="float32", count=1, nodata=np.nan)
    class_profile.update(driver="GTiff", dtype="uint8", count=1, nodata=0)
    with rasterio.open(index_path, "w", **index_profile) as dataset:
        dataset.write(combined_index, 1)
    with rasterio.open(class_path, "w", **class_profile) as dataset:
        dataset.write(combined_class, 1)
    with rasterio.open(flags_path, "w", **class_profile) as dataset:
        dataset.write(source_flags, 1)

    outputs = {
        "hazard_index": str(index_path),
        "hazard_class": str(class_path),
        "hazard_source_flags": str(flags_path),
        "metadata": str(metadata_path),
    }
    metadata = {
        "analysis_method": "combined_any_hazard_maximum",
        "scenario": scenario,
        "hazard_types": list(required),
        "exposure_threshold": exposure_threshold,
        "source_flag_values": HAZARD_FLAG_VALUES,
        "double_counting_policy": (
            "Pixelwise maximum is used instead of summation; source flags retain "
            "which hazards meet the exposure threshold."
        ),
        "inputs": {
            hazard_type: hazard_results[hazard_type].get("run_id")
            for hazard_type in required
        },
        "outputs": outputs,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return {
        "status": "success",
        "hazard_type": "combined",
        "scenario": scenario,
        "summary": metadata,
        "metadata": metadata,
        "outputs": outputs,
        "provenance": {"upstream_runs": metadata["inputs"]},
        "warnings": [
            {
                "code": "combined_hazard_proxy",
                "message": "The combined layer is a maximum-of-inputs analytical proxy, not an operational forecast.",
            }
        ],
    }
