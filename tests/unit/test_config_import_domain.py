"""Carousel CSV parser, static-YAML rendering and Database.php parser (pure functions)."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from app.domain.carousel_csv import (
    build_static_carousal_config,
    parse_carousel_csv,
    render_carousal_config_yaml,
    replace_carousal_config_section,
)
from app.domain.php_connections import NON_PLANT_GROUPS, parse_database_php
from app.domain.static_config import parse_static_config

SHIPPED = Path(__file__).resolve().parents[2] / "config" / "static_plant_config.yaml"

HEADER = [
    "sap_id", "carousal_id", "heads", "rated_productivity", "production_hrs", "breaks", "id",
    "created_at", "updated_at", "entity_id", "max_productivity", "skip_zero_performance_score",
    "min_productivity",
]  # fmt: skip


def shift(start: str, stop: str) -> str:
    return json.dumps([{"stop_time": stop, "shift_name": "Shift 1", "start_time": start, "description": ""}])


def brks(*pairs: tuple[str, str]) -> str:
    return json.dumps(
        [
            {"stop_time": b, "shift_name": f"Break {i}", "start_time": a, "description": ""}
            for i, (a, b) in enumerate(pairs, 1)
        ]
    )


def make_csv(rows: list[list[object]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(HEADER)
    for i, r in enumerate(rows, 1):
        sap, car, heads, rated, prod, br = r
        writer.writerow(
            [sap, car, heads, rated, prod, br, i, "09:15.9", "09:15.9", "NULL", 1600, "NULL", 1300]
        )
    return out.getvalue()


# rows taken from the corrected CSV (values as in the file)
GOOD = [
    [
        2935,
        2,
        24,
        1820,
        shift("06:00:00", "22:30:00"),
        brks(("09:30:00", "10:00:00"), ("19:00:00", "19:30:00")),
    ],
    [2173, 2, 24, 1630, shift("14:00:00", "17:30:00"), "[]"],  # no breaks
    [
        2401,
        1,
        48,
        3200,
        shift("06:40:00", "23:10:00"),
        brks(("11:30:00", "12:00:00"), ("19:30:00", "20:00:00")),
    ],
    [
        2693,
        1,
        12,
        850,
        shift("06:00:00", "22:00:00"),
        brks(("13:00:00", "13:30:00"), ("20:00:00", "20:30:00")),
    ],
    [
        2707,
        1,
        72,
        4200,
        shift("06:00:00", "22:00:00"),
        brks(("09:30:00", "10:00:00"), ("18:30:00", "19:00:00")),
    ],
]


def test_parses_real_rows() -> None:
    result = parse_carousel_csv(make_csv(GOOD))
    assert result.problems == []
    first = result.carousels[0]
    assert (first.sap_id, first.carousal_id, first.heads, first.rated_productivity) == ("2935", 2, 24, 1820)
    assert (first.start, first.end) == ("06:00:00", "22:30:00")
    assert first.breaks == (("09:30:00", "10:00:00"), ("19:00:00", "19:30:00"))
    assert (first.max_productivity, first.min_productivity) == (1600, 1300)
    assert [c.heads for c in result.carousels] == [24, 24, 48, 12, 72]
    assert any("no breaks" in w and "D-06" in w for w in result.warnings)


def test_breaks_are_sorted_ascending() -> None:
    rows = [
        [
            1,
            1,
            24,
            1600,
            shift("06:00:00", "22:30:00"),
            brks(("19:00:00", "19:30:00"), ("09:30:00", "10:00:00")),
        ]
    ]
    carousel = parse_carousel_csv(make_csv(rows)).carousels[0]
    assert carousel.breaks == (("09:30:00", "10:00:00"), ("19:00:00", "19:30:00"))


@pytest.mark.parametrize(
    ("row", "needle"),
    [
        ([1, 1, 24, 1600, json.dumps([]), "[]"], "exactly one shift"),
        (
            [1, 1, 24, 1600, json.dumps(json.loads(shift("06:00:00", "12:00:00")) * 2), "[]"],
            "exactly one shift",
        ),
        ([1, 1, 0, 1600, shift("06:00:00", "12:00:00"), "[]"], "positive"),
        ([1, 1, 24, 1600, shift("12:00:00", "06:00:00"), "[]"], "not before"),
        ([1, 1, 24, 1600, shift("06:00:00", "12:00:00"), brks(("10:00:00", "09:00:00"))], "ascending"),
        (
            [
                1,
                1,
                24,
                1600,
                shift("06:00:00", "20:00:00"),
                brks(("10:00:00", "11:00:00"), ("10:30:00", "12:00:00")),
            ],
            "overlap",
        ),
        ([1, 1, 24, 1600, "not json", "[]"], "Expecting value"),
        (["", 1, 24, 1600, shift("06:00:00", "12:00:00"), "[]"], "sap_id"),
    ],
)
def test_invalid_rows_are_skipped_with_a_reason(row: list[object], needle: str) -> None:
    result = parse_carousel_csv(make_csv([row, GOOD[0]]))
    assert len(result.carousels) == 1 and result.carousels[0].sap_id == "2935"
    assert len(result.problems) == 1 and needle in result.problems[0]


def test_break_outside_shift_and_duplicates_are_warnings() -> None:
    rows = [
        [9, 1, 24, 1600, shift("09:00:00", "17:00:00"), brks(("17:30:00", "18:00:00"))],
        [9, 1, 24, 1700, shift("09:00:00", "17:00:00"), brks(("13:00:00", "13:30:00"))],
    ]
    result = parse_carousel_csv(make_csv(rows))
    assert result.problems == []
    assert len(result.carousels) == 1 and result.carousels[0].rated_productivity == 1700  # later wins
    assert any("outside the shift" in w for w in result.warnings)
    assert any("duplicate" in w for w in result.warnings)


def test_missing_required_column() -> None:
    with pytest.raises(ValueError, match="missing required columns"):
        parse_carousel_csv("sap_id,carousal_id\n1,1\n")


# ---- static YAML ---------------------------------------------------------------------------


def test_render_and_load_round_trip() -> None:
    parsed = parse_carousel_csv(make_csv(GOOD))
    grouped, unmatched = build_static_carousal_config(
        parsed.carousels, {"2935": "anantapur", "2401": "kochi", "2173": "kota", "2693": "jammu"}
    )
    assert unmatched == ["2707"]
    assert list(grouped) == ["anantapur", "jammu", "kochi", "kota"]  # sorted by short name

    shipped = SHIPPED.read_text(encoding="utf-8")
    text = replace_carousal_config_section(shipped, render_carousal_config_yaml(grouped))
    cfg = parse_static_config(text)

    a2 = cfg.carousal_config["anantapur"][2]
    assert (a2.heads, a2.stdOutput) == (24, 1820)
    assert a2.times is not None and (a2.times.start, a2.times.end) == ("06:00:00", "22:30:00")
    assert a2.times.breaks == (
        {"from": "09:30:00", "to": "10:00:00"},
        {"from": "19:00:00", "to": "19:30:00"},
    )
    kota = cfg.carousal_config["kota"][2]
    assert kota.times is not None and kota.times.breaks is None  # no breaks -> key omitted
    assert cfg.carousal_config["kochi"][1].heads == 48
    # the rest of the file is untouched
    assert len(cfg.plants) == 55 and cfg.plant_name == "Mysore LPG Plant"
    assert text.startswith(shipped.split("carousal_config:")[0])


def test_replace_is_idempotent_and_rejects_non_trailing_section() -> None:
    parsed = parse_carousel_csv(make_csv(GOOD[:1]))
    grouped, _ = build_static_carousal_config(parsed.carousels, {"2935": "anantapur"})
    rendered = render_carousal_config_yaml(grouped)
    once = replace_carousal_config_section(SHIPPED.read_text(encoding="utf-8"), rendered)
    assert replace_carousal_config_section(once, rendered) == once
    with pytest.raises(ValueError, match="not the last"):
        replace_carousal_config_section("carousal_config: {}\nother_key: 1\n", rendered)


def test_empty_grouping_renders_empty_mapping() -> None:
    assert render_carousal_config_yaml({}) == "carousal_config: {}\n"


# ---- Database.php ----------------------------------------------------------------------------

PHP = """<?php namespace Config;
class Database extends \\CodeIgniter\\Database\\Config
{
    public $filesPath = APPPATH . 'Database/';
    public $default = [
        'hostname' => 'localhost', 'username' => 'root', 'password' => 'root-pw',
        'database' => 'prodash', 'DBDriver' => 'MySQLi', 'port' => 3306, 'failover' => [],
    ];
    public $kosan = [
        'hostname' => '10.0.0.1', 'username' => 'u', 'password' => 'p', 'database' => 'ccc',
        'DBDriver' => 'Postgre', 'failover' => [], 'port' => 5432,
    ];
    public $anantapur = [
        // 'DSN'      => 'pgsql:host=10.9.9.9;port=5432;dbname=ccc;',
        'DSN'      => '',
        'hostname' => '10.4.1.2',
        'username' => 'readonly',
        'password' => "pa'ss\\"w\\\\rd",
        'database' => 'ccc',
        'DBDriver' => 'Postgre',
        'DBDebug'  => (ENVIRONMENT !== 'production'),
        'failover' => [],
        'port'     => 5433,
    ];
    public $jabalpur1 = [
        'hostname' => '10.4.1.3', 'username' => 'ro', 'password' => 'it\\'s', 'database' => 'ccc',
        'DBDriver' => 'Postgre', 'failover' => [], 'port' => 5432,
    ];
    public $testplant = [
        'hostname' => '10.4.1.4', 'username' => 'ro', 'password' => 'x', 'database' => 'ccc',
        'DBDriver' => 'Postgre', 'failover' => [], 'port' => 5432,
    ];
    public $broken = [
        'hostname' => '', 'username' => 'ro', 'password' => 'x', 'database' => 'ccc',
        'DBDriver' => 'Postgre', 'failover' => [], 'port' => 5432,
    ];
    public $tests = [
        'database' => ':memory:', 'DBDriver' => 'SQLite3', 'failover' => [],
    ];
}
"""


def test_database_php_extracts_plant_groups_only() -> None:
    result = parse_database_php(PHP)
    names = [c.short_name for c in result.connections]
    assert names == ["anantapur", "jabalpur1"]
    assert sorted(result.skipped_groups) == ["kosan", "testplant"]
    assert any("broken" in p for p in result.problems)
    assert {"default", "tests", "kosan", "testplant"} == set(NON_PLANT_GROUPS)

    a = result.connections[0]
    assert (a.host, a.user, a.database, a.port) == ("10.4.1.2", "readonly", "ccc", 5433)
    assert a.password == "pa'ss\"w\\rd"  # escapes resolved
    assert result.connections[1].password == "it's"


def test_commented_dsn_host_is_not_used_and_password_is_not_in_repr() -> None:
    a = parse_database_php(PHP).connections[0]
    assert a.host != "10.9.9.9"
    assert "ss" not in repr(a) and "readonly" in repr(a)
