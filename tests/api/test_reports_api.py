"""E25-E27 over HTTP with a scripted plant DB (agent.md §5.7, §9.4)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import deps
from app.core.config import Settings
from app.domain.static_config import parse_static_config
from app.main import create_app
from app.models import Carousel, CarouselBreak, Plant
from tests.repositories.conftest import make_engine
from tests.unit.fakes import FakePlantDatabases, FakeResult

STATIC = parse_static_config(
    "plants: {}\nplant_name: X\ncarousal_speed: {1: 50}\n"
    "interruption_to_exclude_minutes: 5\nplants_with_device_id: []\n"
)

T_FIRST = datetime(2026, 1, 5, 14, 0, 0)
T_LAST = datetime(2026, 1, 5, 22, 30, 0)
FROM_MS = int(datetime(2026, 1, 5, 8, 30, tzinfo=UTC).timestamp()) * 1000  # 14:00 IST
TO_MS = FROM_MS + 8 * 3600 * 1000


def r(**kw: Any) -> SimpleNamespace:
    return SimpleNamespace(**kw)


class ScriptedPlant:
    """Canned rows chosen by SQL text; records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.rows: dict[str, list[SimpleNamespace]] = {
            "functions": [("getgapbetweentimes",), ("getendgapforphase",)],  # type: ignore[list-item]
            "bottling": [r(system_id=1, production_14_2=1000, production_19=400)],
            "first_last": [r(system_id=1, last_cyl_time=T_LAST, first_cyl_time=T_FIRST)],
            "v1_gap": [r(total_gap=0.5)],
            "cs": [r(process_status=0, system_id=1, cnt=950), r(process_status=1040, system_id=1, cnt=50)],
            "gd": [r(system_id=1, accepted=990, rejected=10, total=1000)],
            "pt": [],
            "phase": [
                r(phase="normal", prod_14_2=800, prod_19=300),
                r(phase="break", prod_14_2=10, prod_19=0),
                r(phase="overtime", prod_14_2=190, prod_19=100),
            ],
            "gap": [r(total_normal_gap=1.0, total_break_gap=0.1, total_overtime_gap=0.2)],
            "non_op": [r(non_op_days=1)],
            "ot": [r(part="pre", hours=1.0), r(part="post", hours=0.5)],
        }

    async def execute(self, stmt: Any, params: Any = None) -> FakeResult:
        text = str(stmt)
        p = params or {}
        self.calls.append((text, p))
        if "pg_proc" in text:
            key = "functions"
        elif "AS phase" in text:
            key = "phase"
        elif "WITH day_wise_data" in text:
            key = "gap"
        elif "generate_series" in text:
            key = "non_op"
        elif "'pre' AS part" in text:
            key = "ot"
        elif "sum(gap)" in text:
            key = "v1_gap"
        elif "production_14_2" in text:
            key = "bottling"
        elif "MAX(process_date)" in text:
            key = "first_last"
        elif "FROM event_log" in text:
            key = "gd" if p["process_ids"] == [3, 23] else "pt"
        elif "process_status, system_id, COUNT" in text:
            key = "cs"
        else:
            raise AssertionError(text)
        return FakeResult(self.rows[key])  # type: ignore[arg-type]

    async def commit(self) -> None:
        return None

    def calls_matching(self, needle: str) -> list[dict[str, Any]]:
        return [p for t, p in self.calls if needle in t]


@pytest.fixture
async def env() -> AsyncIterator[tuple[httpx.AsyncClient, ScriptedPlant]]:
    engine = await make_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as seed:

        def plant(i: int, name: str, zone: str) -> Plant:
            return Plant(id=i, short_name=name, plant_name=name.title(), zone=zone, host_ip="10.0.0.1",
                         db_user="ro", db_password="pw", db_database="ccc")  # fmt: skip

        seed.add_all(
            [
                plant(1, "anantapur", "South"),
                plant(2, "nobreaks", "South"),
                plant(3, "nocfg", "North"),
                Carousel(id=1, plant_id=1, carousal_id=1, heads=72, rated_productivity=3000,
                         start_time=time(14, 0), stop_time=time(22, 30)),
                CarouselBreak(id=1, plant_id=1, carousal_id=1, start_time=time(19, 0),
                              stop_time=time(19, 30)),
                Carousel(id=2, plant_id=2, carousal_id=1, heads=24, rated_productivity=1800,
                         start_time=time(6, 0), stop_time=time(14, 0)),
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

    scripted = ScriptedPlant()
    dbs = FakePlantDatabases(scripted)  # type: ignore[arg-type]
    app.dependency_overrides[deps.get_app_settings] = lambda: settings
    app.dependency_overrides[deps.get_central_session] = central
    app.dependency_overrides[deps.get_static_config] = lambda: STATIC
    app.dependency_overrides[deps.get_plant_databases] = lambda: dbs
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c, scripted
    await engine.dispose()


# ---- V1 -----------------------------------------------------------------------------------


async def test_legacy_v1_report(env: Any) -> None:
    client, scripted = env
    resp = await client.post(
        "/prodash/getPerformanceReport", data={"from": FROM_MS, "to": TO_MS, "plant": "anantapur"}
    )
    body = resp.json()
    assert body["status"] == "success" and body["message"] == "Data Fetched Successfully!"
    data = body["data"]
    assert set(data) == {
        "carousals", "bottlingData", "productionHoursData", "checkScaleRejectionSummary",
        "gdRejectionSummary", "ptRejectionSummary", "productivityData",
    }  # fmt: skip
    assert data["carousals"] == [1]
    assert data["bottlingData"]["1"] == {"carousal": 1, "production_14_2": 1000, "production_19": 400}
    assert data["productionHoursData"]["1"]["total_gap"] == 0.5
    assert data["productionHoursData"]["1"]["total_hours"] == 8.5
    assert data["productivityData"]["1"]["productivity"] == 1500 / 8.0
    assert data["checkScaleRejectionSummary"]["1"]["totalSortout"] == 50
    assert data["gdRejectionSummary"]["1"]["rejected"] == 10
    assert data["ptRejectionSummary"] is False  # legacy `false`
    # window: ms -> naive IST datetimes; carousels bound from the DB config
    first = scripted.calls_matching("production_14_2")[0]
    assert first["from_dt"] == T_FIRST and first["carousels"] == [1]
    assert first["excluded"] == [1296, 5392, 17424]


async def test_v1_report_on_v1_router(env: Any) -> None:
    client, _ = env
    resp = await client.get(
        "/api/v1/plants/anantapur/reports/performance", params={"from_ms": FROM_MS, "to_ms": TO_MS}
    )
    assert resp.status_code == 200 and resp.json()["data"]["carousals"] == [1]


async def test_legacy_v1_unknown_plant_failure_payload(env: Any) -> None:
    client, _ = env
    resp = await client.post(
        "/prodash/getPerformanceReport", data={"from": FROM_MS, "to": TO_MS, "plant": "nowhere"}
    )
    assert resp.json() == {
        "status": "failure",
        "error": "Unable to connect to selected Plant!",
        "th": {},
    }


# ---- V2 -----------------------------------------------------------------------------------


async def test_legacy_v2_report_blocks_and_windows(env: Any) -> None:
    client, scripted = env
    resp = await client.post(
        "/prodash/getPerformanceReportV2",
        data={"from": "2026-01-05", "to": "2026-01-06", "plant": "anantapur"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert set(data) == {
        "carousals", "bottlingData", "bottlingData2", "productionHoursData", "productionHoursData2",
        "checkScaleRejectionSummary", "gdRejectionSummary", "ptRejectionSummary",
        "productivityData", "productivityData2",
    }  # fmt: skip
    assert data["bottlingData2"]["1"]["normal"] == {"phase": "normal", "prod_14_2": 800, "prod_19": 300}
    hours2 = data["productionHoursData2"]["1"]
    assert hours2["intervening_days"] == 2 and hours2["non_op_days"] == 1 and hours2["net_op_days"] == 1
    assert hours2["max_op_hours"] == {"normal": 8.0, "break": 0.5}
    assert hours2["net_op_hours"] == {"normal": 7.0, "break": 0.4}
    p2 = data["productivityData2"]["1"]
    assert p2["normal"]["net_hours"] == 7.0
    assert p2["normal"]["productivity"] == (800 + 1.25 * 300) / 7.0
    assert p2["overtime"]["net_hours"] == pytest.approx(1.5 - 0.2)

    # phase/gap SQL uses .999 end of day, the CS/GD/PT/V1 blocks drop it (§5.2)
    assert scripted.calls_matching("AS phase")[0]["to_dt"] == datetime(2026, 1, 6, 23, 59, 59, 999_000)
    assert scripted.calls_matching("production_14_2")[0]["to_dt"] == datetime(2026, 1, 6, 23, 59, 59)
    phase_params = scripted.calls_matching("AS phase")[0]
    assert phase_params["w0_from"] == "14:00:00" and phase_params["b0_to"] == "19:30:00"
    non_op = scripted.calls_matching("generate_series")[0]
    assert non_op["process_ids"] == [1, 2, 22] and non_op["excluded_status"] == [16]


async def test_v2_alias_route_and_gap_functions_checked_once(env: Any) -> None:
    client, scripted = env
    form = {"from": "2026-01-05", "to": "2026-01-05", "plant": "anantapur"}
    assert (await client.post("/prodash/getPerformanceReportV2Test2", data=form)).status_code == 200
    assert (await client.post("/prodash/getPerformanceReportV2", data=form)).status_code == 200
    # one pg_proc lookup per request chain is fine, but never any CREATE FUNCTION (D-21)
    assert not any("CREATE OR REPLACE" in t for t, _ in scripted.calls)


async def test_v2_plant_without_breaks_is_409_d06(env: Any) -> None:
    client, _ = env
    resp = await client.post(
        "/prodash/getPerformanceReportV2",
        data={"from": "2026-01-05", "to": "2026-01-05", "plant": "nobreaks"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "PLANT_CONFIG_INCOMPLETE"


async def test_v2_plant_without_any_config_is_409(env: Any) -> None:
    client, _ = env
    resp = await client.get(
        "/api/v1/plants/nocfg/reports/performance-v2",
        params={"from_date": "2026-01-05", "to_date": "2026-01-05"},
    )
    assert resp.status_code == 409


async def test_v2_unknown_plant_is_generic_500_in_legacy_d19_and_404_in_v1(env: Any) -> None:
    client, _ = env
    legacy = await client.post(
        "/prodash/getPerformanceReportV2",
        data={"from": "2026-01-05", "to": "2026-01-05", "plant": "nowhere"},
    )
    assert legacy.status_code == 500 and legacy.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "nowhere" not in legacy.text
    v1 = await client.get(
        "/api/v1/plants/nowhere/reports/performance-v2",
        params={"from_date": "2026-01-05", "to_date": "2026-01-05"},
    )
    assert v1.status_code == 404


async def test_v2_validation(env: Any) -> None:
    client, _ = env
    resp = await client.get(
        "/api/v1/plants/anantapur/reports/performance-v2",
        params={"from_date": "2026-01-06", "to_date": "2026-01-05"},
    )
    assert resp.status_code == 422


# ---- E27 -----------------------------------------------------------------------------------


async def test_mailer_rows_legacy_and_v1(env: Any) -> None:
    client, _ = env
    body = {"from": "2026-01-05", "to": "2026-01-06", "plant": "anantapur"}
    legacy = await client.post("/prodash/performance-report", json=body)
    assert legacy.status_code == 200
    assert legacy.headers["access-control-allow-origin"] == "*"
    rows = legacy.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["short_name"] == "anantapur" and row["name"] == "Anantapur"
    assert row["zone"] == "South" and row["carousel"] == 1 and row["carousel_count"] == 1
    assert row["filling_heads"] == 72 and row["isAdditionalCarousel"] is False
    assert row["bottling"] == {"14_2kg": 1000, "19kg": 400, "total": 1400}
    assert row["productivity"]["normal"] == {
        "production": 800 + 1.25 * 300, "stoppages": 1.0, "productivity": 168.0
    }  # fmt: skip
    assert row["productivity"]["break"]["net_hours"] == 0.4
    assert row["rejections"] == {
        "eld": {"percent": 1.0},
        "ort": {"percent": 0.0},
        "cs": {"percent": 5.0},
    }

    v1 = await client.post("/api/v1/reports/performance-mail-rows", json=body)
    assert v1.status_code == 200 and v1.json() == rows


async def test_mailer_rows_empty_when_plant_unusable(env: Any) -> None:
    client, _ = env
    for plant in ("nowhere", "nobreaks", "nocfg"):
        resp = await client.post(
            "/prodash/performance-report", json={"from": "2026-01-05", "to": "2026-01-05", "plant": plant}
        )
        assert resp.status_code == 200 and resp.json() == [], plant


async def test_mailer_rejects_bad_dates(env: Any) -> None:
    client, _ = env
    resp = await client.post(
        "/prodash/performance-report", json={"from": "2026-01-06", "to": "2026-01-05", "plant": "anantapur"}
    )
    assert resp.status_code == 422
