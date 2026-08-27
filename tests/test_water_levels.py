from __future__ import annotations

import pytest

from oasis.domain.water_levels import relative_level_context


@pytest.mark.parametrize(
    ("value", "expected_state", "expected_percent"),
    [
        (0.0, "low", -11.1),
        (0.1, "normal", 0.0),
        (0.55, "normal", 50.0),
        (1.0, "normal", 100.0),
        (1.1, "high", 111.1),
    ],
)
def test_relative_level_context(
    value: float, expected_state: str, expected_percent: float
) -> None:
    state, percent = relative_level_context(value, 0.1, 1.0)
    assert state == expected_state
    assert percent == expected_percent


def test_relative_level_context_rejects_invalid_range() -> None:
    with pytest.raises(ValueError):
        relative_level_context(0.5, 1.0, 1.0)
