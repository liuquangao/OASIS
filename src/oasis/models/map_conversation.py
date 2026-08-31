"""State, evidence, and visualization events for the tool-using map Agent."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from oasis.models.routes import RememberedRoute
from oasis.models.analysis import AnalysisMapLayer


class GeoPlace(BaseModel):
    label: str
    latitude: float
    longitude: float
    place_type: str
    distance_km: float | None = None
    provider: str
    source_url: str
    retrieved_at: datetime


class RememberedLocation(BaseModel):
    id: str
    label: str
    search_query: str
    latitude: float
    longitude: float
    place_type: str = "place"
    distance_km: float | None = None
    provider: str | None = None
    source_url: str | None = None
    retrieved_at: datetime | None = None
    class_value: int | None = None
    risk_level: Literal["high", "medium", "low", "no_data"] | None = None
    risk_label: str | None = None
    hazard_provider: str | None = None
    hazard_dataset: str | None = None
    hazard_source_url: str | None = None
    hazard_retrieved_at: datetime | None = None
    hazard_snapshot_time: datetime | None = None
    hazard_warnings: list[str] = Field(default_factory=list)


class RiskReportEvidence(BaseModel):
    label: str
    value: str
    source: str
    observed_at: datetime | None = None
    source_url: str | None = None


class RiskReport(BaseModel):
    title: str
    question: str
    area: str
    time_horizon: str
    overall_risk: Literal["high", "medium", "low", "mixed", "unknown"]
    summary: str
    key_findings: list[str] = Field(default_factory=list, max_length=6)
    evidence: list[RiskReportEvidence] = Field(default_factory=list, max_length=8)
    limitations: list[str] = Field(default_factory=list, max_length=6)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MapSessionState(BaseModel):
    locations: list[RememberedLocation] = Field(default_factory=list)
    visible_location_ids: list[str] = Field(default_factory=list)
    routes: list[RememberedRoute] = Field(default_factory=list)
    visible_route_ids: list[str] = Field(default_factory=list)
    active_location_id: str | None = None
    hazard_layer_visible: bool = False
    analysis_layers: list[AnalysisMapLayer] = Field(default_factory=list)
    visible_analysis_layer_ids: list[str] = Field(default_factory=list)
    risk_report: RiskReport | None = None
    last_task: Literal["locate", "risk", "nearby", "route"] | None = None


class MapEvent(BaseModel):
    type: Literal[
        "display_locations",
        "refresh_locations",
        "remove_locations",
        "clear_locations",
        "fit_locations",
        "set_hazard_layer",
        "sync_analysis_layers",
        "display_routes",
        "refresh_routes",
        "clear_routes",
        "fit_routes",
    ]
    location_ids: list[str] = Field(default_factory=list)
    route_ids: list[str] = Field(default_factory=list)
    layer_ids: list[str] = Field(default_factory=list)
    visible: bool | None = None


class MapAgentAnswer(BaseModel):
    message: str
    risk_report: RiskReport | str | None = None


class MapAgentResponse(BaseModel):
    message: str
    state: MapSessionState
    events: list[MapEvent]
    tools_used: list[str] = Field(default_factory=list)
