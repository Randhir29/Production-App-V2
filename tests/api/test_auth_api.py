"""Guards and E28 endpoints over HTTP (agent.md §7.8, §9.4)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import deps
from app.core.config import Settings
from app.core.security import create_access_token, create_refresh_token
from app.domain.static_config import parse_static_config
from app.main import create_app
from app.models import Role, User, UserRole
from app.services.auth import Principal, hash_token
from tests.repositories.conftest import make_engine
from tests.unit.fakes import FakePlantDatabases, FakePlantSession

PASSWORD = "correct horse battery staple"
LEGACY_HASH = "$2y$10$6LCqAsHr1SkHzXjfLsz1.eRlfnG45Ug4VY1zHnmKWiPzUuAMkcpEG"
API_KEY = "service-key-for-tests-only"

STATIC = parse_static_config(
    "plants: {kochi: Kochi}\nplant_name: Mysore LPG Plant\ncarousal_speed: {1: 50}\n"
    "interruption_to_exclude_minutes: 5\nplants_with_device_id: []\n"
)

MakeClient = Callable[..., Any]


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "central_database_url": "mysql+aiomysql://u:p@h/d",
        "jwt_secret_key": "j" * 40,
        "service_api_keys": [hash_token(API_KEY)],
    }
    base.update(kw)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


def _bearer(settings: Settings, roles: list[str], user_id: int = 1) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id, roles, settings)}"}


@pytest.fixture
async def make_client() -> AsyncIterator[MakeClient]:
    engine = await make_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as seed:
        await seed.execute(insert(Role), [{"name": n} for n in ("admin", "officer", "viewer")])
        await seed.execute(
            insert(User).values(
                id=1,
                email="alice@example.org",
                username="alice",
                password_hash=LEGACY_HASH,
                active=True,
                force_pass_reset=False,
                failed_login_count=0,
                fullname="Alice A",
            )
        )
        await seed.execute(insert(UserRole).values(user_id=1, role_id=3))  # viewer
        await seed.commit()

    clients: list[httpx.AsyncClient] = []

    def build(**settings_kw: Any) -> tuple[httpx.AsyncClient, Settings, FastAPI]:
        settings = _settings(**settings_kw)
        app = create_app(settings)

        async def central() -> AsyncIterator[AsyncSession]:
            async with sessions() as s:
                yield s

        app.dependency_overrides[deps.get_app_settings] = lambda: settings
        app.dependency_overrides[deps.get_central_session] = central
        app.dependency_overrides[deps.get_static_config] = lambda: STATIC
        plant_dbs = FakePlantDatabases(FakePlantSession())
        app.dependency_overrides[deps.get_plant_databases] = lambda: plant_dbs
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        clients.append(client)
        return client, settings, app

    yield build
    for c in clients:
        await c.aclose()
    await engine.dispose()


# ---- read guard -----------------------------------------------------------------------------


async def test_read_endpoints_require_a_token(make_client: MakeClient) -> None:
    client, _, _ = make_client()
    for path in ("/api/v1/meta/plant-name", "/prodash/getPlantName", "/api/v1/plants"):
        r = await client.get(path)
        assert r.status_code == 401, path
        assert r.json()["error"]["code"] == "UNAUTHENTICATED"
        assert r.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("role", ["viewer", "officer", "admin"])
async def test_any_read_role_is_accepted_on_both_routers(make_client: MakeClient, role: str) -> None:
    client, settings, _ = make_client()
    headers = _bearer(settings, [role])
    assert (await client.get("/api/v1/meta/plant-name", headers=headers)).status_code == 200
    assert (await client.get("/prodash/getPlantName", headers=headers)).json() == {"name": "Mysore LPG Plant"}


async def test_token_without_a_read_role_is_403(make_client: MakeClient) -> None:
    client, settings, _ = make_client()
    r = await client.get("/api/v1/meta/plant-name", headers=_bearer(settings, []))
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
    r = await client.get("/api/v1/meta/plant-name", headers=_bearer(settings, ["intern"]))
    assert r.status_code == 403


async def test_bad_tokens_are_401(make_client: MakeClient) -> None:
    client, settings, _ = make_client()
    refresh = create_refresh_token(1, settings)
    other = _settings(jwt_secret_key="x" * 40)
    bad = [
        {"Authorization": "Bearer garbage"},
        {"Authorization": f"Bearer {refresh}"},  # refresh token is not an access token
        _bearer(other, ["admin"]),  # signed with another key
        {"Authorization": "Basic YWxpY2U6cHc="},
    ]
    for headers in bad:
        r = await client.get("/api/v1/meta/plant-name", headers=headers)
        assert r.status_code == 401, headers


async def test_health_stays_open(make_client: MakeClient) -> None:
    client, _, _ = make_client()
    assert (await client.get("/api/v1/health/live")).status_code == 200


async def test_auth_disabled_opens_read_endpoints_but_not_admin_guard(
    make_client: MakeClient,
) -> None:
    client, settings, app = make_client(auth_enabled=False)
    assert (await client.get("/api/v1/meta/plant-name")).status_code == 200
    assert (await client.get("/prodash/getPlantName")).status_code == 200

    @app.get("/admin-only")
    async def admin_only(p: Principal = Depends(deps.require_roles("admin"))) -> dict[str, int]:  # noqa: B008
        return {"user": p.user_id}

    assert (await client.get("/admin-only")).status_code == 401  # still enforced
    assert (await client.get("/admin-only", headers=_bearer(settings, ["viewer"]))).status_code == 403
    assert (await client.get("/admin-only", headers=_bearer(settings, ["admin"], 7))).json() == {"user": 7}


# ---- E27: API key or admin JWT ----------------------------------------------------------------


MAIL_BODY = {"from": "2026-01-05", "to": "2026-01-05", "plant": "kochi"}


async def test_mailer_requires_key_or_admin(make_client: MakeClient) -> None:
    client, settings, _ = make_client()
    for path in ("/api/v1/reports/performance-mail-rows", "/prodash/performance-report"):
        assert (await client.post(path, json=MAIL_BODY)).status_code == 401
        wrong = await client.post(path, json=MAIL_BODY, headers={"X-API-Key": "not-the-key"})
        assert wrong.status_code == 401
        ok_key = await client.post(path, json=MAIL_BODY, headers={"X-API-Key": API_KEY})
        assert ok_key.status_code == 200 and ok_key.json() == []  # plant unknown -> empty feed
        viewer = await client.post(path, json=MAIL_BODY, headers=_bearer(settings, ["viewer"]))
        assert viewer.status_code == 403
        admin = await client.post(path, json=MAIL_BODY, headers=_bearer(settings, ["admin"]))
        assert admin.status_code == 200


async def test_api_key_does_not_open_other_endpoints(make_client: MakeClient) -> None:
    client, _, _ = make_client()
    r = await client.get("/api/v1/meta/plant-name", headers={"X-API-Key": API_KEY})
    assert r.status_code == 401


async def test_mailer_with_no_configured_keys_rejects_any_key(make_client: MakeClient) -> None:
    client, _, _ = make_client(service_api_keys=[])
    r = await client.post(
        "/api/v1/reports/performance-mail-rows", json=MAIL_BODY, headers={"X-API-Key": API_KEY}
    )
    assert r.status_code == 401


async def test_mailer_open_when_auth_disabled(make_client: MakeClient) -> None:
    client, _, _ = make_client(auth_enabled=False)
    r = await client.post("/prodash/performance-report", json=MAIL_BODY)
    assert r.status_code == 200


# ---- E28 ---------------------------------------------------------------------------------------


async def test_login_me_refresh_flow(make_client: MakeClient) -> None:
    client, _, _ = make_client()
    login = await client.post("/api/v1/auth/login", json={"username_or_email": "alice", "password": PASSWORD})
    assert login.status_code == 200
    body = login.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == 30 * 60

    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json() == {
        "id": 1,
        "email": "alice@example.org",
        "username": "alice",
        "fullname": "Alice A",
        "roles": ["viewer"],
    }

    # the issued access token opens the read endpoints
    read = await client.get(
        "/api/v1/meta/plant-name", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert read.status_code == 200

    rotated = await client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert rotated.status_code == 200
    assert rotated.json()["refresh_token"] != body["refresh_token"]
    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert replay.status_code == 401


async def test_login_failure_is_generic_401_and_leaks_nothing(make_client: MakeClient) -> None:
    client, _, _ = make_client()
    for payload in (
        {"username_or_email": "alice", "password": "wrong"},
        {"username_or_email": "ghost", "password": "wrong"},
    ):
        r = await client.post("/api/v1/auth/login", json=payload)
        assert r.status_code == 401
        assert r.json()["error"]["message"] == "Invalid credentials"
        assert "alice" not in r.text and "ghost" not in r.text


async def test_login_validation(make_client: MakeClient) -> None:
    client, _, _ = make_client()
    assert (await client.post("/api/v1/auth/login", json={"username_or_email": "a"})).status_code == 422
    huge = {"username_or_email": "alice", "password": "x" * 5000}
    assert (await client.post("/api/v1/auth/login", json=huge)).status_code == 422


async def test_me_requires_token_even_when_auth_disabled(make_client: MakeClient) -> None:
    client, _, _ = make_client(auth_enabled=False)
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_account_locks_after_repeated_failures_over_http(make_client: MakeClient) -> None:
    client, _, _ = make_client(login_max_attempts=3)
    for _ in range(3):
        await client.post("/api/v1/auth/login", json={"username_or_email": "alice", "password": "x"})
    r = await client.post("/api/v1/auth/login", json={"username_or_email": "alice", "password": PASSWORD})
    assert r.status_code == 401  # locked: identical to a wrong password
