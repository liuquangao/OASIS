"""Deterministic interpretation of the project's hazard class codes."""

from __future__ import annotations

from typing import Literal


HazardLevel = Literal["high", "medium", "low", "no_data"]

HAZARD_CLASSES: dict[int, tuple[HazardLevel, str]] = {
    1: ("low", "Low"),
    2: ("medium", "Medium"),
    3: ("high", "High"),
}


def interpret_hazard_class(value: int | None) -> tuple[HazardLevel, str]:
    """Map a stored class code to its configured label; never infer no risk."""

    if value is None or value == 0:
        return "no_data", "No classified value"
    return HAZARD_CLASSES.get(value, ("no_data", "Unknown class"))
