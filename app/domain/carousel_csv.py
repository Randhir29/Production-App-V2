"""Parser for the corrected carousel-details CSV and the static ``carousal_config`` rendering.

Pure functions, no I/O. The CSV columns used are ``sap_id``, ``carousal_id``, ``heads``,
``rated_productivity``, ``production_hrs`` (JSON list with one shift) and ``breaks`` (JSON list).
``max_productivity`` and ``min_productivity`` are carried through but not used by any rule yet.
"""

from __future__ import annotations

import csv
import io
import itertools
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.domain.carousel_config import format_time

REQUIRED_COLUMNS = ("sap_id", "carousal_id", "heads", "rated_productivity", "production_hrs", "breaks")


@dataclass(frozen=True, slots=True)
class CsvCarousel:
    sap_id: str
    carousal_id: int
    heads: int
    rated_productivity: int
    start: str
    end: str
    breaks: tuple[tuple[str, str], ...]
    max_productivity: int | None
    min_productivity: int | None
    line: int


@dataclass(slots=True)
class CsvParseResult:
    carousels: list[CsvCarousel] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # rows that were skipped
    warnings: list[str] = field(default_factory=list)  # rows that were kept


def _json_list(raw: str | None) -> list[Any]:
    text = (raw or "").strip()
    if not text or text.upper() == "NULL":
        return []
    value = json.loads(text)
    if not isinstance(value, list):
        raise TypeError("expected a JSON list")
    return value


def _optional_int(raw: str | None) -> int | None:
    text = (raw or "").strip()
    if not text or text.upper() == "NULL":
        return None
    return int(float(text))


def _hms(value: Any) -> str:
    text = format_time(str(value))
    if text is None:
        raise ValueError("missing time")
    return text


def parse_carousel_csv(content: str) -> CsvParseResult:
    """Parse and validate the CSV text. Invalid rows are skipped and described in ``problems``."""
    result = CsvParseResult()
    reader = csv.DictReader(io.StringIO(content.lstrip("﻿")))
    missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"CSV is missing required columns: {', '.join(missing)}")

    seen: dict[tuple[str, int], int] = {}
    for row in reader:
        line = reader.line_num
        label = f"line {line} (sap_id={row.get('sap_id')!r}, carousal_id={row.get('carousal_id')!r})"
        try:
            sap_id = str(row["sap_id"]).strip()
            carousal_id = int(str(row["carousal_id"]).strip())
            heads = int(str(row["heads"]).strip())
            rated = int(float(str(row["rated_productivity"]).strip()))
            if not sap_id or heads <= 0 or rated <= 0:
                raise ValueError("sap_id, heads and rated_productivity must be set and positive")

            shifts = _json_list(row["production_hrs"])
            if len(shifts) != 1:
                raise ValueError(f"expected exactly one shift in production_hrs, found {len(shifts)}")
            start, end = _hms(shifts[0]["start_time"]), _hms(shifts[0]["stop_time"])
            if start >= end:
                raise ValueError(f"shift start {start} is not before stop {end}")

            breaks = sorted((_hms(b["start_time"]), _hms(b["stop_time"])) for b in _json_list(row["breaks"]))
            for b_from, b_to in breaks:
                if b_from >= b_to:
                    raise ValueError(f"break {b_from}-{b_to} is not ascending")
            for (_, prev_to), (next_from, _) in itertools.pairwise(breaks):
                if next_from < prev_to:
                    raise ValueError("breaks overlap")
            outside = [b for b in breaks if b[0] < start or b[1] > end]
            if outside:
                result.warnings.append(f"{label}: break outside the shift {start}-{end}: {outside}")
            if not breaks:
                result.warnings.append(f"{label}: no breaks (V2 reports are blocked, D-06)")

            key = (sap_id, carousal_id)
            if key in seen:
                result.warnings.append(f"{label}: duplicate of line {seen[key]}; the later row wins")
                result.carousels = [c for c in result.carousels if (c.sap_id, c.carousal_id) != key]
            seen[key] = line
            result.carousels.append(
                CsvCarousel(
                    sap_id=sap_id,
                    carousal_id=carousal_id,
                    heads=heads,
                    rated_productivity=rated,
                    start=start,
                    end=end,
                    breaks=tuple(breaks),
                    max_productivity=_optional_int(row.get("max_productivity")),
                    min_productivity=_optional_int(row.get("min_productivity")),
                    line=line,
                )
            )
        except (ValueError, KeyError, TypeError) as exc:  # JSONDecodeError is a ValueError
            result.problems.append(f"{label}: {exc}")
    return result


def build_static_carousal_config(
    carousels: Iterable[CsvCarousel], sap_to_short: Mapping[str, str]
) -> tuple[dict[str, dict[int, CsvCarousel]], list[str]]:
    """Group carousels by plant ``short_name``; returns the grouping and unmatched ``sap_id``s."""
    grouped: dict[str, dict[int, CsvCarousel]] = {}
    unmatched: set[str] = set()
    for c in carousels:
        short = sap_to_short.get(c.sap_id)
        if short is None:
            unmatched.add(c.sap_id)
            continue
        grouped.setdefault(short, {})[c.carousal_id] = c
    ordered = {s: dict(sorted(grouped[s].items())) for s in sorted(grouped)}
    return ordered, sorted(unmatched)


def render_carousal_config_yaml(grouped: Mapping[str, Mapping[int, CsvCarousel]]) -> str:
    """Render the ``carousal_config:`` section in the shape ``StaticPlantConfig`` loads."""
    if not grouped:
        return "carousal_config: {}\n"
    lines = ["carousal_config:"]
    for short, carousels in grouped.items():
        lines.append(f"  {short}:")
        for carousal_id, c in carousels.items():
            lines.append(f"    {carousal_id}:")
            lines.append(f"      heads: {c.heads}")
            lines.append(f"      stdOutput: {c.rated_productivity}")
            lines.append("      times:")
            lines.append(f'        start: "{c.start}"')
            lines.append(f'        end: "{c.end}"')
            if c.breaks:
                lines.append("        breaks:")
                lines.extend(f'          - {{from: "{a}", to: "{b}"}}' for a, b in c.breaks)
    return "\n".join(lines) + "\n"


def replace_carousal_config_section(yaml_text: str, rendered: str) -> str:
    """Replace the trailing ``carousal_config:`` section of the static YAML (it must be last)."""
    lines = yaml_text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith("carousal_config:"):
            tail = lines[i + 1 :]
            if any(t.strip() and not t.startswith((" ", "#")) for t in tail):
                raise ValueError("carousal_config is not the last top-level key of the YAML file")
            return "".join(lines[:i]) + rendered
    return yaml_text.rstrip("\n") + "\n\n" + rendered
