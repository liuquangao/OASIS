"""Hydrometric station and observation models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .provenance import DataProvenance


class MonitoringStation(BaseModel):
    station_no: str
    name: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    parameter_name: str
    parameter_no: str
    distance_km: float | None = Field(default=None, ge=0)


class WaterLevelReading(BaseModel):
    timestamp: datetime
    value_m: float
    quality_code: int | str | None = None


class WaterLevelSummary(BaseModel):
    station: MonitoringStation
    reading_count: int = Field(ge=1)
    recent_readings: list[WaterLevelReading]
    latest_value_m: float
    latest_timestamp: datetime
    minimum_value_m: float
    maximum_value_m: float
    change_m: float
    change_per_hour_m: float | None
    trend: str
    normal_range_low_m: float | None = None
    normal_range_high_m: float | None = None
    relative_level_percent: float | None = None
    level_state: Literal["low", "normal", "high"] | None = None
    level_context_source_url: str | None = None
    provenance: DataProvenance
    warnings: list[str] = Field(default_factory=list)


class WaterLevelAreaSummary(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_km: float = Field(gt=0)
    station_count: int = Field(ge=0)
    stations: list[WaterLevelSummary]
    warnings: list[str] = Field(default_factory=list)
