"""Rejection aggregation (agent.md §5.3.3, §5.7.1). Pure, no I/O."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from app.domain.constants import (
    CS_NTARE,
    CS_OTHER_ERR_STATUSES,
    CS_OVERFILLED,
    CS_PTARE,
    CS_SORTOUT_STATUSES,
    CS_TIMEOUT,
    CS_UNDERFILLED,
)

SORTOUT_STATUSES = frozenset(CS_SORTOUT_STATUSES) | frozenset(CS_OTHER_ERR_STATUSES)
COMM_ERROR_STATUS = 4096


def is_comm_error(status: int) -> bool:
    """commErrorSortout: ``process_status < 0`` or ``== 4096``."""
    return status < 0 or status == COMM_ERROR_STATUS


def cs_rejection_report(
    rows: Iterable[tuple[int, int, int]], carousels: Sequence[int]
) -> dict[int, dict[str, int]]:
    """§5.7.1 ``check_scale_rejection_report``: one all-int dict per DB carousel.

    ``rows`` are ``(process_status, system_id, count)``. Carousels with no rows get zeros.
    """
    counts: dict[int, dict[int, int]] = {c: {} for c in carousels}
    for status, system_id, count in rows:
        if system_id in counts:
            counts[system_id][status] = counts[system_id].get(status, 0) + count

    report: dict[int, dict[str, int]] = {}
    for carousel in carousels:
        by_status = counts[carousel]
        total = sum(by_status.values())
        sortout = sum(n for s, n in by_status.items() if s in SORTOUT_STATUSES)
        report[carousel] = {
            "total": total,
            "cylFilled": total - sortout,
            "underfilled": by_status.get(CS_UNDERFILLED, 0),
            "overfilled": by_status.get(CS_OVERFILLED, 0),
            "ntare": sum(by_status.get(s, 0) for s in CS_NTARE),
            "ptare": sum(by_status.get(s, 0) for s in CS_PTARE),
            "timeout": sum(by_status.get(s, 0) for s in CS_TIMEOUT),
            "other": sum(by_status.get(s, 0) for s in CS_OTHER_ERR_STATUSES),
            "totalSortout": sortout,
            "commErrorSortout": sum(n for s, n in by_status.items() if is_comm_error(s)),
        }
    return report


def gdpt_report(
    rows: Iterable[tuple[int, int, int, int]],
) -> dict[int, dict[str, int]] | bool:
    """§5.7.1 ``gdpt_rejection_report``: ``{carousel: {carousal, accepted, rejected, total}}``.

    ``rows`` are ``(system_id, accepted, rejected, total)``. Carousels without rows are absent
    and the result is ``False`` when there are no rows at all (legacy ``false``).
    """
    report = {
        system_id: {"carousal": system_id, "accepted": accepted, "rejected": rejected, "total": total}
        for system_id, accepted, rejected, total in rows
    }
    return report or False


def rejection_percent(entry: Any, numerator_key: str) -> float:
    """``numerator / total * 100`` as a float, or 0 when absent or ``total`` is 0 (§5.7.8)."""
    if not entry:
        return 0.0
    total: float = entry.get("total", 0)
    if not total:
        return 0.0
    return float(entry.get(numerator_key, 0)) / total * 100
