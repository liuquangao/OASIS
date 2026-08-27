"""Evidence provenance attached to every external observation."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DataProvenance(BaseModel):
    provider: str
    dataset: str
    source_url: str
    retrieved_at: datetime
    observation_start: datetime | None = None
    observation_end: datetime | None = None
    licence: str | None = None
    integration: str

