"""Deterministic context calculations for river-level observations."""

from __future__ import annotations

from typing import Literal


LevelState = Literal["low", "normal", "high"]


def relative_level_context(
    value_m: float, normal_low_m: float, normal_high_m: float
) -> tuple[LevelState, float]:
    """Classify a level and locate it relative to a station's normal range.

    The percentage is intentionally not clamped: a value below the normal range
    is negative and a value above it exceeds 100 percent.
    """

    if normal_high_m <= normal_low_m:
        raise ValueError("normal_high_m must be greater than normal_low_m")

    percentage = (value_m - normal_low_m) / (normal_high_m - normal_low_m) * 100
    if value_m < normal_low_m:
        state: LevelState = "low"
    elif value_m > normal_high_m:
        state = "high"
    else:
        state = "normal"
    return state, round(percentage, 1)
