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
    map_layers: list["AnalysisMapLayer"] = Field(default_factory=list)
    warnings: list[AnalysisWarning] = Field(default_factory=list)
    requires_human_review: bool = True


class AnalysisMapLayer(BaseModel):
    id: str
    label: str
    kind: Literal["wms", "geojson"]
    url: str
    layer_name: str | None = None
    style: str = ""
    opacity: float = Field(default=0.68, ge=0, le=1)
    visible: bool = True


class GeneralizedAnalysisPlan(BaseModel):
    area: str
    hazard_type: str
    temporal_scope: Literal["historical", "current", "future"]
    executable_now: bool
    reusable_components: list[str]
    required_datasets: list[str]
    missing_datasets: list[str]
    extension_points: list[str]
    workflow: list[str]
    discovered_sources: list[dict[str, str]] = Field(default_factory=list)


class ExtensionFactor(BaseModel):
    name: str
    weight: float = Field(gt=0)
    direction: Literal["higher", "lower"] = "higher"


class HazardExtensionSpec(BaseModel):
    hazard_type: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    factors: list[ExtensionFactor]
    medium_threshold: float = Field(gt=0, lt=1)
    high_threshold: float = Field(gt=0, lt=1)

    @model_validator(mode="after")
    def validate_extension(self) -> "HazardExtensionSpec":
        if self.medium_threshold >= self.high_threshold:
            raise ValueError("medium_threshold must be lower than high_threshold")
        return self


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
    geometry: dict[str, Any] | None = Field(
        default=None,
        description="Optional GeoJSON geometry in EPSG:4326 for website map display.",
    )


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
