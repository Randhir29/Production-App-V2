"""SQL rule checks for the production_log repository (agent.md §8.4): binds and row mapping."""

from __future__ import annotations

import re
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import TextClause
from sqlalchemy.dialects import postgresql

from app.domain.constants import FILL_ACCEPTED_STATUSES, TARE_EXCLUDED
from app.repositories.plant import production_log_repo as repo_module
from app.repositories.plant.production_log_repo import ProductionLogRepository

FROM, TO = datetime(2026, 1, 5, 14, 0, 0), datetime(2026, 1, 5, 15, 0, 0)

ALL_STATEMENTS = {name: getattr(repo_module, name) for name in dir(repo_module) if name.startswith("SQL_")}


class CaptureSession:
    def __init__(self, *results: list[Any]) -> None:
        self._results = list(results)
        self.calls: list[tuple[TextClause, dict[str, Any]]] = []

    async def execute(self, stmt: TextClause, params: dict[str, Any]) -> list[Any]:
        self.calls.append((stmt, params))
        return self._results.pop(0) if self._results else []


@pytest.mark.parametrize("name", sorted(ALL_STATEMENTS))
def test_statements_are_text_with_named_binds_and_no_inline_values(name: str) -> None:
    stmt = ALL_STATEMENTS[name]
    assert isinstance(stmt, TextClause)
    compiled = str(stmt.compile(dialect=postgresql.dialect()))
    assert re.search(r"\bBETWEEN\b", compiled)
    # constant tuples are bound, never typed inline (§8.4.4)
    for forbidden in ("1296", "5392", "17424", "1040", "2064"):
        assert forbidden not in compiled, f"{name} inlines status {forbidden}"


def _binds(stmt: TextClause) -> set[str]:
    return set(stmt._bindparams)


async def test_filling_count_binds_every_parameter() -> None:
    session = CaptureSession(
        [SimpleNamespace(machine_id=1, system_id=1, device_id=101, cnt=7)],
        [SimpleNamespace(system_id=1, first_cyl_time=FROM, last_cyl_time=TO)],
    )
    counts, spans = await ProductionLogRepository(session).scale_filling_counts(FROM, TO, (1, 2))  # type: ignore[arg-type]
    assert [(c.machine_id, c.count) for c in counts] == [(1, 7)]
    assert spans[1].last_cyl_time == TO
    for stmt, params in session.calls:
        assert _binds(stmt) <= set(params)
    assert session.calls[0][1]["excluded"] == list(TARE_EXCLUDED)
    assert session.calls[0][1]["carousels"] == [1, 2]
    assert session.calls[0][1]["process_ids"] == [2, 22]


async def test_filling_count_skips_span_query_when_empty() -> None:
    session = CaptureSession([])
    counts, spans = await ProductionLogRepository(session).scale_filling_counts(FROM, TO, (1,))  # type: ignore[arg-type]
    assert counts == [] and spans == {} and len(session.calls) == 1


async def test_inaccurate_scales_uses_nominals_from_constants() -> None:
    row = SimpleNamespace(machine_id=1, system_id=1, device_id=101, total=10, hundred_plus=2)
    session = CaptureSession([row])
    out = await ProductionLogRepository(session).inaccurate_scales(FROM, TO)  # type: ignore[arg-type]
    assert out[0].hundred_plus == 2
    stmt, params = session.calls[0]
    assert _binds(stmt) <= set(params)
    assert (params["nominal_1"], params["nominal_2"]) == (14200, 19000)
    assert params["carousels"] == [1, 2]  # the carousel argument is ignored in legacy (§5.5.3)
    assert "process_status" not in str(stmt)  # all statuses


async def test_filling_statistics_params_and_mapping() -> None:
    row = SimpleNamespace(
        device_id=101, cnt=10, n200=1, n150=0, n100=2, n50=1, nil_var=3, p50=1, p100=1, p150=0,
        p200=1, average=-12.5, stddev=88.2,
    )  # fmt: skip
    session = CaptureSession([row])
    out = await ProductionLogRepository(session).filling_statistics(FROM, TO, 2, 2)  # type: ignore[arg-type]
    stmt, params = session.calls[0]
    assert _binds(stmt) <= set(params)
    assert params["nominal"] == 19000 and params["accepted"] == list(FILL_ACCEPTED_STATUSES)
    assert (out[0].scale_id, out[0].total, out[0].average, out[0].std_dev) == (101, 10, -12.5, 88.2)


async def test_accuracy_trend_passes_strings_through_as_bound_text() -> None:
    session = CaptureSession([])
    await ProductionLogRepository(session).accuracy_trend("2026-01-05 14:00:00", "2026-01-05 15:00", 1, 1)  # type: ignore[arg-type]
    stmt, params = session.calls[0]
    assert _binds(stmt) <= set(params)
    assert params["from_dt"] == "2026-01-05 14:00:00" and params["to_dt"] == "2026-01-05 15:00"
    assert (params["range"], params["bucket_min"], params["nominal"]) == (300, 15, 14200)


async def test_periodic_production_binds() -> None:
    session = CaptureSession([SimpleNamespace(period_start=FROM, c1_t1=1, c1_t2=2, c2_t1=3, c2_t2=4)])
    out = await ProductionLogRepository(session).periodic_production(FROM, TO, 30)  # type: ignore[arg-type]
    stmt, params = session.calls[0]
    assert _binds(stmt) <= set(params) and params["interval_min"] == 30
    assert "process_status" not in str(stmt)  # no status exclusion (§5.6)
    assert out[0].c2_t2 == 4
