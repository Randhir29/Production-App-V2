"""Carousel configuration shape (agent.md §4.1). Pure functions, no I/O."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import time, timedelta
from typing import Any

TimeLike = time | timedelta | str | None

_HMS = re.compile(r"^(\d{1,3}):([0-5]\d):([0-5]\d)(?:\.\d+)?$")


def format_time(value: TimeLike) -> str | None:
    """Normalise a TIME value to zero-padded ``"HH:MM:SS"`` (§7.4).

    ``aiomysql`` returns ``TIME`` as ``timedelta``; PostgreSQL returns ``datetime.time``.
    Fractional seconds are dropped. ``None`` stays ``None``.
    """
    if value is None:
        return None
    if isinstance(value, time):
        return f"{value.hour:02d}:{value.minute:02d}:{value.second:02d}"
    if isinstance(value, timedelta):
        total = int(value.total_seconds())
        if total < 0:
            raise ValueError(f"negative TIME value: {value!r}")
        hours, rem = divmod(total, 3600)
        minutes, seconds = divmod(rem, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    match = _HMS.match(value.strip())
    if match is None:
        raise ValueError(f"unrecognised TIME value: {value!r}")
    hours, minutes, seconds = (int(g) for g in match.groups())
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def parse_time(value: TimeLike) -> time | None:
    """Convert any TIME representation to ``datetime.time``; values of 24 h or more are rejected."""
    text = format_time(value)
    if text is None:
        return None
    hours, minutes, seconds = (int(p) for p in text.split(":"))
    if hours > 23:
        raise ValueError(f"TIME value out of range for time-of-day: {text}")
    return time(hours, minutes, seconds)


@dataclass(frozen=True, slots=True)
class CarouselRow:
    """One ``carousals`` row with times already normalised."""

    carousal_id: int
    heads: int
    rated_productivity: int
    start_time: str | None
    stop_time: str | None


@dataclass(frozen=True, slots=True)
class BreakRow:
    """One ``breaks`` row with times already normalised."""

    carousal_id: int
    start_time: str
    stop_time: str


def build_carousel_config(
    carousels: Iterable[CarouselRow], breaks: Iterable[BreakRow]
) -> dict[int, dict[str, Any]]:
    """Build ``{carousal_id: {heads, stdOutput, times: {start, end, breaks}}}`` (§4.1).

    * Carousels are keyed by ``carousal_id`` in the order given (table order). A repeated id
      overwrites the earlier row's values but keeps its position, like a PHP array assignment.
    * ``breaks`` is ``None`` when the carousel has no break rows; otherwise the breaks are
      ``{"from", "to"}`` pairs in the order given.
    """
    by_carousel: dict[int, list[dict[str, str]]] = {}
    for brk in breaks:
        by_carousel.setdefault(brk.carousal_id, []).append({"from": brk.start_time, "to": brk.stop_time})

    config: dict[int, dict[str, Any]] = {}
    for row in carousels:
        config[row.carousal_id] = {
            "heads": row.heads,
            "stdOutput": row.rated_productivity,
            "times": {
                "start": row.start_time,
                "end": row.stop_time,
                "breaks": by_carousel.get(row.carousal_id) or None,
            },
        }
    return config


def carousel_ids(config: Mapping[int, Any]) -> list[int]:
    """Carousel ids in config order (the ``getCarousals`` list used by reports, §5.7.1)."""
    return list(config)
