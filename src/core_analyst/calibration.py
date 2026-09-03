from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from rasterio.enums import Resampling

from core_analyst.analysts.pluvial import PluvialFloodAnalyst
from core_analyst.data_adapters import AlignedRasterSource
from core_analyst.data_sources import DataSource, RasterGrid, write_raster
from core_analyst.tools.classification import HazardClassifier
from core_analyst.tools.factor_analyzers import (
    ElevationRiskAnalyzer,
    FlowAccumulationAnalyzer,
    ImperviousnessAnalyzer,
    RainfallAnalyzer,
    SlopeRiskAnalyzer,
)
from core_analyst.utils.config import load_config
from core_analyst.workflows.hydromind_real_data import build_hydromind_input_sources, gdb_uri


FACTOR_NAMES = ["elevation", "slope", "flow_accumulation", "imperviousness", "rainfall"]
STATIC_CALIBRATION_FACTORS = ["elevation", "slope", "flow_accumulation", "imperviousness"]


@dataclass
class CalibrationResult:
    weights: dict[str, float]
    metrics: dict[str, Any]


def compute_factor_grids(sources: dict[str, DataSource]) -> tuple[dict[str, np.ndarray], RasterGrid]:
    grids: dict[str, RasterGrid] = {"dem": sources["dem"].get_data()}
    reference = grids["dem"]
    for name in ("slope", "flow_accumulation", "imperviousness", "rainfall"):
        grids[name] = sources[name].get_data(reference=reference)

    factors = {
        "elevation": ElevationRiskAnalyzer().analyze(grids["dem"].data),
        "slope": SlopeRiskAnalyzer().analyze(grids["slope"].data),
        "flow_accumulation": FlowAccumulationAnalyzer().analyze(grids["flow_accumulation"].data),
        "imperviousness": ImperviousnessAnalyzer().analyze(grids["imperviousness"].data),
        "rainfall": RainfallAnalyzer().analyze(grids["rainfall"].data),
    }
    return factors, reference


def load_sepa_reference_labels(
    input_dir: str | Path,
    reference: RasterGrid,
    layer_name: str = "SEPA_Coastal_and_River_Medium_Flood_5m_res",
) -> np.ndarray:
    input_dir = Path(input_dir)
    gdb_path = input_dir / "HYDROMIND_raster.gdb" / "HYDROMIND_raster.gdb"
    if gdb_path.exists():
        source_path = gdb_uri(gdb_path, layer_name)
    else:
        tif_name = {
            "SEPA_Coastal_and_River_High_Flood_5m_res": "SEPA_Coastal_and_River_Hi.tif",
            "SEPA_Coastal_and_River_Medium_Flood_5m_res": "SEPA_Coastal_and_River_Me.tif",
            "SEPA_Coastal_and_River_Low_Flood_5m_res": "SEPA_Coastal_and_River_Lo.tif",
        }.get(layer_name, "SEPA_Coastal_and_River_Me.tif")
        source_path = input_dir / "HYDROMIND_Rasters" / "HYDROMIND_Rasters" / tif_name

    grid = AlignedRasterSource(
        "sepa_reference",
        source_path,
        resampling=Resampling.nearest,
        fill_value=0.0,
    ).get_data(reference=reference)
    return grid.data


def make_binary_labels(reference_values: np.ndarray, valid_mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    labels = np.zeros(reference_values.shape, dtype="uint8")
    labels[np.isin(reference_values, [1, 2, 3])] = 1
    calibration_mask = valid_mask & (reference_values != 999) & np.isfinite(reference_values)
    return labels, calibration_mask


def fit_constrained_weights(
    factors: dict[str, np.ndarray],
    labels: np.ndarray,
    mask: np.ndarray,
    factor_names: list[str] = STATIC_CALIBRATION_FACTORS,
    n_candidates: int = 2000,
    sample_size: int = 120000,
    seed: int = 42,
) -> CalibrationResult:
    rng = np.random.default_rng(seed)
    positive_idx = np.flatnonzero(mask.ravel() & (labels.ravel() == 1))
    negative_idx = np.flatnonzero(mask.ravel() & (labels.ravel() == 0))
    if len(positive_idx) == 0 or len(negative_idx) == 0:
        raise ValueError("SEPA calibration requires both positive and negative reference pixels.")

    half = sample_size // 2
    pos_sample = rng.choice(positive_idx, size=min(half, len(positive_idx)), replace=False)
    neg_sample = rng.choice(negative_idx, size=min(half, len(negative_idx)), replace=False)
    sample_idx = np.concatenate([pos_sample, neg_sample])
    rng.shuffle(sample_idx)

    feature_stack = np.vstack([factors[name].ravel()[sample_idx] for name in factor_names]).T
    y = labels.ravel()[sample_idx].astype("float32")
    finite = np.isfinite(feature_stack).all(axis=1)
    feature_stack = feature_stack[finite]
    y = y[finite]

    candidates = rng.dirichlet(np.ones(len(factor_names)), size=n_candidates)
    best_loss = np.inf
    best_weights = candidates[0]
    for start in range(0, len(candidates), 200):
        chunk = candidates[start:start + 200]
        scores = feature_stack @ chunk.T
        losses = np.mean((scores - y[:, None]) ** 2, axis=0)
        idx = int(np.argmin(losses))
        if float(losses[idx]) < best_loss:
            best_loss = float(losses[idx])
            best_weights = chunk[idx]

    full_weights = {name: 0.0 for name in FACTOR_NAMES}
    for name, value in zip(factor_names, best_weights):
        full_weights[name] = float(value)

    full_scores = weighted_score(factors, full_weights)
    metrics = evaluate_against_labels(full_scores, labels, mask)
    metrics["fit_objective"] = "minimize_brier_score_balanced_sample"
    metrics["sample_brier_score"] = float(best_loss)
    metrics["calibration_reference"] = "SEPA flood map used as proxy/reference labels, not true pluvial ground truth."
    return CalibrationResult(weights=full_weights, metrics=metrics)


def weighted_score(factors: dict[str, np.ndarray], weights: dict[str, float]) -> np.ndarray:
    total = sum(float(weights.get(name, 0.0)) for name in FACTOR_NAMES)
    if total <= 0:
        raise ValueError("At least one factor weight must be positive.")
    score = np.zeros_like(next(iter(factors.values())), dtype="float32")
    invalid = np.zeros_like(score, dtype=bool)
    for name in FACTOR_NAMES:
        factor = factors[name]
        invalid |= ~np.isfinite(factor)
        score += factor.astype("float32") * (float(weights.get(name, 0.0)) / total)
    score[invalid] = np.nan
    return np.clip(score, 0.0, 1.0).astype("float32")


def evaluate_against_labels(scores: np.ndarray, labels: np.ndarray, mask: np.ndarray) -> dict[str, Any]:
    valid = mask & np.isfinite(scores)
    y = labels[valid].astype("uint8")
    s = scores[valid].astype("float32")
    pred = s >= 0.80
    positive = y == 1
    negative = y == 0
    tp = int(np.sum(pred & positive))
    fp = int(np.sum(pred & negative))
    fn = int(np.sum(~pred & positive))
    tn = int(np.sum(~pred & negative))
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    specificity = tn / max(tn + fp, 1)
    return {
        "auc": float(auc_score(y.astype("float32"), s)),
        "threshold_for_high": 0.80,
        "precision_high": float(precision),
        "recall_high": float(recall),
        "specificity_high": float(specificity),
        "confusion_matrix": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "positive_pixels": int(np.sum(positive)),
        "negative_pixels": int(np.sum(negative)),
    }


def auc_score(y_true: np.ndarray, scores: np.ndarray) -> float:
    y_true = y_true.astype("uint8")
    positive = y_true == 1
    negative = y_true == 0
    n_pos = int(np.sum(positive))
    n_neg = int(np.sum(negative))
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty_like(order, dtype="float64")
    ranks[order] = np.arange(1, len(scores) + 1)
    rank_sum_pos = float(np.sum(ranks[positive]))
    return (rank_sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def compare_expert_and_calibrated(
    input_dir: str | Path = "Input",
    output_dir: str | Path = "outputs/sepa_weight_comparison",
    expert_config_path: str | Path = "config/hydromind_static_config.yaml",
    reference_layer: str = "SEPA_Coastal_and_River_Medium_Flood_5m_res",
    hybrid_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    expert_config = load_config(expert_config_path)
    sources = build_hydromind_input_sources(input_dir, rainfall_source="mock")
    factors, reference_grid = compute_factor_grids(sources)
    reference_values = load_sepa_reference_labels(input_dir, reference_grid, layer_name=reference_layer)
    valid_mask = np.isfinite(reference_grid.data)
    labels, calibration_mask = make_binary_labels(reference_values, valid_mask)

    expert_score = weighted_score(factors, expert_config["weights"])
    expert_metrics = evaluate_against_labels(expert_score, labels, calibration_mask)

    calibration = fit_constrained_weights(factors, labels, calibration_mask)
    calibrated_config = json.loads(json.dumps(expert_config))
    calibrated_config["weights"] = calibration.weights
    hybrid_config = json.loads(json.dumps(expert_config))
    hybrid_config["weights"] = hybrid_weights or {
        "elevation": 0.35,
        "slope": 0.15,
        "flow_accumulation": 0.20,
        "imperviousness": 0.20,
        "rainfall": 0.10,
    }

    expert_outputs = write_score_outputs(
        expert_score,
        reference_grid,
        expert_config,
        output_dir / "expert",
        "expert_weighted_overlay",
        expert_metrics,
    )
    calibrated_score = weighted_score(factors, calibration.weights)
    calibrated_outputs = write_score_outputs(
        calibrated_score,
        reference_grid,
        calibrated_config,
        output_dir / "sepa_calibrated",
        "sepa_calibrated_weighted_overlay",
        calibration.metrics,
    )
    hybrid_score = weighted_score(factors, hybrid_config["weights"])
    hybrid_metrics = evaluate_against_labels(hybrid_score, labels, calibration_mask)
    hybrid_outputs = write_score_outputs(
        hybrid_score,
        reference_grid,
        hybrid_config,
        output_dir / "hybrid",
        "hybrid_reference_informed_weighted_overlay",
        hybrid_metrics,
    )

    report = {
        "reference_layer": reference_layer,
        "important_limitation": (
            "SEPA flood maps are used as proxy/reference labels. They are not pure pluvial flood ground truth, "
            "so fitted weights represent agreement with SEPA mapped flood extents rather than validated pluvial physics."
        ),
        "expert": {"weights": expert_config["weights"], "metrics": expert_metrics, "outputs": expert_outputs},
        "sepa_calibrated": {
            "weights": calibration.weights,
            "metrics": calibration.metrics,
            "outputs": calibrated_outputs,
        },
        "hybrid": {
            "weights": hybrid_config["weights"],
            "metrics": hybrid_metrics,
            "outputs": hybrid_outputs,
        },
    }
    (output_dir / "comparison_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output_dir / "comparison_report.md").write_text(format_comparison_report(report), encoding="utf-8")
    return report


def write_score_outputs(
    score: np.ndarray,
    reference_grid: RasterGrid,
    config: dict[str, Any],
    output_dir: Path,
    method_name: str,
    metrics: dict[str, Any],
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    class_values = HazardClassifier().classify(score, config["classification"])
    class_values[~np.isfinite(score)] = 0

    index_profile = reference_grid.profile.copy()
    index_profile.update(dtype="float32", nodata=np.nan)
    class_profile = reference_grid.profile.copy()
    class_profile.update(dtype="uint8", nodata=0)
    write_raster(output_dir / "hazard_index.tif", RasterGrid("hazard_index", score, index_profile, "analysis_output"))
    write_raster(output_dir / "hazard_class.tif", RasterGrid("hazard_class", class_values, class_profile, "analysis_output"), dtype="uint8")

    metadata = {
        "hazard_type": "pluvial_static_susceptibility",
        "analysis_method": method_name,
        "weights": config["weights"],
        "classification": config["classification"],
        "validation_against_sepa_reference": metrics,
        "prototype_limitation": (
            "SEPA reference maps are proxy labels, not pure pluvial flood ground truth. "
            "This output should be interpreted as calibration/diagnostic evidence."
        ),
    }
    (output_dir / "analysis_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return {
        "hazard_index": str(output_dir / "hazard_index.tif"),
        "hazard_class": str(output_dir / "hazard_class.tif"),
        "metadata": str(output_dir / "analysis_metadata.json"),
    }


def format_comparison_report(report: dict[str, Any]) -> str:
    expert = report["expert"]
    calibrated = report["sepa_calibrated"]
    hybrid = report["hybrid"]
    return f"""# SEPA Weight Calibration Comparison

Reference layer: `{report["reference_layer"]}`

Important limitation: {report["important_limitation"]}

## Expert Weights

```json
{json.dumps(expert["weights"], indent=2)}
```

Metrics:

```json
{json.dumps(expert["metrics"], indent=2)}
```

## SEPA-Calibrated Weights

```json
{json.dumps(calibrated["weights"], indent=2)}
```

Metrics:

```json
{json.dumps(calibrated["metrics"], indent=2)}
```

## Hybrid Weights

```json
{json.dumps(hybrid["weights"], indent=2)}
```

Metrics:

```json
{json.dumps(hybrid["metrics"], indent=2)}
```

## Interpretation

The calibrated version searches for non-negative weights that sum to one and minimize binary prediction error against the selected SEPA reference flood map. The hybrid version keeps a stronger pluvial/urban-runoff interpretation while borrowing evidence from the SEPA comparison. Both should be interpreted as transparent calibration baselines, not as deep neural networks and not as proof of pluvial flood causality.
"""
