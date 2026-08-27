"""Provider-neutral rainfall observation models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from oasis.models.provenance import DataProvenance


class RainfallStation(BaseModel):
    station_no: str
    name: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    distance_km: float = Field(ge=0)


class RainfallReading(BaseModel):
    timestamp: datetime
    value_mm: float = Field(ge=0)
    quality_code: int | str | None = None


class RainfallStationSummary(BaseModel):
    station: RainfallStation
    requested_period_hours: int = Field(ge=1)
    reading_count: int = Field(ge=1)
    recent_readings: list[RainfallReading]
    total_mm: float = Field(ge=0)
    last_1h_mm: float = Field(ge=0)
    last_3h_mm: float = Field(ge=0)
    last_6h_mm: float = Field(ge=0)
    last_24h_mm: float = Field(ge=0)
    maximum_15min_mm: float = Field(ge=0)
    maximum_1h_mm: float = Field(ge=0)
    observation_start: datetime
    latest_timestamp: datetime
    quality_codes: list[int | str] = Field(default_factory=list)


class RainfallAreaSummary(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_km: float = Field(gt=0)
    station_count: int = Field(ge=0)
    stations: list[RainfallStationSummary]
    provenance: DataProvenance
    warnings: list[str] = Field(default_factory=list)

