"""Report building blocks, productivity and rejection aggregation (agent.md §5.7.1-§5.7.6)."""

from __future__ import annotations

from datetime import date, datetime

from app.domain.rejections import cs_rejection_report, gdpt_report, rejection_percent
from app.domain.report_calcs import (
    BottlingRow,
    CylTimeRow,
    bottling_summary,
    combine_gap_totals,
    first_last_cyl_times,
    pg_timestamp_text,
    phase_production,
    production_hours_summary,
    productivity_v1,
    productivity_v2,
)

T0 = datetime(2026, 1, 5, 6, 0, 0)
T1 = datetime(2026, 1, 5, 14, 30, 0)


# ---- bottling / first-last ------------------------------------------------------------


def test_bottling_summary_shape() -> None:
    out = bottling_summary([BottlingRow(1, 100, 20), BottlingRow(2, 50, 0)])
    assert out == {
        1: {"carousal": 1, "production_14_2": 100, "production_19": 20},
        2: {"carousal": 2, "production_14_2": 50, "production_19": 0},
    }


def test_bottling_summary_none_when_empty_or_first_row_zero_d25() -> None:
    assert bottling_summary([]) is None
    # D-25: only the FIRST row decides, even though carousel 2 has production
    assert bottling_summary([BottlingRow(1, 0, 0), BottlingRow(2, 80, 5)]) is None
    # a later zero row does not null the summary
    assert bottling_summary([BottlingRow(1, 10, 0), BottlingRow(2, 0, 0)]) is not None


def test_first_last_times_and_total_hours() -> None:
    out = first_last_cyl_times([CylTimeRow(1, T1, T0)], [1, 2])
    assert out is not None
    assert out[1]["total_hours"] == 8.5
    assert out[1]["last_cyl_time"] == "2026-01-05 14:30:00"
    assert out[1]["first_cyl_time"] == "2026-01-05 06:00:00"
    assert out[2] == {}  # carousel without rows
    assert first_last_cyl_times([], [1]) is None


def test_total_hours_truncates_fractions_per_endpoint() -> None:
    first = datetime(2026, 1, 5, 6, 0, 0, 900_000)
    last = datetime(2026, 1, 5, 7, 0, 0, 100_000)
    out = first_last_cyl_times([CylTimeRow(1, last, first)], [1])
    assert out is not None and out[1]["total_hours"] == 1.0


def test_pg_timestamp_text_matches_postgres_output() -> None:
    assert pg_timestamp_text(datetime(2026, 1, 5, 6, 0, 0)) == "2026-01-05 06:00:00"
    assert pg_timestamp_text(datetime(2026, 1, 5, 6, 0, 0, 500_000)) == "2026-01-05 06:00:00.5"
    assert pg_timestamp_text(datetime(2026, 1, 5, 6, 0, 0, 123_456)) == "2026-01-05 06:00:00.123456"


def test_production_hours_summary_merges_first_last() -> None:
    fl = first_last_cyl_times([CylTimeRow(1, T1, T0)], [1, 2])
    out = production_hours_summary({1: 0.5, 2: None}, fl)
    assert out[1]["total_gap"] == 0.5
    assert out[1]["total_hours"] == 8.5 and out[1]["carousal"] == 1
    assert out[1]["first_cyl_time"] == "2026-01-05 06:00:00"
    assert out[2] == {"total_gap": None, "carousal": 2}


# ---- productivity V1 --------------------------------------------------------------------


def test_productivity_v1_uses_equivalence_and_net_hours() -> None:
    bottling = {1: {"carousal": 1, "production_14_2": 1000, "production_19": 400}}
    hours = {1: {"total_hours": 8.5, "total_gap": 0.5}}
    out = productivity_v1(bottling, hours)
    assert out[1]["total_production"] == 1000 + 1.25 * 400
    assert out[1]["net_hours"] == 8.0
    assert out[1]["productivity"] == 1500 / 8.0


def test_productivity_v1_null_gap_counts_as_zero_and_bottling_none() -> None:
    bottling = {1: {"carousal": 1, "production_14_2": 80, "production_19": 0}}
    out = productivity_v1(bottling, {1: {"total_hours": 4.0, "total_gap": None}})
    assert out[1]["net_hours"] == 4.0 and out[1]["productivity"] == 20.0
    assert productivity_v1(None, {}) == {}


def test_productivity_v1_zero_net_hours_is_guarded_d05() -> None:
    bottling = {1: {"carousal": 1, "production_14_2": 80, "production_19": 0}}
    warnings: list[str] = []
    out = productivity_v1(bottling, {1: {"total_hours": 2.0, "total_gap": 2.0}}, warnings)
    assert out[1]["productivity"] == 0.0
    assert any("D-05" in w for w in warnings)


# ---- productivity V2 --------------------------------------------------------------------


def _bottling2() -> dict[int, dict[str, dict[str, int]]]:
    return {
        1: {
            "normal": {"prod_14_2": 800, "prod_19": 80},
            "break": {"prod_14_2": 10, "prod_19": 0},
            "overtime": {"prod_14_2": 100, "prod_19": 40},
        }
    }


def test_productivity_v2_per_phase() -> None:
    hours2 = {
        1: {
            "max_op_hours": {"normal": 8.0, "break": 0.5},
            "total_normal_gap": 1.0,
            "total_break_gap": 0.1,
            "total_overtime_gap": 0.25,
            "total_pre_shift_time": 1.0,
            "total_post_shift_time": 0.75,
        }
    }
    out = productivity_v2(_bottling2(), hours2)[1]
    assert out["normal"] == {
        "total_production": 800 + 1.25 * 80,
        "net_hours": 7.0,
        "productivity": 900 / 7.0,
    }
    assert out["break"]["net_hours"] == 0.4
    assert out["overtime"]["net_hours"] == 1.0 + 0.75 - 0.25
    assert out["overtime"]["total_production"] == 150


def test_productivity_v2_zero_net_is_zero_and_negative_net_allowed() -> None:
    hours2 = {
        1: {
            "max_op_hours": {"normal": 1.0, "break": 0.5},
            "total_normal_gap": 1.0,  # net 0 -> productivity 0
            "total_break_gap": 0.75,  # net -0.25 -> negative productivity kept
            "total_overtime_gap": None,
            "total_pre_shift_time": None,
            "total_post_shift_time": None,
        }
    }
    out = productivity_v2(_bottling2(), hours2)[1]
    assert out["normal"]["productivity"] == 0
    assert out["break"]["net_hours"] == -0.25 and out["break"]["productivity"] == 10 / -0.25
    assert out["overtime"]["net_hours"] == 0 and out["overtime"]["productivity"] == 0


# ---- §5.7.5 combination ------------------------------------------------------------------


def test_combine_gap_totals() -> None:
    out = combine_gap_totals(
        1,
        {"total_normal_gap": 2.0, "total_break_gap": None, "total_overtime_gap": 0.5},
        non_op_days=1,
        from_date=date(2026, 1, 1),
        to_date=date(2026, 1, 4),
        daily_hours={"normal": 8.0, "break": 0.5},
        ot={"total_pre_shift_time": 1.5, "total_post_shift_time": None},
    )
    assert out["intervening_days"] == 4 and out["non_op_days"] == 1 and out["net_op_days"] == 3
    assert out["max_op_hours"] == {"normal": 24.0, "break": 1.5}
    assert out["net_op_hours"] == {"normal": 22.0, "break": 1.5}
    assert out["total_pre_shift_time"] == 1.5 and out["total_post_shift_time"] is None


def test_phase_production_defaults_missing_phases() -> None:
    out = phase_production([("normal", 10, 2)])
    assert out["normal"] == {"phase": "normal", "prod_14_2": 10, "prod_19": 2}
    assert out["break"] == {"prod_14_2": 0, "prod_19": 0}
    assert out["overtime"] == {"prod_14_2": 0, "prod_19": 0}


# ---- rejections --------------------------------------------------------------------------


def test_cs_report_every_bucket() -> None:
    rows = [
        (0, 1, 900),
        (1040, 1, 10),  # underfilled
        (2064, 1, 5),  # overfilled
        (1296, 1, 2),  # negative tare
        (5392, 1, 1),  # negative tare
        (17424, 1, 3),  # positive tare
        (1048, 1, 4),  # timeout
        (4120, 1, 6),  # timeout
        (1041, 1, 7),  # other
        (6160, 1, 8),  # other
        (-5, 1, 9),  # comm error (not a sort-out status)
        (4096, 1, 11),  # comm error
    ]
    out = cs_rejection_report(rows, [1])[1]
    assert out == {
        "total": 966,
        "cylFilled": 966 - 46,
        "underfilled": 10,
        "overfilled": 5,
        "ntare": 3,
        "ptare": 3,
        "timeout": 10,
        "other": 15,
        "totalSortout": 10 + 5 + 2 + 1 + 3 + 4 + 6 + 7 + 8,
        "commErrorSortout": 20,
    }
    assert all(isinstance(v, int) for v in out.values())


def test_cs_report_carousel_without_rows_is_zeroed_and_unknown_carousel_ignored() -> None:
    out = cs_rejection_report([(0, 1, 5), (0, 9, 99)], [1, 2])
    assert out[2] == dict.fromkeys(out[2], 0)
    assert out[1]["total"] == 5 and 9 not in out


def test_gdpt_report_false_when_empty_and_absent_carousels() -> None:
    assert gdpt_report([]) is False
    out = gdpt_report([(2, 90, 10, 100)])
    assert out == {2: {"carousal": 2, "accepted": 90, "rejected": 10, "total": 100}}


def test_rejection_percent_guards() -> None:
    assert rejection_percent({"rejected": 1, "total": 8}, "rejected") == 12.5
    assert rejection_percent({"rejected": 1, "total": 0}, "rejected") == 0.0
    assert rejection_percent(None, "rejected") == 0.0
    assert rejection_percent(False, "rejected") == 0.0
