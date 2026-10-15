"""AuthService and admin provisioning against an in-memory central DB (agent.md §7.8, §9)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AuthError
from app.core.security import decode_token, verify_password
from app.models import RefreshToken, User, UserRole
from app.repositories.central.user_repo import RefreshTokenRepository, UserRepository
from app.services.admin_users import AdminUserError, AdminUserService
from app.services.auth import AuthService, Principal, hash_token
from tests.repositories.conftest import seed_roles

# bcrypt(base64(sha384(pw))) with a $2y$ prefix at cost 10: what myth/auth stored.
PASSWORD = "correct horse battery staple"
LEGACY_HASH = "$2y$10$6LCqAsHr1SkHzXjfLsz1.eRlfnG45Ug4VY1zHnmKWiPzUuAMkcpEG"

# Naive UTC like the stored values. Anchored at the real now because the JWT library
# checks `exp` against the wall clock; the service clock then only moves forward from it.
T0 = datetime.now(UTC).replace(tzinfo=None, microsecond=0)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


def _settings(**kw: object) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        central_database_url="mysql+aiomysql://u:p@h/d",
        jwt_secret_key="s" * 40,
        access_token_expire_minutes=30,
        refresh_token_expire_days=7,
        login_max_attempts=3,
        login_lockout_minutes=15,
        **kw,  # type: ignore[arg-type]
    )


async def _add_user(session: AsyncSession, **overrides: object) -> int:
    values: dict[str, object] = {
        "email": "Alice@Example.org",
        "username": "alice",
        "password_hash": LEGACY_HASH,
        "active": True,
        "force_pass_reset": False,
        "failed_login_count": 0,
        "fullname": "Alice A",
    }
    values.update(overrides)
    result = await session.execute(insert(User).values(**values))
    await session.commit()
    return int(result.inserted_primary_key[0])  # type: ignore[index]


async def _grant(session: AsyncSession, user_id: int, role_name: str) -> None:
    from app.models import Role

    role_id = (await session.execute(select(Role.id).where(Role.name == role_name))).scalar_one()
    await session.execute(insert(UserRole).values(user_id=user_id, role_id=role_id))
    await session.commit()


@pytest.fixture
async def ctx(central_session: AsyncSession) -> tuple[AuthService, AsyncSession, Clock]:
    await seed_roles(central_session)
    clock = Clock()
    service = AuthService(
        UserRepository(central_session),
        RefreshTokenRepository(central_session),
        _settings(),
        clock,
    )
    return service, central_session, clock


# ---- login -------------------------------------------------------------------------------


async def test_legacy_user_logs_in_with_existing_password_and_hash_is_upgraded(ctx) -> None:  # type: ignore[no-untyped-def]
    service, session, _ = ctx
    uid = await _add_user(session)
    await _grant(session, uid, "officer")

    pair = await service.login("alice", PASSWORD)

    claims = decode_token(pair.access_token, "access", service._settings)
    assert claims["sub"] == str(uid) and claims["roles"] == ["officer"]
    assert pair.expires_in == 30 * 60
    refresh = decode_token(pair.refresh_token, "refresh", service._settings)
    assert refresh["jti"] != claims["jti"]

    stored = (await session.execute(select(User.password_hash).where(User.id == uid))).scalar_one()
    assert stored.startswith("$2b$12$") and verify_password(PASSWORD, stored)  # §7.8 upgrade


async def test_refresh_token_is_stored_hashed_only(ctx) -> None:  # type: ignore[no-untyped-def]
    service, session, clock = ctx
    uid = await _add_user(session)
    pair = await service.login("alice", PASSWORD)
    row = (await session.execute(select(RefreshToken))).scalar_one()
    assert row.user_id == uid and row.token_hash == hash_token(pair.refresh_token)
    assert pair.refresh_token not in row.token_hash
    assert row.expires_at == clock.now + timedelta(days=7)


async def test_login_by_email_is_case_insensitive(ctx) -> None:  # type: ignore[no-untyped-def]
    service, session, _ = ctx
    await _add_user(session)
    assert (await service.login("ALICE@example.ORG", PASSWORD)).access_token


async def test_wrong_password_and_unknown_user_give_identical_error(ctx) -> None:  # type: ignore[no-untyped-def]
    service, session, _ = ctx
    await _add_user(session)
    with pytest.raises(AuthError) as wrong:
        await service.login("alice", "nope")
    with pytest.raises(AuthError) as unknown:
        await service.login("nobody", "nope")
    assert wrong.value.message == unknown.value.message == "Invalid credentials"
    assert wrong.value.http_status == 401 and wrong.value.code == "UNAUTHENTICATED"


@pytest.mark.parametrize(
    "overrides",
    [
        {"active": False},
        {"status": "banned"},
        {"status": "BANNED"},
        {"deleted_at": T0 - timedelta(days=1)},
    ],
)
async def test_inactive_banned_and_deleted_users_are_rejected(ctx, overrides) -> None:  # type: ignore[no-untyped-def]
    service, session, _ = ctx
    await _add_user(session, **overrides)
    with pytest.raises(AuthError):
        await service.login("alice", PASSWORD)  # correct password
    assert (await session.execute(select(RefreshToken))).first() is None


async def test_lockout_after_max_failures_and_expiry(ctx) -> None:  # type: ignore[no-untyped-def]
    service, session, clock = ctx
    uid = await _add_user(session)
    for _ in range(3):
        with pytest.raises(AuthError):
            await service.login("alice", "wrong")
    row = (await session.execute(select(User).where(User.id == uid))).scalar_one()
    assert row.failed_login_count == 3 and row.locked_until == T0 + timedelta(minutes=15)

    # locked: even the right password is refused, with the same generic error
    clock.advance(minutes=5)
    with pytest.raises(AuthError) as locked:
        await service.login("alice", PASSWORD)
    assert locked.value.message == "Invalid credentials"

    # after the lock expires the right password works and the counters are cleared
    clock.advance(minutes=11)
    assert (await service.login("alice", PASSWORD)).access_token
    session.expire_all()
    row = (await session.execute(select(User).where(User.id == uid))).scalar_one()
    assert row.failed_login_count == 0 and row.locked_until is None


async def test_failure_after_stale_lock_restarts_the_counter(ctx) -> None:  # type: ignore[no-untyped-def]
    service, session, clock = ctx
    uid = await _add_user(session)
    for _ in range(3):
        with pytest.raises(AuthError):
            await service.login("alice", "wrong")
    clock.advance(minutes=16)
    with pytest.raises(AuthError):
        await service.login("alice", "wrong")
    session.expire_all()
    row = (await session.execute(select(User).where(User.id == uid))).scalar_one()
    assert row.failed_login_count == 1 and row.locked_until is None


async def test_success_resets_partial_failure_count(ctx) -> None:  # type: ignore[no-untyped-def]
    service, session, _ = ctx
    uid = await _add_user(session)
    for _ in range(2):
        with pytest.raises(AuthError):
            await service.login("alice", "wrong")
    await service.login("alice", PASSWORD)
    session.expire_all()
    row = (await session.execute(select(User).where(User.id == uid))).scalar_one()
    assert row.failed_login_count == 0


# ---- refresh -----------------------------------------------------------------------------


async def test_refresh_rotates_and_old_token_is_dead(ctx) -> None:  # type: ignore[no-untyped-def]
    service, session, clock = ctx
    uid = await _add_user(session)
    await _grant(session, uid, "viewer")
    first = await service.login("alice", PASSWORD)
    clock.advance(minutes=1)
    second = await service.refresh(first.refresh_token)
    assert second.refresh_token != first.refresh_token
    assert decode_token(second.access_token, "access", service._settings)["roles"] == ["viewer"]
    tokens = (await session.execute(select(RefreshToken).order_by(RefreshToken.id))).scalars().all()
    assert [t.revoked_at is not None for t in tokens] == [True, False]


async def test_replaying_a_rotated_refresh_token_revokes_everything(ctx) -> None:  # type: ignore[no-untyped-def]
    service, session, clock = ctx
    await _add_user(session)
    first = await service.login("alice", PASSWORD)
    clock.advance(minutes=1)
    second = await service.refresh(first.refresh_token)
    with pytest.raises(AuthError):
        await service.refresh(first.refresh_token)  # replay of the revoked token
    with pytest.raises(AuthError):
        await service.refresh(second.refresh_token)  # family revoked as well


async def test_refresh_rejects_expired_wrong_type_and_garbage(ctx) -> None:  # type: ignore[no-untyped-def]
    service, session, clock = ctx
    await _add_user(session)
    pair = await service.login("alice", PASSWORD)
    with pytest.raises(AuthError):
        await service.refresh(pair.access_token)  # an access token is not a refresh token
    with pytest.raises(AuthError):
        await service.refresh("garbage")
    # the JWT expires after the configured lifetime
    clock.advance(days=8)
    with pytest.raises(AuthError):
        await service.refresh(pair.refresh_token)


async def test_refresh_denied_once_user_is_disabled(ctx) -> None:  # type: ignore[no-untyped-def]
    service, session, clock = ctx
    uid = await _add_user(session)
    pair = await service.login("alice", PASSWORD)
    await session.execute(User.__table__.update().where(User.id == uid).values(active=False))
    await session.commit()
    clock.advance(minutes=1)
    with pytest.raises(AuthError):
        await service.refresh(pair.refresh_token)


# ---- profile -----------------------------------------------------------------------------


async def test_me_returns_profile_with_roles(ctx) -> None:  # type: ignore[no-untyped-def]
    service, session, _ = ctx
    uid = await _add_user(session)
    await _grant(session, uid, "admin")
    await _grant(session, uid, "viewer")
    profile = await service.me(Principal(uid, frozenset({"admin"})))
    assert (profile.username, profile.fullname, profile.roles) == ("alice", "Alice A", ["admin", "viewer"])
    with pytest.raises(AuthError):
        await service.me(Principal(9999, frozenset()))


# ---- create-admin ------------------------------------------------------------------------


async def test_create_admin_can_then_log_in(ctx) -> None:  # type: ignore[no-untyped-def]
    service, session, _ = ctx
    admin = AdminUserService(UserRepository(session), clock=lambda: T0)
    uid = await admin.create_admin("root.admin", "root@example.org", "a-long-passphrase", "Root")
    pair = await service.login("root@example.org", "a-long-passphrase")
    claims = decode_token(pair.access_token, "access", service._settings)
    assert claims["sub"] == str(uid) and claims["roles"] == ["admin"]
    stored = (await session.execute(select(User.password_hash).where(User.id == uid))).scalar_one()
    assert stored.startswith("$2b$12$") and "a-long-passphrase" not in stored


@pytest.mark.parametrize(
    ("username", "email", "password", "needle"),
    [
        ("ab", "a@example.org", "a-long-passphrase", "username"),
        ("valid.name", "not-an-email", "a-long-passphrase", "email"),
        ("valid.name", "a@example.org", "short", "password"),
    ],
)
async def test_create_admin_validation(ctx, username, email, password, needle) -> None:  # type: ignore[no-untyped-def]
    _, session, _ = ctx
    with pytest.raises(AdminUserError, match=needle):
        await AdminUserService(UserRepository(session)).create_admin(username, email, password)


async def test_create_admin_rejects_duplicates_and_missing_role(
    central_session: AsyncSession,
) -> None:
    repo = UserRepository(central_session)
    admin = AdminUserService(repo, clock=lambda: T0.replace(tzinfo=UTC).replace(tzinfo=None))
    with pytest.raises(AdminUserError, match="role 'admin' is missing"):
        await admin.create_admin("first.admin", "first@example.org", "a-long-passphrase")
    await central_session.rollback()

    await seed_roles(central_session)
    await admin.create_admin("first.admin", "first@example.org", "a-long-passphrase")
    with pytest.raises(AdminUserError, match="already exists"):
        await admin.create_admin("first.admin", "other@example.org", "a-long-passphrase")
    with pytest.raises(AdminUserError, match="already exists"):
        await admin.create_admin("other.admin", "FIRST@example.org", "a-long-passphrase")
