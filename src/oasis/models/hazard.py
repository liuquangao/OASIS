"""Provider-neutral result returned by a calculated hazard raster lookup."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class HazardLookupResult(BaseModel):
    latitude: float
    longitude: float
    class_value: int | None
    risk_level: Literal["high", "medium", "low", "no_data"]
    risk_label: str
    provider: str
    dataset: str
    source_url: str
    retrieved_at: datetime
    snapshot_time: datetime | None = None
    warnings: list[str]
