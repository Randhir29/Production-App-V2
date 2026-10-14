"""Unit tests for app.core.security (agent.md §8.3, §7.8, §9.1). No I/O."""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.config import Settings
from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    password_needs_rehash,
    php_array_merge,
    php_object_keys,
    php_round,
    verify_password,
)

# bcrypt(base64(sha384(password))) with a $2y$ prefix and cost 10, i.e. the format myth/auth
# (PHP password_hash) stores. Generated once from the algorithm in agent.md §7.8.
LEGACY_PASSWORD = "correct horse battery staple"
LEGACY_PREHASH = "wkuSRJyHHzO7vx/BmJ5eEDfPqaPf2xeUf4FyImGB54Jeu0x1B2ORWDW/ElpZDgWu"
LEGACY_HASH = "$2y$10$6LCqAsHr1SkHzXjfLsz1.eRlfnG45Ug4VY1zHnmKWiPzUuAMkcpEG"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        central_database_url="mysql+aiomysql://u:p@localhost:3306/prodash",
        jwt_secret_key="unit-test-secret-" + "x" * 32,
        access_token_expire_minutes=5,
        refresh_token_expire_days=1,
    )


# ---------------------------------------------------------------------------------------
# php_round
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.5, 1.0),
        (1.5, 2.0),
        (2.5, 3.0),  # banker's rounding would give 2
        (3.5, 4.0),
        (-0.5, -1.0),
        (-1.5, -2.0),
        (-2.5, -3.0),
        (0.49999, 0.0),
        (-0.4, 0.0),
        (7, 7.0),
        (0, 0.0),
    ],
)
def test_php_round_halves_go_away_from_zero(value: float, expected: float) -> None:
    assert php_round(value) == expected


@pytest.mark.parametrize(
    ("value", "precision", "expected"),
    [
        (2.675, 2, 2.68),  # built-in round() gives 2.67
        (1.005, 2, 1.01),  # built-in round() gives 1.0
        (1.955, 2, 1.96),
        (5.055, 2, 5.06),
        (-2.675, 2, -2.68),
        (0.285, 2, 0.29),
        (3.14159, 3, 3.142),
        (1234567.891, -3, 1235000.0),
        (1250, -2, 1300.0),
        (-1250, -2, -1300.0),
        (99.995, 2, 100.0),
    ],
)
def test_php_round_precision(value: float, precision: int, expected: float) -> None:
    assert php_round(value, precision) == expected


def test_php_round_differs_from_builtin_round() -> None:
    assert round(2.5) == 2
    assert php_round(2.5) == 3.0
    assert round(2.675, 2) == 2.67
    assert php_round(2.675, 2) == 2.68


def test_php_round_returns_float_and_accepts_decimal() -> None:
    assert isinstance(php_round(5), float)
    assert php_round(Decimal("2.675"), 2) == 2.68


def test_php_round_non_finite_passthrough() -> None:
    assert php_round(float("inf")) == float("inf")
    assert php_round(float("nan")) != php_round(float("nan"))


# ---------------------------------------------------------------------------------------
# php_object_keys / php_array_merge
# ---------------------------------------------------------------------------------------


def test_php_object_keys_stringifies_int_keys() -> None:
    assert php_object_keys({1: "a", 2: "b"}) == {"1": "a", "2": "b"}


def test_php_object_keys_indexes_sequences() -> None:
    assert php_object_keys(["x", "y"]) == {"0": "x", "1": "y"}


def test_php_array_merge_renumbers_int_keys_as_in_kpi_productivity() -> None:
    """§5.3.2: array_merge(['overall' => x], [1 => c1, 2 => c2]) -> overall, 0, 1."""
    merged = php_array_merge({"overall": 10.5}, {1: "car1", 2: "car2"})
    assert merged == {"overall": 10.5, 0: "car1", 1: "car2"}
    assert list(merged) == ["overall", 0, 1]


def test_php_array_merge_string_keys_later_wins() -> None:
    assert php_array_merge({"a": 1, "b": 2}, {"b": 3, "c": 4}) == {"a": 1, "b": 3, "c": 4}


def test_php_array_merge_numeric_string_keys_are_ints() -> None:
    assert php_array_merge({"5": "a"}, {"9": "b"}) == {0: "a", 1: "b"}


def test_php_array_merge_sequences_and_empty() -> None:
    assert php_array_merge([1, 2], [3]) == {0: 1, 1: 2, 2: 3}
    assert php_array_merge() == {}


# ---------------------------------------------------------------------------------------
# Password hashing (§7.8)
# ---------------------------------------------------------------------------------------


def test_prehash_vector_matches_sha384_base64() -> None:
    digest = hashlib.sha384(LEGACY_PASSWORD.encode()).digest()
    assert base64.b64encode(digest).decode() == LEGACY_PREHASH


def test_verify_password_accepts_legacy_2y_hash() -> None:
    assert verify_password(LEGACY_PASSWORD, LEGACY_HASH) is True


def test_verify_password_rejects_wrong_password_and_raw_bcrypt_input() -> None:
    assert verify_password("wrong password", LEGACY_HASH) is False
    # The pre-hash string itself must not verify; only the raw password does.
    assert verify_password(LEGACY_PREHASH, LEGACY_HASH) is False


def test_verify_password_malformed_hash_is_false_not_error() -> None:
    assert verify_password("anything", "not-a-hash") is False
    assert verify_password("anything", "") is False


def test_hash_password_round_trip_and_salting() -> None:
    first = hash_password(LEGACY_PASSWORD)
    second = hash_password(LEGACY_PASSWORD)
    assert first != second
    assert first.startswith("$2b$12$")
    assert verify_password(LEGACY_PASSWORD, first)
    assert not verify_password("other", first)


def test_long_password_is_not_truncated_by_bcrypt_72_byte_limit() -> None:
    base = "a" * 100
    hashed = hash_password(base)
    assert verify_password(base, hashed)
    assert not verify_password("a" * 99 + "b", hashed)


def test_password_needs_rehash_for_lower_cost() -> None:
    assert password_needs_rehash(LEGACY_HASH) is True
    assert password_needs_rehash(hash_password("x")) is False


# ---------------------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------------------


def test_access_token_round_trip(settings: Settings) -> None:
    token = create_access_token(42, roles=["admin", "viewer"], settings=settings)
    claims = decode_token(token, "access", settings=settings)
    assert claims["sub"] == "42"
    assert claims["roles"] == ["admin", "viewer"]
    assert claims["type"] == "access"
    assert claims["exp"] - claims["iat"] == 5 * 60
    assert len(claims["jti"]) == 32


def test_refresh_token_round_trip_and_unique_jti(settings: Settings) -> None:
    a = create_refresh_token("7", settings=settings)
    b = create_refresh_token("7", settings=settings)
    ca = decode_token(a, "refresh", settings=settings)
    cb = decode_token(b, "refresh", settings=settings)
    assert ca["exp"] - ca["iat"] == 24 * 3600
    assert ca["jti"] != cb["jti"]


def test_decode_rejects_wrong_token_type(settings: Settings) -> None:
    refresh = create_refresh_token("1", settings=settings)
    with pytest.raises(TokenError):
        decode_token(refresh, "access", settings=settings)
    access = create_access_token("1", settings=settings)
    with pytest.raises(TokenError):
        decode_token(access, "refresh", settings=settings)


def test_decode_rejects_expired_token(settings: Settings) -> None:
    long_ago = datetime.now(UTC) - timedelta(hours=2)
    token = create_access_token("1", settings=settings, now=long_ago)
    with pytest.raises(TokenError):
        decode_token(token, "access", settings=settings)


def test_decode_rejects_tampered_and_foreign_tokens(settings: Settings) -> None:
    token = create_access_token("1", settings=settings)
    header, payload, signature = token.split(".")
    with pytest.raises(TokenError):
        decode_token(f"{header}.{payload}.{signature[:-2]}AA", settings=settings)
    with pytest.raises(TokenError):
        decode_token("garbage", settings=settings)

    other = Settings(
        _env_file=None,  # type: ignore[call-arg]
        central_database_url="mysql+aiomysql://u:p@localhost:3306/prodash",
        jwt_secret_key="a-different-secret-" + "y" * 32,
    )
    with pytest.raises(TokenError):
        decode_token(token, settings=other)


def test_decode_rejects_alg_none_token(settings: Settings) -> None:
    unsigned = (
        base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
        + "."
        + base64.urlsafe_b64encode(b'{"sub":"1","type":"access","iat":1,"exp":9999999999,"roles":["admin"]}')
        .rstrip(b"=")
        .decode()
        + "."
    )
    with pytest.raises(TokenError):
        decode_token(unsigned, settings=settings)
