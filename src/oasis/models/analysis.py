"""Typed, compact results exposed by the Core Analyst Agent tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


AnalysisStatus = Literal[
    "success",
    "success_with_warnings",
    "partial",
    "unavailable",
    "failed",
]


class AnalysisWarning(BaseModel):
    code: str = "analysis_warning"
    message: str


class AnalysisRunSummary(BaseModel):
    """Small Agent-facing handle for a persisted full analysis result."""

    run_id: str
    analysis_type: str
    status: AnalysisStatus
    summary: dict[str, Any] = Field(default_factory=dict)
    output_keys: list[str] = Field(default_factory=list)
    warnings: list[AnalysisWarning] = Field(default_factory=list)
    requires_human_review: bool = True


class DataReadinessItem(BaseModel):
    dataset: str
    category: str
    status: str
    reason: str | None = None


class DataReadinessSummary(BaseModel):
    status_counts: dict[str, int]
    available: list[DataReadinessItem]
    incomplete: list[DataReadinessItem]
    guidance: list[str] = Field(default_factory=list)


class PriorityUnitInput(BaseModel):
    id: str
    name: str | None = None
    hazard: float = Field(ge=0, le=1)
    exposure: float = Field(ge=0, le=1)
    vulnerability: float = Field(ge=0, le=1)


class PriorityWeights(BaseModel):
    hazard: float = Field(ge=0, le=1)
    exposure: float = Field(ge=0, le=1)
    vulnerability: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> "PriorityWeights":
        total = self.hazard + self.exposure + self.vulnerability
        if abs(total - 1.0) > 1e-6:
            raise ValueError("Priority weights must sum to 1.")
        return self


class PriorityScenarioInput(BaseModel):
    name: str
    weights: PriorityWeights
    description: str = ""
