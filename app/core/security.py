"""PHP-compatibility helpers (agent.md §8.3) and authentication primitives (§7.8)."""

from __future__ import annotations

import base64
import hashlib
import math
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import Settings, get_settings

# ---------------------------------------------------------------------------------------
# PHP numeric / array compatibility
# ---------------------------------------------------------------------------------------

_PHP_INT_KEY = re.compile(r"^(0|-?[1-9][0-9]*)$")


def php_round(value: float | Decimal, precision: int = 0) -> float:
    """Round like PHP ``round()``: half away from zero (§8.3, D-16).

    Python's built-in ``round()`` uses banker's rounding on the binary float, which differs.
    The shortest repr of the float is rounded as a decimal, so ``2.675`` gives ``2.68``.
    Non-finite values are returned unchanged.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return value
    number = value if isinstance(value, Decimal) else Decimal(repr(value))
    quantum = Decimal(1).scaleb(-precision)
    rounded = abs(number).quantize(quantum, rounding=ROUND_HALF_UP)
    return float(-rounded if number < 0 else rounded)


def _normalise_key(key: Any) -> int | str:
    """PHP array keys: numeric strings such as ``"1"`` become ints."""
    if isinstance(key, str):
        return int(key) if _PHP_INT_KEY.match(key) else key
    return int(key) if isinstance(key, int) else str(key)


def _items(array: Mapping[Any, Any] | Sequence[Any]) -> list[tuple[int | str, Any]]:
    if isinstance(array, Mapping):
        return [(_normalise_key(k), v) for k, v in array.items()]
    return list(enumerate(array))


def php_object_keys(array: Mapping[Any, Any] | Sequence[Any]) -> dict[str, Any]:
    """Return the array as a JSON-object-shaped dict with string keys (§8.3).

    PHP encodes arrays with non-sequential integer keys as JSON objects (``{"1": …}``).
    Sequences are keyed by their index. For legacy adapters only.
    """
    return {str(k): v for k, v in _items(array)}


def php_array_merge(*arrays: Mapping[Any, Any] | Sequence[Any]) -> dict[int | str, Any]:
    """Merge like PHP ``array_merge`` (§5.3.2, D-17).

    String keys: later values overwrite earlier ones (position of first occurrence kept).
    Integer keys: renumbered from 0 in order and appended. For legacy adapters only.
    """
    merged: dict[int | str, Any] = {}
    next_index = 0
    for array in arrays:
        for key, value in _items(array):
            if isinstance(key, int):
                merged[next_index] = value
                next_index += 1
            else:
                merged[key] = value
    return merged


# ---------------------------------------------------------------------------------------
# Passwords: bcrypt(base64(sha384(password))), as stored by myth/auth (§7.8)
# ---------------------------------------------------------------------------------------

pwd_context = CryptContext(
    schemes=["bcrypt"], deprecated="auto", bcrypt__default_rounds=12, bcrypt__min_rounds=12
)


def _prehash(password: str) -> str:
    return base64.b64encode(hashlib.sha384(password.encode()).digest()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password against a legacy-compatible hash (``$2y$`` / ``$2b$`` accepted)."""
    try:
        return bool(pwd_context.verify(_prehash(password), password_hash))
    except (ValueError, TypeError):
        return False


def hash_password(password: str) -> str:
    """Hash with the same pre-hash as myth/auth so hashes stay interchangeable."""
    return str(pwd_context.hash(_prehash(password)))


def password_needs_rehash(password_hash: str) -> bool:
    """True when the stored hash should be upgraded after a successful login."""
    return bool(pwd_context.needs_update(password_hash))


# ---------------------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------------------

TokenType = Literal["access", "refresh"]


class TokenError(Exception):
    """The token is malformed, expired, has a bad signature or is of the wrong type."""


def _encode(
    subject: str,
    token_type: TokenType,
    lifetime: timedelta,
    settings: Settings,
    extra: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> str:
    issued = now or datetime.now(UTC)
    claims: dict[str, Any] = {
        **(extra or {}),
        "sub": subject,
        "type": token_type,
        "iat": int(issued.timestamp()),
        "exp": int((issued + lifetime).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return str(
        jwt.encode(claims, settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm)
    )


def create_access_token(
    subject: str | int,
    roles: Sequence[str] = (),
    settings: Settings | None = None,
    now: datetime | None = None,
) -> str:
    """Issue an access JWT with ``sub``, ``roles``, ``type``, ``iat``, ``exp``, ``jti``."""
    cfg = settings or get_settings()
    return _encode(
        str(subject),
        "access",
        timedelta(minutes=cfg.access_token_expire_minutes),
        cfg,
        {"roles": list(roles)},
        now,
    )


def create_refresh_token(
    subject: str | int, settings: Settings | None = None, now: datetime | None = None
) -> str:
    """Issue a refresh JWT (``type="refresh"``); the caller stores its hash for revocation."""
    cfg = settings or get_settings()
    return _encode(str(subject), "refresh", timedelta(days=cfg.refresh_token_expire_days), cfg, None, now)


def decode_token(
    token: str, expected_type: TokenType = "access", settings: Settings | None = None
) -> dict[str, Any]:
    """Verify signature and expiry and return the claims.

    Raises ``TokenError`` for any failure, including a token of the wrong type.
    """
    cfg = settings or get_settings()
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            cfg.jwt_secret_key.get_secret_value(),
            algorithms=[cfg.jwt_algorithm],
            options={"require_sub": True, "require_exp": True, "require_iat": True},
        )
    except JWTError as exc:
        raise TokenError("invalid token") from exc
    if claims.get("type") != expected_type:
        raise TokenError("unexpected token type")
    return claims
