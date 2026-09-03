"""Create compact, paper-facing evidence from one confirmed assessment job."""

from __future__ import annotations

import csv
from datetime import datetime
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from hydromind.models.analysis import AnalysisRunSummary
from hydromind.models.assessment import AssessmentJob


def generate_competition_package(
    *,
    output_root: Path,
    job: AssessmentJob,
    result: AnalysisRunSummary,
    quality: dict[str, Any],
) -> dict[str, str]:
    """Index deterministic evidence and generate the two small missing artifacts."""

    root = output_root / "jobs" / job.job_id / "experiment_package"
    root.mkdir(parents=True, exist_ok=True)
    stored = json.loads(
        (output_root / "runs" / result.run_id / "result.json").read_text(encoding="utf-8")
    )
    outputs = stored.get("outputs", {})

    top_path = root / "top_10_data_zones.csv"
    top_rows = result.summary.get("top_areas", [])
    fields = [
        "rank",
        "id",
        "name",
        "priority_score",
        "rank_change",
        "hazard_score",
        "exposure_score",
        "vulnerability_score",
    ]
    with top_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(top_rows)

    timeline_path = root / "agent_execution_timeline.png"
    _plot_timeline(job, timeline_path)

    paper_summary = {
        "run_id": result.run_id,
        "analysis_type": result.analysis_type,
        "quality_status": quality.get("status"),
        "autonomy": {
            "plan_id": job.plan_id,
            "confirmed_preferences": job.preferences.model_dump(mode="json"),
            "execution_steps": [step.model_dump(mode="json") for step in job.steps],
        },
        "spatial_results": result.summary,
        "robustness": {
            "quality_checks": quality.get("checks", []),
            "warnings": job.warnings,
        },
        "social_good": {
            "priority_definition": (
                "Relative intervention ranking under explicit stakeholder weights; "
                "not flood probability or an official warning."
            ),
            "weights": job.preferences.weights.model_dump(),
            "simd_included": job.preferences.include_simd,
        },
        "reflection": {
            "geographic_scope": "Glasgow only",
            "historical_validation_scope": (
                "Forecast-input and decision-stability validation; no inundation truth."
            ),
        },
    }
    paper_path = root / "paper_results_summary.json"
    paper_path.write_text(json.dumps(paper_summary, indent=2), encoding="utf-8")

    manifest = {
        "run_id": result.run_id,
        "generated_at": datetime.now().astimezone().isoformat(),
        "generated": {
            "top_10_table": str(top_path),
            "execution_timeline": str(timeline_path),
            "paper_results_summary": str(paper_path),
        },
        "indexed_run_outputs": {
            key: value
            for key, value in outputs.items()
            if key in {
                "four_panel_map",
                "sensitivity_figure",
                "sensitivity",
                "priority_scenarios",
                "hazard_by_data_zone",
                "exposure_by_data_zone",
                "vulnerability_by_data_zone",
                "priority_by_data_zone",
                "historical_validation_figure",
                "historical_validation_summary",
                "forecast_hazard_class",
                "observed_hazard_class",
                "baseline_hazard_class",
            }
        },
    }
    manifest_path = root / "experiment_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        "experiment_manifest": str(manifest_path),
        "paper_results_summary": str(paper_path),
        "top_10_table": str(top_path),
        "execution_timeline_figure": str(timeline_path),
    }


def _plot_timeline(job: AssessmentJob, path: Path) -> None:
    steps = [step for step in job.steps if step.started_at or step.finished_at]
    fig, axis = plt.subplots(figsize=(8, max(2.4, len(job.steps) * 0.45)))
    if not steps:
        axis.text(0.5, 0.5, "No executed steps", ha="center", va="center")
        axis.axis("off")
    else:
        origin = min((step.started_at or step.finished_at) for step in steps)
        colors = {
            "completed": "#0f766e",
            "warning": "#d97706",
            "failed": "#b91c1c",
            "running": "#2563eb",
        }
        for index, step in enumerate(steps):
            start = step.started_at or step.finished_at
            finish = step.finished_at or start
            offset = (start - origin).total_seconds()
            duration = max((finish - start).total_seconds(), 0.05)
            axis.barh(index, duration, left=offset, color=colors.get(step.status, "#94a3b8"))
        axis.set_yticks(range(len(steps)), [step.label for step in steps])
        axis.invert_yaxis()
        axis.set_xlabel("Seconds after confirmed execution")
        axis.grid(axis="x", alpha=0.2)
    axis.set_title("HydroMind Agent execution and validation timeline", loc="left", fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
