"""Provider-neutral route geometry and calculated hazard summaries."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RouteCandidate(BaseModel):
    coordinates: list[tuple[float, float]]
    distance_m: float
    duration_seconds: float
    provider: str
    source_url: str


class RouteHazardSummary(BaseModel):
    sample_spacing_m: float
    high_distance_m: float
    medium_distance_m: float
    low_distance_m: float
    no_data_distance_m: float
    coverage_percent: float
    hazard_index: float | None
    highest_class: Literal["high", "medium", "low", "no_data"]
    warnings: list[str] = Field(default_factory=list)


class RememberedRoute(BaseModel):
    id: str
    label: str
    origin_location_id: str
    destination_location_id: str
    mode: Literal["driving"] = "driving"
    coordinates: list[tuple[float, float]]
    distance_m: float
    duration_seconds: float
    provider: str
    source_url: str
    hazard: RouteHazardSummary | None = None
    rank: int | None = None


class RouteSummary(BaseModel):
    id: str
    label: str
    mode: Literal["driving"]
    distance_m: float
    duration_seconds: float
    hazard: RouteHazardSummary | None = None
    rank: int | None = None
