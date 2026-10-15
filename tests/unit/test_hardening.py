"""P7 hardening: cache, plant engine cache and concurrency, query timing, load-test stats."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
import sqlalchemy as sa

from app.core.cache import TTLCache
from app.core.config import Settings
from app.core.db import PlantDatabases, _PlantEntry, attach_query_timing
from scripts.load_v2 import Sample, format_summary, percentile, summarise

# ---- TTLCache ------------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_cache_disabled_always_computes() -> None:
    cache = TTLCache(0)
    calls = 0

    async def compute() -> int:
        nonlocal calls
        calls += 1
        return calls

    assert [await cache.get_or_compute("k", compute) for _ in range(3)] == [1, 2, 3]
    assert not cache.enabled and len(cache) == 0


async def test_cache_hit_expiry_and_isolation() -> None:
    clock = Clock()
    cache = TTLCache(30, clock=clock)
    calls = 0

    async def compute() -> dict[str, list[int]]:
        nonlocal calls
        calls += 1
        return {"rows": [calls]}

    first = await cache.get_or_compute("k", compute)
    first["rows"].append(99)  # mutating the returned value must not poison the cache
    clock.now += 10
    assert await cache.get_or_compute("k", compute) == {"rows": [1]} and calls == 1
    clock.now += 21  # past the TTL
    assert await cache.get_or_compute("k", compute) == {"rows": [2]} and calls == 2


async def test_cache_single_flight_and_errors_not_cached() -> None:
    cache = TTLCache(30)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def slow() -> str:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "value"

    tasks = [asyncio.create_task(cache.get_or_compute("k", slow)) for _ in range(5)]
    await started.wait()
    release.set()
    assert await asyncio.gather(*tasks) == ["value"] * 5 and calls == 1

    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("boom")
        return "ok"

    with pytest.raises(RuntimeError):
        await cache.get_or_compute("e", flaky)
    assert await cache.get_or_compute("e", flaky) == "ok" and attempts == 2


async def test_cache_waiters_share_the_failure() -> None:
    cache = TTLCache(30)
    release = asyncio.Event()

    async def failing() -> str:
        await release.wait()
        raise ValueError("shared")

    first = asyncio.create_task(cache.get_or_compute("k", failing))
    await asyncio.sleep(0)
    second = asyncio.create_task(cache.get_or_compute("k", failing))
    await asyncio.sleep(0)
    release.set()
    for task in (first, second):
        with pytest.raises(ValueError, match="shared"):
            await task


async def test_cache_is_bounded_lru() -> None:
    cache = TTLCache(30, max_entries=2)
    for key in ("a", "b", "c"):
        await cache.get_or_compute(key, lambda k=key: _const(k))
    assert len(cache) == 2


async def _const(value: str) -> str:
    return value


# ---- PlantDatabases: engine cache, semaphore, no leaks ---------------------------------------


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = 0

    async def dispose(self) -> None:
        self.disposed += 1


class FakeSession:
    def __init__(self, tracker: dict[str, int], fail_connect: bool) -> None:
        self._tracker = tracker
        self._fail = fail_connect
        tracker["opened"] += 1

    async def connection(self) -> None:
        if self._fail:
            raise ConnectionRefusedError("down")
        await asyncio.sleep(0)

    async def close(self) -> None:
        self._tracker["closed"] += 1


class FakeEnginePlantDatabases(PlantDatabases):
    """Replaces real engines with fakes so no database is needed."""

    def __init__(self, settings: Settings, fail_connect: bool = False) -> None:
        super().__init__(settings)
        self.engines: list[FakeEngine] = []
        self.tracker = {"opened": 0, "closed": 0}
        self.fail_connect = fail_connect

    def _create_entry(self, url: Any) -> _PlantEntry:
        engine = FakeEngine()
        self.engines.append(engine)
        return _PlantEntry(
            engine=engine,  # type: ignore[arg-type]
            sessions=lambda: FakeSession(self.tracker, self.fail_connect),  # type: ignore[arg-type,return-value]
            semaphore=asyncio.Semaphore(self._settings.plant_max_concurrency),
        )


def _settings(**kw: Any) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        central_database_url="mysql+aiomysql://u:p@h/d",
        jwt_secret_key="k" * 40,
        **kw,
    )


def _target(n: int) -> dict[str, Any]:
    return {"host": f"10.0.0.{n}", "port": 5432, "dbname": "ccc", "user": "ro", "password": "pw"}


async def test_engine_is_reused_per_plant_and_lru_evicts_and_disposes() -> None:
    dbs = FakeEnginePlantDatabases(_settings(plant_engine_cache_max=2))
    for n in (1, 1, 2, 3):
        async with dbs.session(**_target(n)):
            pass
    assert len(dbs.engines) == 3  # plant 1 reused once, then plants 2 and 3 created
    assert dbs.engines[0].disposed == 1  # least recently used was evicted and disposed
    assert len(dbs._entries) == 2


async def test_ttl_eviction_disposes_stale_engines() -> None:
    dbs = FakeEnginePlantDatabases(_settings(plant_engine_ttl_s=60))
    async with dbs.session(**_target(1)):
        pass
    for entry in dbs._entries.values():
        entry.created_at -= 3600
    async with dbs.session(**_target(2)):
        pass
    assert dbs.engines[0].disposed == 1 and len(dbs._entries) == 1


async def test_semaphore_bounds_concurrency_per_plant() -> None:
    dbs = FakeEnginePlantDatabases(_settings(plant_max_concurrency=2))
    running = 0
    peak = 0

    async def worker() -> None:
        nonlocal running, peak
        async with dbs.session(**_target(1)):
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1

    await asyncio.gather(*(worker() for _ in range(8)))
    assert peak == 2


async def test_sessions_are_always_closed_even_on_errors() -> None:
    dbs = FakeEnginePlantDatabases(_settings())
    with pytest.raises(RuntimeError):
        async with dbs.session(**_target(1)):
            raise RuntimeError("query failed")
    assert dbs.tracker["opened"] == dbs.tracker["closed"] == 1

    failing = FakeEnginePlantDatabases(_settings(), fail_connect=True)
    for _ in range(3):
        with pytest.raises(ConnectionRefusedError):
            async with failing.session(**_target(1)):
                pass
    assert failing.tracker["opened"] == failing.tracker["closed"] == 3  # no leak on connect errors


async def test_failed_connect_releases_the_semaphore() -> None:
    dbs = FakeEnginePlantDatabases(_settings(plant_max_concurrency=1), fail_connect=True)
    for _ in range(3):  # would hang forever if a failure kept the single slot
        with pytest.raises(ConnectionRefusedError):
            async with dbs.session(**_target(1)):
                pass


async def test_invalidate_and_dispose() -> None:
    dbs = FakeEnginePlantDatabases(_settings())
    for n in (1, 2):
        async with dbs.session(**_target(n)):
            pass
    dbs.mark_bootstrapped("10.0.0.1", 5432, "ccc", "ro")
    await dbs.invalidate("10.0.0.1", 5432, "ccc", "ro")
    assert dbs.engines[0].disposed == 1 and not dbs.is_bootstrapped("10.0.0.1", 5432, "ccc", "ro")
    await dbs.dispose()
    assert dbs.engines[1].disposed == 1 and len(dbs._entries) == 0


# ---- query timing / slow-query logging -------------------------------------------------------


def _engine_with_timing(slow_ms: int) -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    attach_query_timing(engine, "plant", slow_ms)
    return engine


def test_slow_query_warns_without_parameters(caplog: pytest.LogCaptureFixture) -> None:
    engine = _engine_with_timing(slow_ms=0)
    with caplog.at_level(logging.DEBUG, logger="prodash.core.db"), engine.connect() as conn:
        conn.execute(sa.text("SELECT   :secret  AS\n  value"), {"secret": "TOP-SECRET-VALUE"})
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "slow query engine=plant" in message and "SELECT ? AS value" in message
    assert "TOP-SECRET-VALUE" not in caplog.text


def test_fast_query_is_debug_only(caplog: pytest.LogCaptureFixture) -> None:
    engine = _engine_with_timing(slow_ms=60_000)
    with caplog.at_level(logging.DEBUG, logger="prodash.core.db"), engine.connect() as conn:
        conn.execute(sa.text("SELECT 1"))
    levels = {r.levelno for r in caplog.records if "query engine=plant" in r.getMessage()}
    assert levels == {logging.DEBUG}


def test_statement_label_is_truncated() -> None:
    engine = _engine_with_timing(slow_ms=0)
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = Capture()
    logger = logging.getLogger("prodash.core.db")
    logger.addHandler(handler)
    try:
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT " + ", ".join(f"{i} AS c{i}" for i in range(60))))
    finally:
        logger.removeHandler(handler)
    statement = records[0].split("statement=", 1)[1]
    assert len(statement) <= 80


# ---- load-test helpers ---------------------------------------------------------------------------


def test_percentile_nearest_rank() -> None:
    values = [float(i) for i in range(1, 101)]
    assert percentile(values, 50) == 50.0
    assert percentile(values, 95) == 95.0
    assert percentile([3.0], 95) == 3.0
    assert percentile([], 95) == 0.0


def test_summary_counts_failures_and_formats() -> None:
    samples = [
        Sample("a", 200, 1.0),
        Sample("b", 200, 3.0),
        Sample("c", 502, 0.5),
        Sample("d", 0, 9.0),
    ]
    summary = summarise(samples, wall_seconds=9.5)
    assert summary.total == 4 and summary.ok == 2 and summary.error_rate == 0.5
    assert summary.by_status == {200: 2, 502: 1, 0: 1}
    assert summary.slowest == 9.0
    text = format_summary(summary, samples)
    assert "c(502)" in text and "d(timeout)" in text and "error_rate=50.0%" in text
