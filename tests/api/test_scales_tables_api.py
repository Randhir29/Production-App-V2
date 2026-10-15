"""E19-E24 over HTTP: v1 and legacy payload shapes with a scripted plant DB (agent.md §9.4)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import deps
from app.core.config import Settings
from app.domain.static_config import parse_static_config
from app.main import create_app
from app.models import Plant
from tests.repositories.conftest import make_engine
from tests.unit.fakes import FakePlantDatabases, FakeResult

STATIC = parse_static_config(
    """
plants: {anantapur: Anantapur, kochi: Kochi}
plant_name: X
carousal_speed: {1: 50, 2: 48}
interruption_to_exclude_minutes: 5
plants_with_device_id: [anantapur]
carousal_config:
  anantapur:
    1: {heads: 72, stdOutput: 3000}
    2: {heads: 24, stdOutput: 1500}
"""
)

T0 = datetime(2026, 1, 5, 14, 0, 0)
T1 = datetime(2026, 1, 5, 15, 0, 0)

FROM_S = int(datetime(2026, 1, 5, 8, 30, tzinfo=UTC).timestamp())  # 14:00:00 IST
TO_S = FROM_S + 3600
FROM_MS, TO_MS = FROM_S * 1000, TO_S * 1000


class ScriptedSession:
    """Returns canned rows chosen by the SQL text; records the params it received."""

    def __init__(self) -> None:
        self.rows: dict[str, list[SimpleNamespace]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: Any = None) -> FakeResult:
        text = str(stmt)
        self.calls.append((text, params or {}))
        if "hundred_plus" in text:
            key = "inaccurate"
        elif "first_cyl_time" in text:
            key = "span"
        elif "STDDEV_POP" in text:
            key = "stats"
        elif "date_trunc('minute'" in text:
            key = "trend"
        elif "period_start" in text:
            key = "periodic"
        elif "AS cnt" in text:
            key = "counts"
        else:
            raise AssertionError(text)
        return FakeResult(self.rows.get(key, []))  # type: ignore[arg-type]


def row(**kw: Any) -> SimpleNamespace:
    return SimpleNamespace(**kw)


@pytest.fixture
async def env() -> AsyncIterator[tuple[httpx.AsyncClient, ScriptedSession]]:
    engine = await make_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as seed:
        seed.add_all(
            [
                Plant(id=1, short_name="anantapur", plant_name="A", zone="S", host_ip="10.0.0.1",
                      db_user="ro", db_password="pw", db_database="ccc"),
                Plant(id=2, short_name="kochi", plant_name="K", zone="S", host_ip="10.0.0.2",
                      db_user="ro", db_password="pw", db_database="ccc"),
            ]
        )  # fmt: skip
        await seed.commit()

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        central_database_url="mysql+aiomysql://u:p@h/d",
        jwt_secret_key="k" * 40,
        auth_enabled=False,
    )
    app = create_app(settings)

    async def central() -> AsyncIterator[AsyncSession]:
        async with sessions() as s:
            yield s

    scripted = ScriptedSession()
    dbs = FakePlantDatabases(scripted)  # type: ignore[arg-type]
    app.dependency_overrides[deps.get_app_settings] = lambda: settings
    app.dependency_overrides[deps.get_central_session] = central
    app.dependency_overrides[deps.get_static_config] = lambda: STATIC
    app.dependency_overrides[deps.get_plant_databases] = lambda: dbs

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c, scripted
    await engine.dispose()


def _counts(s: ScriptedSession) -> None:
    s.rows["counts"] = [
        row(machine_id=1, system_id=1, device_id=101, cnt=40),
        row(machine_id=2, system_id=1, device_id=102, cnt=20),
        row(machine_id=1, system_id=2, device_id=201, cnt=10),
    ]
    s.rows["span"] = [
        row(system_id=1, first_cyl_time=T0, last_cyl_time=T1),
        row(system_id=2, first_cyl_time=T0, last_cyl_time=T1),
    ]


# ---- E19 -----------------------------------------------------------------------------


async def test_legacy_under_performing_shape_and_order(env: Any) -> None:
    client, scripted = env
    _counts(scripted)
    r = await client.get(f"/prodash/getUnderPerformingScales/anantapur/2/0/{FROM_S}/{TO_S}")
    body = r.json()
    assert set(body) == {"rows", "meta"} and len(body["rows"]) == 2
    first = body["rows"][0]
    assert set(first) == {
        "system_id", "machine_id", "device_id", "count", "scale", "carousal",
        "efficiency", "stdOutput", "interval",
    }  # fmt: skip
    assert first["scale"] == first["device_id"]  # anantapur uses device ids
    assert body["rows"][0]["efficiency"] <= body["rows"][1]["efficiency"]
    assert set(body["meta"]) == {"car1Eff", "car2Eff"}
    # explicit window bounds were converted to naive IST datetimes
    _, params = scripted.calls[0]
    assert params["from_dt"] == T0 and params["to_dt"] == T1


async def test_v1_under_performing_has_tags_and_snake_case(env: Any) -> None:
    client, scripted = env
    _counts(scripted)
    r = await client.get(
        "/api/v1/plants/anantapur/scales/under-performing",
        params={"row_count": 3, "from_ts": FROM_S, "to_ts": TO_S},
    )
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert {"std_output", "tag"} <= set(rows[0])
    assert {x["tag"] for x in rows} <= {"below-average", "average", "above-average"}
    assert set(r.json()["meta"]) == {"car1_eff", "car2_eff"}


async def test_under_performing_no_data(env: Any) -> None:
    client, _ = env
    r = await client.get(f"/prodash/getUnderPerformingScales/kochi/10/0/{FROM_S}/{TO_S}")
    assert r.json() == {"rows": [], "meta": {"car1Eff": 0, "car2Eff": 0}}


async def test_legacy_unknown_plant_gives_database_exception_marker(env: Any) -> None:
    client, _ = env
    r = await client.get(f"/prodash/getUnderPerformingScales/nowhere/10/0/{FROM_S}/{TO_S}")
    assert r.status_code == 200
    assert "DatabaseException" in r.json()["exception_type"]


async def test_v1_unknown_plant_is_404_envelope(env: Any) -> None:
    client, _ = env
    r = await client.get("/api/v1/plants/nowhere/scales/under-performing")
    assert r.status_code == 404 and r.json()["error"]["code"] == "PLANT_NOT_FOUND"


async def test_v1_query_validation(env: Any) -> None:
    client, _ = env
    bad = [
        ("/api/v1/plants/kochi/scales/under-performing", {"row_count": 0}),
        ("/api/v1/plants/kochi/scales/under-performing", {"from_ts": 10, "to_ts": 5}),
        ("/api/v1/plants/kochi/tables/scale-efficiency", {"from_ms": 1, "to_ms": 2, "carousel": 3}),
        (
            "/api/v1/plants/kochi/tables/filling-statistics",
            {"from_ms": 1, "to_ms": 2, "carousel": 1, "cyl_type": 3},
        ),
        ("/api/v1/plants/kochi/tables/periodic-production", {"from_ms": 1, "to_ms": 2, "interval_min": 0}),
    ]
    for path, params in bad:
        assert (await client.get(path, params=params)).status_code == 422, (path, params)


# ---- E20 -----------------------------------------------------------------------------


async def test_inaccurate_scales(env: Any) -> None:
    client, scripted = env
    scripted.rows["inaccurate"] = [
        row(machine_id=1, system_id=1, device_id=101, total=100, hundred_plus=10),
        row(machine_id=2, system_id=1, device_id=102, total=100, hundred_plus=50),
    ]
    r = await client.get(f"/prodash/getInaccurateScales/kochi/1/0/{FROM_S}/{TO_S}")
    body = r.json()
    assert len(body["rows"]) == 1  # rowCount slices
    assert body["rows"][0]["accuracy"] == 0.5 and body["rows"][0]["interval"] == 3600
    assert body["rows"][0]["scale"] == 2  # kochi: machine_id
    assert set(body["meta"]) == {"car1Acc", "car2Acc"}


async def test_inaccurate_row_count_zero_falls_back_to_default_five(env: Any) -> None:
    client, scripted = env
    scripted.rows["inaccurate"] = [
        row(machine_id=i, system_id=1, device_id=i, total=10, hundred_plus=i) for i in range(1, 9)
    ]
    r = await client.get(f"/prodash/getInaccurateScales/kochi/0/0/{FROM_S}/{TO_S}")
    assert len(r.json()["rows"]) == 5


# ---- E21 -----------------------------------------------------------------------------


async def test_legacy_scale_efficiency_table(env: Any) -> None:
    client, scripted = env
    _counts(scripted)
    r = await client.post(
        "/prodash/getScaleEfficiencyDetailsTableData2",
        data={"from": FROM_MS, "to": TO_MS, "plant": "anantapur", "carousal": 0},
    )
    body = r.json()
    assert body["status"] == "success" and body["message"] == "Data Fetched Successfully!"
    assert body["tableparams"] == {"carousals": 0}
    assert [t["scaleId"] for t in body["tabledata"]] == [101, 102, 201]
    assert "car1Count" in body["tabledata"][0] and "car2Count" not in body["tabledata"][0]
    assert scripted.calls[0][1]["carousels"] == [1, 2]


async def test_scale_efficiency_carousal_filter_and_empty(env: Any) -> None:
    client, scripted = env
    r = await client.post(
        "/prodash/getScaleEfficiencyDetailsTableData2",
        data={"from": FROM_MS, "to": TO_MS, "plant": "kochi", "carousal": 2},
    )
    assert r.json()["tabledata"] == [] and r.json()["tableparams"] == {"carousals": 2}
    assert scripted.calls[0][1]["carousels"] == [2]


# ---- E22 -----------------------------------------------------------------------------


def _stat_row() -> SimpleNamespace:
    return row(
        device_id=101, cnt=10, n200=1, n150=0, n100=2, n50=1, nil_var=3, p50=1, p100=1, p150=0, p200=1,
        average=-12.5, stddev=88.25,
    )  # fmt: skip


async def test_legacy_filling_statistics_success_and_empty(env: Any) -> None:
    client, scripted = env
    form = {"from": FROM_MS, "to": TO_MS, "plant": "kochi", "carousal": 1, "cylType": 2}
    empty = await client.post("/prodash/getFillingStatistics", data=form)
    assert empty.json() == {"status": "failure", "errors": None}  # D-18

    scripted.rows["stats"] = [_stat_row()]
    ok = await client.post("/prodash/getFillingStatistics", data=form)
    body = ok.json()
    assert body["status"] == "success" and body["message"] == "Data Fetched Successfully!"
    assert body["tabledata"][0] == {
        "scaleId": 101, "n200": 1, "n150": 0, "n100": 2, "n50": 1, "nilVar": 3, "p50": 1,
        "p100": 1, "p150": 0, "p200": 1, "total": 10, "average": -12.5, "stdDev": 88.25,
    }  # fmt: skip
    _, params = scripted.calls[-1]
    assert params["nominal"] == 19000 and params["carousel"] == 1


async def test_v1_filling_statistics_empty_is_success_list(env: Any) -> None:
    client, _ = env
    r = await client.get(
        "/api/v1/plants/kochi/tables/filling-statistics",
        params={"from_ms": FROM_MS, "to_ms": TO_MS, "carousel": 1, "cyl_type": 1},
    )
    assert r.status_code == 200 and r.json() == []


# ---- E23 -----------------------------------------------------------------------------


async def test_legacy_accuracy_trend(env: Any) -> None:
    client, scripted = env
    form = {"from": "2026-01-05 14:00:00", "to": "2026-01-05 15:00:00", "plant": "kochi",
            "carousal": 1, "cylType": 1}  # fmt: skip
    assert (await client.post("/prodash/getAccuracyTrend", data=form)).json() == {
        "status": "success",
        "data": None,
    }
    scripted.rows["trend"] = [
        row(carousal=1, interval_start=T0, variation_eq_0=30, total_cylinders=40,
            variation_eq_0_percentage=75.0)
    ]  # fmt: skip
    body = (await client.post("/prodash/getAccuracyTrend", data=form)).json()
    assert body["status"] == "success"
    assert body["data"] == [
        {"carousal": 1, "interval_start": "2026-01-05 14:00:00", "variation_eq_0": 30,
         "total_cylinders": 40, "variation_eq_0_percentage": 75.0}
    ]  # fmt: skip


async def test_accuracy_trend_rejects_non_datetime_input(env: Any) -> None:
    client, _ = env
    r = await client.post(
        "/prodash/getAccuracyTrend",
        data={"from": "x'; DROP TABLE production_log;--", "to": "y", "plant": "kochi",
              "carousal": 1, "cylType": 1},
    )  # fmt: skip
    assert r.status_code == 422


# ---- E24 -----------------------------------------------------------------------------


async def test_legacy_periodic_production(env: Any) -> None:
    client, scripted = env
    scripted.rows["periodic"] = [
        row(period_start=datetime(2026, 1, 5, 14, 0), c1_t1=10, c1_t2=2, c2_t1=5, c2_t2=1),
        row(period_start=datetime(2026, 1, 5, 14, 30), c1_t1=7, c1_t2=0, c2_t1=0, c2_t2=3),
    ]
    r = await client.post(
        "/prodash/getHourlyProductionTableData",
        data={"from": FROM_MS, "to": TO_MS, "plant": "kochi", "interval": 30},
    )
    body = r.json()
    assert body["message"] == "Data Fetched Successfully!"
    assert body["tabledata"][0] == {
        "from": "14:00", "to": "14:30", "c1_t1": 10, "c1_t2": 2, "c1_total": 12,
        "c2_t1": 5, "c2_t2": 1, "c2_total": 6,
    }  # fmt: skip
    assert scripted.calls[0][1]["interval_min"] == 30


async def test_periodic_production_show_date_and_default_interval(env: Any) -> None:
    client, scripted = env
    scripted.rows["periodic"] = [
        row(period_start=datetime(2026, 1, 5, 23, 0), c1_t1=1, c1_t2=1, c2_t1=0, c2_t2=0)
    ]
    r = await client.post(
        "/prodash/getHourlyProductionTableData",
        data={"from": FROM_MS, "to": TO_MS + 24 * 3600 * 1000, "plant": "kochi"},
    )
    first = r.json()["tabledata"][0]
    assert first["from"] == "2026-01-05 23:00" and first["to"] == "2026-01-06 00:00"
    assert scripted.calls[0][1]["interval_min"] == 60


async def test_periodic_production_no_data_message(env: Any) -> None:
    client, _ = env
    r = await client.post(
        "/prodash/getHourlyProductionTableData",
        data={"from": FROM_MS, "to": TO_MS, "plant": "kochi"},
    )
    assert r.json() == {"status": "success", "message": "No data found!", "tabledata": []}


async def test_v1_periodic_production_uses_from_to_keys(env: Any) -> None:
    client, scripted = env
    scripted.rows["periodic"] = [
        row(period_start=datetime(2026, 1, 5, 14, 0), c1_t1=1, c1_t2=1, c2_t1=0, c2_t2=0)
    ]
    r = await client.get(
        "/api/v1/plants/kochi/tables/periodic-production",
        params={"from_ms": FROM_MS, "to_ms": TO_MS},
    )
    assert r.status_code == 200
    assert set(r.json()[0]) == {"from", "to", "c1_t1", "c1_t2", "c1_total", "c2_t1", "c2_t2", "c2_total"}


async def test_legacy_table_failure_payload_never_leaks(env: Any) -> None:
    client, _ = env
    r = await client.post(
        "/prodash/getHourlyProductionTableData",
        data={"from": FROM_MS, "to": TO_MS, "plant": "nowhere"},
    )
    assert r.json() == {"status": "failure", "error": "Unable to connect to selected Plant!", "th": {}}
