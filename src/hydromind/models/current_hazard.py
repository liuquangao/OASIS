"""Models for the latest calculated Glasgow hazard snapshot."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CurrentHazardSnapshot(BaseModel):
    available: bool
    generated_at: datetime | None = None
    observation_start: datetime | None = None
    observation_end: datetime | None = None
    station_count: int = 0
    dataset: str = "glasgow_flood:current_hazard_class_5m"
    warnings: list[str] = Field(default_factory=list)
