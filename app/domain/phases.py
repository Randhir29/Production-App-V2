"""Operating phases and daily operating hours (agent.md §5.7.2). Pure, no I/O."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict

OVERTIME_DAY_START = "00:00:00"
OVERTIME_DAY_END = "23:59:59.999"


class CarouselPhases(TypedDict):
    working: list[dict[str, str]]
    breaks: list[dict[str, str]]
    overtime: list[dict[str, str]]


def config_to_phases(config: Mapping[int, Mapping[str, Any]]) -> dict[int, CarouselPhases]:
    """``configToPhases``: working windows, break windows and overtime windows per carousel.

    ``HH:MM:SS`` strings are compared as strings, like the PHP code. A carousel's ``start`` and
    ``end`` must be set (callers reject incomplete configs first); ``breaks`` may be ``None``.
    """
    phases: dict[int, CarouselPhases] = {}
    for carousel, cfg in config.items():
        times = cfg["times"]
        start: str = times["start"]
        end: str = times["end"]
        working: list[dict[str, str]] = []
        breaks_out: list[dict[str, str]] = []
        current = start
        for br in times.get("breaks") or []:
            if current < br["from"]:
                working.append({"from": current, "to": br["from"]})
            breaks_out.append({"from": br["from"], "to": br["to"]})
            current = br["to"]
        if current < end:
            working.append({"from": current, "to": end})
        overtime = [
            {"from": OVERTIME_DAY_START, "to": start},
            {"from": end, "to": OVERTIME_DAY_END},
        ]
        phases[carousel] = {"working": working, "breaks": breaks_out, "overtime": overtime}
    return phases


def _seconds(value: str) -> float:
    hours, minutes, seconds = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def window_seconds(window: Mapping[str, str]) -> float:
    """Duration from the h, m, s components only; days are ignored (§5.7.2)."""
    return abs(_seconds(window["to"]) - _seconds(window["from"]))


def daily_op_hours(phases: Mapping[int, CarouselPhases]) -> dict[int, dict[str, float]]:
    """``{carousel: {normal, break}}`` in hours per operating day."""
    return {
        carousel: {
            "normal": sum(window_seconds(w) for w in p["working"]) / 3600,
            "break": sum(window_seconds(w) for w in p["breaks"]) / 3600,
        }
        for carousel, p in phases.items()
    }


def carousels_missing_times(config: Mapping[int, Mapping[str, Any]]) -> list[int]:
    """Carousels whose shift start or end is unset: phases cannot be derived for them."""
    return [c for c, cfg in config.items() if not cfg["times"]["start"] or not cfg["times"]["end"]]


def carousels_without_breaks(config: Mapping[int, Mapping[str, Any]]) -> list[int]:
    """Carousels with no break rows (V2 SQL is invalid for them, D-06)."""
    return [c for c, cfg in config.items() if not cfg["times"].get("breaks")]
