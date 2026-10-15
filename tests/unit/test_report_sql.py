"""Generated SQL for the V2 reports (agent.md §8.4.3): placeholders only, validated times."""

from __future__ import annotations

import re

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories.plant.sql import report_sql as sql_module
from app.repositories.plant.sql.report_sql import build_gap_query, build_phase_production_query

WORKING = [{"from": "14:00:00", "to": "19:00:00"}, {"from": "19:30:00", "to": "22:30:00"}]
BREAKS = [{"from": "19:00:00", "to": "19:30:00"}]
OVERTIME = [{"from": "00:00:00", "to": "14:00:00"}, {"from": "22:30:00", "to": "23:59:59.999"}]


def _text(built: object) -> str:
    return str(built.sql.compile(dialect=postgresql.dialect()))  # type: ignore[attr-defined]


def test_phase_query_uses_bind_placeholders_not_values() -> None:
    built = build_phase_production_query(WORKING, BREAKS)
    sql = _text(built)
    assert built.time_params == {
        "w0_from": "14:00:00",
        "w0_to": "19:00:00",
        "w1_from": "19:30:00",
        "w1_to": "22:30:00",
        "b0_from": "19:00:00",
        "b0_to": "19:30:00",
    }
    for value in built.time_params.values():
        assert value not in sql
    assert sql.count("BETWEEN CAST(%(w") == 2 and sql.count("BETWEEN CAST(%(b") == 1
    assert "THEN 'normal'" in sql and "THEN 'break'" in sql and "ELSE 'overtime'" in sql
    assert "process_status NOT IN" in sql
    # the normal condition is checked before the break condition
    assert sql.index("'normal'") < sql.index("'break'")


def test_phase_query_without_windows_degrades_to_false() -> None:
    sql = _text(build_phase_production_query([], []))
    assert "WHEN FALSE THEN 'normal'" in sql and "WHEN FALSE THEN 'break'" in sql


@pytest.mark.parametrize("bad", ["19:00", "19:00:00'; DROP TABLE x;--", "7:00:00", "19:00:00.1234", ""])
def test_invalid_time_strings_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        build_phase_production_query([{"from": bad, "to": "20:00:00"}], BREAKS)
    with pytest.raises(ValueError):
        build_gap_query(WORKING, BREAKS, [{"from": "00:00:00", "to": bad}])


def test_gap_query_structure() -> None:
    built = build_gap_query(WORKING, BREAKS, OVERTIME)
    sql = _text(built)
    for cte in (
        "day_wise_data",
        "time_gaps",
        "grouped_gaps",
        "last_cyl_data",
        "end_gap_data",
        "intervening_gaps_data",
    ):
        assert f"{cte} AS" in sql
    assert "PARTITION BY system_id, process_day ORDER BY process_time" in sql
    assert "to_char(process_date, 'HH24:MI:SS.MS')" in sql
    assert sql.count("getGapBetweenTimes(") == 3  # 2 working + 1 break windows
    assert sql.count("getEndGapForPhase(") == 3
    assert "> '60 seconds'::interval" in sql
    assert "EXTRACT(EPOCH FROM" in sql and "/ 3600" in sql
    assert set(built.time_params) == {
        "w0_from", "w0_to", "w1_from", "w1_to", "b0_from", "b0_to",
        "o0_from", "o0_to", "o1_from", "o1_to",
    }  # fmt: skip
    # no literal clock values leaked into the statement text
    assert not re.search(r"\b\d{2}:\d{2}:\d{2}\b", sql.replace("HH24:MI:SS", ""))


def test_gap_query_overtime_requires_both_times_in_same_window() -> None:
    sql = _text(build_gap_query(WORKING, BREAKS, OVERTIME))
    assert "prev_process_time IS NOT NULL" in sql
    assert sql.count("prev_process_time::time BETWEEN") == 2


def test_gap_query_empty_break_list_uses_zero_interval() -> None:
    sql = _text(build_gap_query(WORKING, [], OVERTIME))
    assert "('0 seconds'::interval) AS break_gap" in sql


def test_v1_gap_sql_is_ported_with_lag_over_empty_window_d22() -> None:
    sql = str(sql_module.SQL_V1_GAP_HOURS)
    assert "LAG(process_date) OVER()" in sql
    assert "gap > :threshold_s" in sql and "sum(gap)/60/60 AS total_gap" in sql


def test_fixed_statements_have_no_literal_status_codes() -> None:
    for name in dir(sql_module):
        if not name.startswith("SQL_"):
            continue
        text = str(getattr(sql_module, name))
        for code in ("1296", "5392", "17424"):
            assert code not in text, f"{name} inlines {code}"
