"""Typed plans and audit records for human-confirmed flood assessments."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from hydromind.models.analysis import PriorityWeights


IntentCategory = Literal[
    "point_risk",
    "rainfall_water",
    "route_nearby",
    "city_hazard",
    "integrated_risk",
    "historical_validation",
    "setup_help",
]
AssessmentStatus = Literal[
    "planned",
    "awaiting_confirmation",
    "queued",
    "running",
    "validating",
    "completed",
    "partial",
    "failed",
]
StepStatus = Literal["pending", "running", "completed", "warning", "failed", "skipped"]


def _social_equity_weights() -> PriorityWeights:
    return PriorityWeights(hazard=0.25, exposure=0.25, vulnerability=0.50)


class AnalysisIntent(BaseModel):
    """Small model-selected description of the requested spatial task."""

    category: IntentCategory
    area: str = "Glasgow"
    scenario: Literal["current", "future", "historical"] = "future"
    forecast_horizon_hours: int = Field(default=24, ge=1, le=48)
    hazard_threshold: Literal[1, 2, 3] = 2
    priority_scenario: Literal[
        "life_safety", "social_equity", "economic_protection"
    ] = "social_equity"
    rationale: str = Field(max_length=240)
    confidence: float = Field(default=1.0, ge=0, le=1)


class AssessmentPreferences(BaseModel):
    scenario: Literal["current", "future", "historical"] = "future"
    use_live_data: bool = True
    forecast_horizon_hours: int = Field(default=24, ge=1, le=48)
    hazard_threshold: Literal[1, 2, 3] = 2
    priority_scenario: Literal[
        "life_safety", "social_equity", "economic_protection", "custom"
    ] = "social_equity"
    weights: PriorityWeights = Field(default_factory=_social_equity_weights)
    include_simd: bool = True
    historical_issue_time: datetime | None = None

    @model_validator(mode="after")
    def historical_time_matches_scenario(self) -> "AssessmentPreferences":
        if self.scenario == "historical" and self.historical_issue_time is None:
            self.historical_issue_time = datetime(2023, 10, 6, 6, tzinfo=UTC)
        return self


class ExecutionStep(BaseModel):
    id: str
    label: str
    status: StepStatus = "pending"
    detail: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    retry_count: int = Field(default=0, ge=0)


class QualityCheck(BaseModel):
    code: str
    status: Literal["pass", "warning", "fail"]
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class RecoveryRecord(BaseModel):
    step_id: str
    action: Literal["retry", "reuse_cache", "degrade", "stop"]
    reason: str
    attempted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    outcome: str


class AssessmentPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    status: Literal["planned", "awaiting_confirmation"] = "awaiting_confirmation"
    question: str
    intent: AnalysisIntent
    preferences: AssessmentPreferences = Field(default_factory=AssessmentPreferences)
    required_datasets: list[str] = Field(default_factory=list)
    missing_datasets: list[str] = Field(default_factory=list)
    reusable_run_id: str | None = None
    steps: list[ExecutionStep] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    requires_confirmation: bool = True


class AssessmentJob(BaseModel):
    job_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    plan_id: str
    status: AssessmentStatus = "queued"
    preferences: AssessmentPreferences
    steps: list[ExecutionStep]
    run_id: str | None = None
    source_run_id: str | None = None
    warnings: list[str] = Field(default_factory=list)
    available_actions: list[str] = Field(default_factory=list)
    error: str | None = None
    final_response: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class HistoricalValidationSummary(BaseModel):
    issue_time: datetime
    valid_until: datetime
    area: str
    status: Literal["success", "partial", "unavailable", "failed"]
    forecast_source: str
    observation_sources: list[str] = Field(default_factory=list)
    rainfall_bias_mm: float | None = None
    rainfall_mae_mm: float | None = None
    spatial_correlation: float | None = None
    top_10_overlap: int | None = None
    rank_correlation: float | None = None
    interpretation: str = (
        "Forecast-input and decision-stability validation, not flood-extent accuracy."
    )
