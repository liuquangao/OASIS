"""Create and persist human-confirmed assessment plans."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re

from hydromind.models.analysis import DataReadinessSummary, PriorityWeights
from hydromind.models.assessment import (
    AnalysisIntent,
    AssessmentPlan,
    AssessmentPreferences,
    ExecutionStep,
)


_PLAN_ID = re.compile(r"^[a-f0-9]{12}$")


def create_assessment_plan(
    *,
    question: str,
    intent: AnalysisIntent,
    readiness: DataReadinessSummary,
    output_dir: Path,
    reusable_run_id: str | None = None,
) -> AssessmentPlan:
    historical = intent.category == "historical_validation"
    preferences = AssessmentPreferences(
        scenario="historical" if historical else intent.scenario,
        use_live_data=not historical,
        forecast_horizon_hours=intent.forecast_horizon_hours,
        hazard_threshold=intent.hazard_threshold,
        priority_scenario=intent.priority_scenario,
        weights=_preset_weights(intent.priority_scenario),
        include_simd=True,
        historical_issue_time=(
            datetime(2023, 10, 6, 6, tzinfo=UTC) if historical else None
        ),
    )
    steps = _historical_steps() if historical else _assessment_steps()
    plan = AssessmentPlan(
        question=question,
        intent=intent,
        preferences=preferences,
        required_datasets=[item.dataset for item in readiness.available + readiness.incomplete],
        missing_datasets=[item.dataset for item in readiness.incomplete],
        reusable_run_id=reusable_run_id,
        steps=steps,
    )
    root = output_dir / "plans"
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{plan.plan_id}.json").write_text(
        plan.model_dump_json(indent=2), encoding="utf-8"
    )
    return plan


def load_assessment_plan(output_dir: Path, plan_id: str) -> AssessmentPlan:
    if not _PLAN_ID.fullmatch(plan_id):
        raise ValueError("Invalid assessment plan id")
    path = output_dir / "plans" / f"{plan_id}.json"
    if not path.is_file():
        raise ValueError(f"Unknown assessment plan id: {plan_id}")
    return AssessmentPlan.model_validate_json(path.read_text(encoding="utf-8"))


def _preset_weights(scenario: str) -> PriorityWeights:
    values = {
        "life_safety": (0.45, 0.40, 0.15),
        "social_equity": (0.25, 0.25, 0.50),
        "economic_protection": (0.40, 0.45, 0.15),
    }[scenario]
    return PriorityWeights(
        hazard=values[0], exposure=values[1], vulnerability=values[2]
    )


def _assessment_steps() -> list[ExecutionStep]:
    return [
        ExecutionStep(id="data_readiness", label="Data readiness"),
        ExecutionStep(id="hazard", label="Multi-hazard analysis"),
        ExecutionStep(id="exposure", label="Exposure aggregation"),
        ExecutionStep(id="vulnerability", label="Social vulnerability"),
        ExecutionStep(id="priority", label="Priority ranking"),
        ExecutionStep(id="validation", label="Quality validation"),
        ExecutionStep(id="publish", label="Publish decision layers"),
    ]


def _historical_steps() -> list[ExecutionStep]:
    return [
        ExecutionStep(id="data_readiness", label="Historical data readiness"),
        ExecutionStep(id="hazard", label="Archived UKV forecast"),
        ExecutionStep(id="exposure", label="Observation reconstruction"),
        ExecutionStep(id="vulnerability", label="Data Zone assessment"),
        ExecutionStep(id="priority", label="Ranking comparison"),
        ExecutionStep(id="validation", label="No-leakage validation"),
        ExecutionStep(id="publish", label="Publish validation layers"),
    ]
