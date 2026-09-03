"""Deterministic calculations over rainfall readings."""

from __future__ import annotations

from datetime import datetime, timedelta

from hydromind.models.rainfall import RainfallReading


def rainfall_total(
    readings: list[RainfallReading],
    *,
    hours: float,
    end: datetime | None = None,
) -> float:
    """Sum interval rainfall within a trailing time window."""

    if hours <= 0:
        raise ValueError("hours must be positive")
    if not readings:
        return 0.0
    end = end or max(reading.timestamp for reading in readings)
    start = end - timedelta(hours=hours)
    return sum(
        reading.value_mm for reading in readings if start < reading.timestamp <= end
    )


def maximum_rolling_rainfall(
    readings: list[RainfallReading], *, hours: float
) -> float:
    """Return the largest trailing-window total ending at an observation."""

    if hours <= 0:
        raise ValueError("hours must be positive")
    if not readings:
        return 0.0
    ordered = sorted(readings, key=lambda reading: reading.timestamp)
    return max(
        rainfall_total(ordered, hours=hours, end=reading.timestamp)
        for reading in ordered
    )

