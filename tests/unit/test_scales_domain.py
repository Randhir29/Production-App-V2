"""Scale efficiency/accuracy maths (agent.md §5.5.1-§5.5.4, D-01, D-05, D-15)."""

from __future__ import annotations

from datetime import datetime

from app.domain.php_compat import php_usort
from app.domain.scales import (
    AccuracyCount,
    CarouselSpan,
    ScaleCount,
    compute_accuracies,
    compute_efficiencies,
    efficiency_tag,
    pivot_efficiency_rows,
    scale_id,
    sort_under_performing,
)
from app.domain.static_config import StaticCarousel
from app.services.scales import carousels_for_filter

T0 = datetime(2026, 1, 5, 14, 0, 0)
T1 = datetime(2026, 1, 5, 15, 0, 0)  # exactly one hour later
SPEED = {1: 50, 2: 48}
ANANTAPUR = {1: StaticCarousel(heads=72, stdOutput=3000), 2: StaticCarousel(heads=24, stdOutput=1500)}


def _counts() -> list[ScaleCount]:
    return [
        ScaleCount(machine_id=1, system_id=1, device_id=101, count=40),
        ScaleCount(machine_id=2, system_id=1, device_id=102, count=20),
        ScaleCount(machine_id=1, system_id=2, device_id=201, count=10),
    ]


SPANS = {1: CarouselSpan(T0, T1), 2: CarouselSpan(T0, T1)}


def test_configured_plant_expected_count_per_head() -> None:
    res = compute_efficiencies(_counts(), SPANS, 3600, ANANTAPUR, SPEED, uses_device_id=False)
    # stdOutput = 3000 * (3600/3600) / 72 heads for carousel 1; 1500 * 1 / 24 for carousel 2
    assert res.std_output[1] == 3000 / 72
    assert res.std_output[2] == 1500 / 24
    first = res.rows[0]
    assert first["efficiency"] == 40 / (3000 / 72)
    assert first["stdOutput"] == 3000 / 72 and first["interval"] == 3600
    assert list(first) == [
        "system_id", "machine_id", "device_id", "count", "scale", "carousal",
        "efficiency", "stdOutput", "interval",
    ]  # fmt: skip
    # meta: sum(count) / (stdOutput * heads)
    assert res.meta["car1Eff"] == 60 / ((3000 / 72) * 72)
    assert res.meta["car2Eff"] == 10 / ((1500 / 24) * 24)


def test_unconfigured_plant_uses_carousal_speed_and_24_heads() -> None:
    res = compute_efficiencies(_counts(), SPANS, 3600, None, SPEED, uses_device_id=False)
    assert res.std_output[1] == (1 / 50) * 3600
    assert res.std_output[2] == (1 / 48) * 3600
    assert res.rows[0]["efficiency"] == 40 / ((1 / 50) * 3600)
    assert res.meta["car1Eff"] == 60 / (((1 / 50) * 3600) * 24)


def test_interval_is_min_of_window_and_observed_span() -> None:
    short_span = {1: CarouselSpan(T0, datetime(2026, 1, 5, 14, 30, 0)), 2: CarouselSpan(T0, T1)}
    res = compute_efficiencies(_counts(), short_span, 3600, None, SPEED, False)
    assert res.interval == {1: 1800, 2: 3600}
    narrow_window = compute_efficiencies(_counts(), SPANS, 600, None, SPEED, False)
    assert narrow_window.interval == {1: 600, 2: 600}


def test_milliseconds_in_span_are_truncated() -> None:
    spans = {
        1: CarouselSpan(datetime(2026, 1, 5, 14, 0, 0, 900_000), datetime(2026, 1, 5, 14, 0, 10, 100_000))
    }
    res = compute_efficiencies([ScaleCount(1, 1, 1, 5)], spans, 3600, None, SPEED, False)
    assert res.interval[1] == 10  # 14:00:10 - 14:00:00, fractions dropped on both sides


def test_scale_identifier_rule() -> None:
    assert scale_id(3, 301, uses_device_id=False) == 3
    assert scale_id(3, 301, uses_device_id=True) == 301
    res = compute_efficiencies(_counts(), SPANS, 3600, None, SPEED, uses_device_id=True)
    assert [r["scale"] for r in res.rows] == [101, 102, 201]


def test_no_data_gives_empty_rows_and_zero_meta() -> None:
    res = compute_efficiencies([], {}, 3600, None, SPEED, False)
    assert res.rows == [] and res.meta == {"car1Eff": 0, "car2Eff": 0}


def test_unconfigured_carousel_in_configured_plant_is_guarded_not_crashing() -> None:
    """D-05: stdOutput 0 would raise DivisionByZeroError in PHP 8."""
    only_c1 = {1: StaticCarousel(heads=72, stdOutput=3000)}
    res = compute_efficiencies(_counts(), SPANS, 3600, only_c1, SPEED, False)
    c2_row = next(r for r in res.rows if r["system_id"] == 2)
    assert c2_row["efficiency"] == 0.0 and c2_row["stdOutput"] == 0.0
    assert res.meta["car2Eff"] == 0
    assert any("D-05" in w for w in res.warnings)


def test_zero_length_interval_is_guarded() -> None:
    spans = {1: CarouselSpan(T0, T0), 2: CarouselSpan(T0, T0)}
    res = compute_efficiencies(_counts(), spans, 3600, None, SPEED, False)
    assert all(r["efficiency"] == 0.0 for r in res.rows)
    assert res.warnings


def test_meta_only_for_requested_carousels() -> None:
    res = compute_efficiencies(
        _counts()[:2], {1: SPANS[1]}, 3600, ANANTAPUR, SPEED, False, meta_carousels=(1,)
    )
    assert res.meta["car1Eff"] > 0 and res.meta["car2Eff"] == 0


# ---- sorting (D-15) ------------------------------------------------------------------


def _row(eff: float, tag: str) -> dict[str, float | str]:
    return {"efficiency": eff, "id": tag}


def test_comparator_truncates_so_close_values_keep_original_order() -> None:
    rows = [_row(0.504, "A"), _row(0.500, "B"), _row(0.496, "C")]
    ordered = sort_under_performing(rows, 10)
    assert [r["id"] for r in ordered] == ["A", "B", "C"]  # a strict sort would give C, B, A


def test_sort_ascending_and_slice() -> None:
    rows = [_row(1.2, "hi"), _row(0.4, "lo"), _row(0.9, "mid")]
    assert [r["id"] for r in sort_under_performing(rows, 2)] == ["lo", "mid"]


def test_php_usort_factor_and_stability() -> None:
    items = [(0.001, "a"), (0.0, "b")]
    assert php_usort(items, lambda x: x[0], 100) == items  # difference 0.1 -> 0 -> equal
    assert php_usort(items, lambda x: x[0], 10_000) == [(0.0, "b"), (0.001, "a")]


def test_efficiency_tag_thresholds() -> None:
    assert efficiency_tag(0.75) == "below-average"
    assert efficiency_tag(0.7501) == "average"
    assert efficiency_tag(1.0) == "average"
    assert efficiency_tag(1.0001) == "above-average"


# ---- accuracy ------------------------------------------------------------------------


def test_accuracy_rows_meta_and_sort() -> None:
    rows = [
        AccuracyCount(machine_id=1, system_id=1, device_id=101, total=100, hundred_plus=10),
        AccuracyCount(machine_id=2, system_id=1, device_id=102, total=200, hundred_plus=2),
        AccuracyCount(machine_id=1, system_id=2, device_id=201, total=50, hundred_plus=25),
    ]
    out, meta = compute_accuracies(rows, 3600, uses_device_id=True, row_count=2)
    assert [r["scale"] for r in out] == [201, 101]  # 0.5 then 0.9; the 0.99 row is sliced off
    assert out[0]["accuracy"] == 0.5 and out[0]["interval"] == 3600
    assert meta["car1Acc"] == 1 - 12 / 300
    assert meta["car2Acc"] == 1 - 25 / 50


def test_accuracy_meta_for_carousel_without_rows_is_one() -> None:
    """1 - 0 / max(0, 1): preserved legacy formula."""
    rows = [AccuracyCount(1, 1, 101, 10, 0)]
    _, meta = compute_accuracies(rows, 60, False, 5)
    assert meta == {"car1Acc": 1.0, "car2Acc": 1.0}


def test_accuracy_no_data() -> None:
    assert compute_accuracies([], 60, False, 5) == ([], {"car1Acc": 0, "car2Acc": 0})


def test_accuracy_sort_truncates_below_one_basis_point() -> None:
    rows = [
        AccuracyCount(1, 1, 1, 100_000, 10),  # 0.9999
        AccuracyCount(2, 1, 2, 100_000, 9),  # 0.99991 (difference 0.00001 -> int(0.1) = 0)
    ]
    out, _ = compute_accuracies(rows, 1, False, 5)
    assert [r["machine_id"] for r in out] == [1, 2]


# ---- pivot and filters ---------------------------------------------------------------


def test_pivot_first_seen_order_and_missing_carousel_keys() -> None:
    res = compute_efficiencies(
        [
            ScaleCount(1, 1, 101, 40),
            ScaleCount(2, 1, 102, 20),
            ScaleCount(1, 2, 201, 10),  # same scale 1 as the first row, other carousel
            ScaleCount(7, 2, 207, 5),
        ],
        SPANS,
        3600,
        ANANTAPUR,
        SPEED,
        False,
    )
    table = pivot_efficiency_rows(res.rows)
    assert [r["scaleId"] for r in table] == [1, 2, 7]
    assert set(table[0]) == {"scaleId", "car1Count", "car1Percent", "car2Count", "car2Percent"}
    assert set(table[1]) == {"scaleId", "car1Count", "car1Percent"}
    assert set(table[2]) == {"scaleId", "car2Count", "car2Percent"}
    assert table[0]["car2Count"] == 10
    assert table[0]["car1Percent"] == res.rows[0]["efficiency"]


def test_carousel_filter_mapping() -> None:
    assert carousels_for_filter(0) == (1, 2)
    assert carousels_for_filter(1) == (1,)
    assert carousels_for_filter(2) == (2,)
    assert carousels_for_filter(7) == (1, 2)
