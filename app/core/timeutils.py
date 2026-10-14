"""IST time conversions with PHP semantics (agent.md §5.2, §8.3). Plant timestamps are naive."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.domain.constants import TZ

IST = ZoneInfo(TZ)
DT_FORMAT = "%Y-%m-%d %H:%M:%S"


def now_local() -> datetime:
    """Current time in Asia/Kolkata as a naive datetime."""
    return datetime.now(IST).replace(tzinfo=None)


def from_epoch_local(seconds: float) -> datetime:
    """Epoch seconds -> naive Asia/Kolkata datetime (whole seconds)."""
    return datetime.fromtimestamp(int(seconds), IST).replace(tzinfo=None)


def ms_to_local(milliseconds: float) -> datetime:
    """Form fields in ms: divide by 1000 and truncate to an int second (PHP ``date()``)."""
    return from_epoch_local(int(milliseconds / 1000))


def to_epoch_seconds(value: datetime) -> int:
    """Naive datetime -> int seconds, dropping microseconds (PHP ``strtotime``).

    Both operands of every difference are converted the same way, so the zone cancels out.
    """
    return int(value.replace(microsecond=0, tzinfo=UTC).timestamp())


def format_dt(value: datetime) -> str:
    """``'YYYY-MM-DD HH:MM:SS'``."""
    return value.strftime(DT_FORMAT)


def resolve_scale_window(
    from_ts: int, to_ts: int, period_hours: int, now: datetime | None = None
) -> tuple[datetime, datetime]:
    """Window rule for the scales endpoints (§5.2).

    Both ``from`` and ``to`` non-zero: use them (epoch seconds). Otherwise ``to`` is now and
    ``from`` is today 00:00 local when ``period == 0`` or ``now - period*3600``.
    """
    if from_ts and to_ts:
        return from_epoch_local(from_ts), from_epoch_local(to_ts)
    current = (now or now_local()).replace(microsecond=0)
    if period_hours == 0:
        start = current.replace(hour=0, minute=0, second=0)
    else:
        start = current - timedelta(hours=period_hours)
    return start, current
