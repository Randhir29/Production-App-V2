"""Authentication service (agent.md §7.8, E28). No HTTP concerns.

* Legacy hashes (``bcrypt(base64(sha384(password)))``) verify unchanged and are upgraded on
  login when the cost is below the current policy.
* Failed logins are counted per account and lock it for a while; a locked account answers
  exactly like a wrong password so lockout state cannot be probed.
* Refresh tokens are stored hashed, rotate on every use, and a replayed (already revoked)
  token revokes the whole family.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache

from app.core.config import Settings
from app.core.errors import AuthError, PermissionDenied
from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from app.repositories.central.user_repo import (
    RefreshTokenRepository,
    UserRecord,
    UserRepository,
)

logger = logging.getLogger("prodash.services.auth")

READ_ROLES = frozenset({"viewer", "officer", "admin"})
BANNED_STATUS = "banned"


def utcnow() -> datetime:
    """Naive UTC, the convention of every central-DB timestamp."""
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller, derived from an access token."""

    user_id: int
    roles: frozenset[str]

    def has_any(self, roles: Sequence[str]) -> bool:
        return bool(self.roles.intersection(roles))


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True, slots=True)
class Profile:
    id: int
    email: str
    username: str | None
    fullname: str | None
    roles: list[str]


def hash_token(token: str) -> str:
    """SHA-256 hex digest used to store refresh tokens and API keys."""
    return hashlib.sha256(token.encode()).hexdigest()


def principal_from_access_token(token: str, settings: Settings) -> Principal:
    """Verify an access JWT and build the principal; raises ``AuthError``."""
    try:
        claims = decode_token(token, "access", settings)
        return Principal(int(claims["sub"]), frozenset(claims.get("roles") or []))
    except (TokenError, ValueError, KeyError) as exc:
        raise AuthError() from exc


def api_key_matches(presented: str, hashed_keys: Sequence[str]) -> bool:
    """Constant-time check of a presented key against SHA-256 hex digests (no early exit)."""
    digest = hash_token(presented)
    matched = False
    for candidate in hashed_keys:
        if hmac.compare_digest(digest, candidate.strip().lower()):
            matched = True
    return matched


@cache
def _dummy_hash() -> str:
    return hash_password("prodash-timing-equaliser")


class AuthService:
    """Login, token refresh and profile lookup."""

    def __init__(
        self,
        users: UserRepository,
        tokens: RefreshTokenRepository,
        settings: Settings,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._users = users
        self._tokens = tokens
        self._settings = settings
        self._clock = clock

    # ---- login -------------------------------------------------------------------------

    async def login(self, username_or_email: str, password: str) -> TokenPair:
        """Verify credentials and issue a token pair. Every failure is a generic ``AuthError``."""
        now = self._clock()
        user = await self._users.find_by_login(username_or_email.strip())
        if user is None:
            verify_password(password, _dummy_hash())  # equalise timing with the real path
            raise AuthError("Invalid credentials")

        if user.locked_until is not None and user.locked_until > now:
            verify_password(password, _dummy_hash())
            logger.warning("login refused: account locked user_id=%s", user.id)
            raise AuthError("Invalid credentials")

        if not verify_password(password, user.password_hash):
            await self._register_failure(user, now)
            raise AuthError("Invalid credentials")

        if not self._may_log_in(user):
            logger.warning("login refused: account not usable user_id=%s", user.id)
            raise AuthError("Invalid credentials")

        if password_needs_rehash(user.password_hash):
            await self._users.set_password_hash(user.id, hash_password(password), now)
        if user.failed_login_count or user.locked_until:
            await self._users.set_lockout(user.id, 0, None)
        pair = await self._issue(user.id, now)
        await self._users.commit()
        return pair

    @staticmethod
    def _may_log_in(user: UserRecord) -> bool:
        """Inactive, banned and soft-deleted users are rejected (§7.8)."""
        return user.active and user.deleted_at is None and (user.status or "").lower() != BANNED_STATUS

    async def _register_failure(self, user: UserRecord, now: datetime) -> None:
        stale_lock = user.locked_until is not None and user.locked_until <= now
        count = 1 if stale_lock else user.failed_login_count + 1
        locked_until = None
        if count >= self._settings.login_max_attempts:
            locked_until = now + timedelta(minutes=self._settings.login_lockout_minutes)
            logger.warning("account locked after %s failures user_id=%s", count, user.id)
        await self._users.set_lockout(user.id, count, locked_until)
        await self._users.commit()

    # ---- refresh -----------------------------------------------------------------------

    async def refresh(self, refresh_token: str) -> TokenPair:
        """Rotate a refresh token. Replay of a revoked token revokes all of the user's tokens."""
        now = self._clock()
        try:
            claims = decode_token(refresh_token, "refresh", self._settings)
            user_id = int(claims["sub"])
        except (TokenError, ValueError, KeyError) as exc:
            raise AuthError("Invalid refresh token") from exc

        record = await self._tokens.get_by_hash(hash_token(refresh_token))
        if record is None or record.user_id != user_id:
            raise AuthError("Invalid refresh token")
        if record.revoked_at is not None:
            logger.warning("refresh token replay detected user_id=%s", user_id)
            await self._tokens.revoke_all(user_id, now)
            await self._users.commit()
            raise AuthError("Invalid refresh token")
        if record.expires_at <= now:
            raise AuthError("Invalid refresh token")

        user = await self._users.get(user_id)
        if user is None or not self._may_log_in(user):
            await self._tokens.revoke_all(user_id, now)
            await self._users.commit()
            raise AuthError("Invalid refresh token")

        if not await self._tokens.revoke(record.id, now):
            raise AuthError("Invalid refresh token")  # lost a rotation race
        pair = await self._issue(user_id, now)
        await self._users.commit()
        return pair

    async def _issue(self, user_id: int, now: datetime) -> TokenPair:
        roles = await self._users.role_names(user_id)
        access = create_access_token(user_id, roles, self._settings, now.replace(tzinfo=UTC))
        refresh = create_refresh_token(user_id, self._settings, now.replace(tzinfo=UTC))
        expires = now + timedelta(days=self._settings.refresh_token_expire_days)
        await self._tokens.add(user_id, hash_token(refresh), expires, now)
        return TokenPair(access, refresh, self._settings.access_token_expire_minutes * 60)

    # ---- profile -----------------------------------------------------------------------

    async def me(self, principal: Principal) -> Profile:
        user = await self._users.get(principal.user_id)
        if user is None or not self._may_log_in(user):
            raise AuthError()
        roles = await self._users.role_names(user.id)
        return Profile(user.id, user.email, user.username, user.fullname, roles)


def require_any_role(principal: Principal, roles: Sequence[str]) -> None:
    """Raise ``PermissionDenied`` unless the principal holds one of ``roles``."""
    if not principal.has_any(roles):
        raise PermissionDenied()


__all__ = [
    "READ_ROLES",
    "AuthService",
    "Principal",
    "Profile",
    "TokenPair",
    "api_key_matches",
    "hash_token",
    "principal_from_access_token",
    "require_any_role",
]
