"""IST conversions and window rules (agent.md §5.2, §8.3)."""

from __future__ import annotations

from datetime import datetime

from app.core.timeutils import (
    format_dt,
    from_epoch_local,
    ms_to_local,
    resolve_scale_window,
    to_epoch_seconds,
)

# 2026-01-05 08:30:00 UTC == 14:00:00 IST
EPOCH = int(datetime(2026, 1, 5, 8, 30, 0).replace(tzinfo=__import__("datetime").UTC).timestamp())


def test_epoch_seconds_to_naive_ist() -> None:
    assert from_epoch_local(EPOCH) == datetime(2026, 1, 5, 14, 0, 0)
    assert format_dt(from_epoch_local(EPOCH)) == "2026-01-05 14:00:00"


def test_ms_truncates_not_rounds() -> None:
    assert ms_to_local(EPOCH * 1000 + 999) == datetime(2026, 1, 5, 14, 0, 0)
    assert ms_to_local(EPOCH * 1000 - 1) == datetime(2026, 1, 5, 13, 59, 59)


def test_to_epoch_seconds_drops_microseconds() -> None:
    a = datetime(2026, 1, 5, 14, 0, 0, 999_999)
    b = datetime(2026, 1, 5, 14, 0, 5, 1)
    assert to_epoch_seconds(b) - to_epoch_seconds(a) == 5


def test_explicit_window_wins() -> None:
    start, end = resolve_scale_window(EPOCH, EPOCH + 3600, period_hours=12)
    assert (start, end) == (datetime(2026, 1, 5, 14, 0, 0), datetime(2026, 1, 5, 15, 0, 0))


def test_zero_period_means_local_midnight_to_now() -> None:
    now = datetime(2026, 1, 5, 16, 45, 12, 345)
    assert resolve_scale_window(0, 0, 0, now) == (
        datetime(2026, 1, 5, 0, 0, 0),
        datetime(2026, 1, 5, 16, 45, 12),
    )


def test_trailing_period() -> None:
    now = datetime(2026, 1, 5, 16, 0, 0)
    assert resolve_scale_window(0, 0, 3, now) == (datetime(2026, 1, 5, 13, 0, 0), now)


def test_one_missing_bound_falls_back_to_period() -> None:
    now = datetime(2026, 1, 5, 16, 0, 0)
    start, end = resolve_scale_window(EPOCH, 0, 2, now)
    assert (start, end) == (datetime(2026, 1, 5, 14, 0, 0), now)
