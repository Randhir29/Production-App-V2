"""configToPhases and daily operating hours (agent.md §5.7.2, §9.1)."""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.phases import (
    carousels_missing_times,
    carousels_without_breaks,
    config_to_phases,
    daily_op_hours,
    window_seconds,
)


def cfg(start: str | None, end: str | None, breaks: list[tuple[str, str]] | None) -> dict[str, Any]:
    return {
        "heads": 72,
        "stdOutput": 3000,
        "times": {
            "start": start,
            "end": end,
            "breaks": None if breaks is None else [{"from": a, "to": b} for a, b in breaks],
        },
    }


def test_anantapur_example_start_1400_break_1900_1930_end_2230() -> None:
    phases = config_to_phases({1: cfg("14:00:00", "22:30:00", [("19:00:00", "19:30:00")])})[1]
    assert phases["working"] == [
        {"from": "14:00:00", "to": "19:00:00"},
        {"from": "19:30:00", "to": "22:30:00"},
    ]
    assert phases["breaks"] == [{"from": "19:00:00", "to": "19:30:00"}]
    assert phases["overtime"] == [
        {"from": "00:00:00", "to": "14:00:00"},
        {"from": "22:30:00", "to": "23:59:59.999"},
    ]
    assert daily_op_hours({1: phases}) == {1: {"normal": 8.0, "break": 0.5}}


def test_no_breaks_gives_single_working_window() -> None:
    phases = config_to_phases({1: cfg("06:00:00", "14:00:00", None)})[1]
    assert phases["working"] == [{"from": "06:00:00", "to": "14:00:00"}]
    assert phases["breaks"] == []
    assert daily_op_hours({1: phases})[1] == {"normal": 8.0, "break": 0.0}


def test_two_breaks_keep_config_order() -> None:
    phases = config_to_phases(
        {1: cfg("06:00:00", "14:00:00", [("08:00:00", "08:15:00"), ("11:00:00", "11:30:00")])}
    )[1]
    assert phases["working"] == [
        {"from": "06:00:00", "to": "08:00:00"},
        {"from": "08:15:00", "to": "11:00:00"},
        {"from": "11:30:00", "to": "14:00:00"},
    ]
    assert daily_op_hours({1: phases})[1]["break"] == 0.75


def test_break_at_start_has_no_leading_working_window() -> None:
    phases = config_to_phases({1: cfg("06:00:00", "14:00:00", [("06:00:00", "06:15:00")])})[1]
    assert phases["working"] == [{"from": "06:15:00", "to": "14:00:00"}]


def test_break_ending_at_shift_end_has_no_trailing_window() -> None:
    phases = config_to_phases({1: cfg("06:00:00", "14:00:00", [("13:30:00", "14:00:00")])})[1]
    assert phases["working"] == [{"from": "06:00:00", "to": "13:30:00"}]


def test_multiple_carousels_keyed_by_id_in_order() -> None:
    phases = config_to_phases({2: cfg("06:00:00", "14:00:00", None), 1: cfg("14:00:00", "22:00:00", None)})
    assert list(phases) == [2, 1]


def test_window_seconds_uses_clock_components_only() -> None:
    assert window_seconds({"from": "19:00:00", "to": "19:30:00"}) == 1800
    assert window_seconds({"from": "22:00:00", "to": "02:00:00"}) == 20 * 3600  # days ignored
    assert window_seconds({"from": "22:30:00", "to": "23:59:59.999"}) == pytest.approx(5399.999)


def test_config_guards() -> None:
    config = {
        1: cfg("06:00:00", "14:00:00", [("08:00:00", "08:15:00")]),
        2: cfg(None, None, None),
        3: cfg("06:00:00", "14:00:00", None),
    }
    assert carousels_missing_times(config) == [2]
    assert carousels_without_breaks(config) == [2, 3]
