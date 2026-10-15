"""Users, roles and refresh tokens in the central DB (agent.md §7.8). SQL only."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshToken, Role, User, UserRole


@dataclass(frozen=True, slots=True)
class UserRecord:
    """A ``users`` row; the password hash is excluded from ``repr``."""

    id: int
    email: str
    username: str | None
    fullname: str | None
    password_hash: str = field(repr=False)
    active: bool
    status: str | None
    deleted_at: datetime | None
    failed_login_count: int
    locked_until: datetime | None


@dataclass(frozen=True, slots=True)
class RefreshTokenRecord:
    id: int
    user_id: int
    expires_at: datetime
    revoked_at: datetime | None


def _record(user: User) -> UserRecord:
    return UserRecord(
        id=user.id,
        email=user.email,
        username=user.username,
        fullname=user.fullname,
        password_hash=user.password_hash,
        active=bool(user.active),
        status=user.status,
        deleted_at=user.deleted_at,
        failed_login_count=int(user.failed_login_count or 0),
        locked_until=user.locked_until,
    )


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    async def find_by_login(self, login: str) -> UserRecord | None:
        """Match ``username`` exactly or ``email`` case-insensitively; lowest id wins."""
        stmt = (
            select(User)
            .where(or_(User.username == login, func.lower(User.email) == login.lower()))
            .order_by(User.id)
            .limit(1)
        )
        user = (await self._session.execute(stmt)).scalar_one_or_none()
        return None if user is None else _record(user)

    async def get(self, user_id: int) -> UserRecord | None:
        user = await self._session.get(User, user_id)
        return None if user is None else _record(user)

    async def role_names(self, user_id: int) -> list[str]:
        stmt = (
            select(Role.name)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
            .order_by(Role.name)
        )
        return [name for (name,) in await self._session.execute(stmt)]

    async def set_lockout(self, user_id: int, failed_count: int, locked_until: datetime | None) -> None:
        await self._session.execute(
            update(User)
            .where(User.id == user_id)
            .values(failed_login_count=failed_count, locked_until=locked_until)
        )

    async def set_password_hash(self, user_id: int, password_hash: str, now: datetime) -> None:
        await self._session.execute(
            update(User).where(User.id == user_id).values(password_hash=password_hash, updated_at=now)
        )

    async def create_user(
        self,
        *,
        email: str,
        username: str,
        password_hash: str,
        fullname: str | None,
        now: datetime,
    ) -> int:
        user = User(
            email=email,
            username=username,
            password_hash=password_hash,
            fullname=fullname,
            active=True,
            force_pass_reset=False,
            failed_login_count=0,
            created_at=now,
            updated_at=now,
        )
        self._session.add(user)
        await self._session.flush()
        return user.id

    async def assign_role(self, user_id: int, role_name: str) -> bool:
        """Grant a role by name; returns False when the role does not exist."""
        role_id = (
            await self._session.execute(select(Role.id).where(Role.name == role_name))
        ).scalar_one_or_none()
        if role_id is None:
            return False
        exists = (
            await self._session.execute(
                select(UserRole.user_id).where(UserRole.user_id == user_id, UserRole.role_id == role_id)
            )
        ).first()
        if exists is None:
            self._session.add(UserRole(user_id=user_id, role_id=role_id))
            await self._session.flush()
        return True


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, user_id: int, token_hash: str, expires_at: datetime, created_at: datetime) -> None:
        self._session.add(
            RefreshToken(
                user_id=user_id,
                token_hash=token_hash,
                expires_at=expires_at,
                created_at=created_at,
            )
        )
        await self._session.flush()

    async def get_by_hash(self, token_hash: str) -> RefreshTokenRecord | None:
        row = (
            await self._session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        ).scalar_one_or_none()
        if row is None:
            return None
        return RefreshTokenRecord(row.id, row.user_id, row.expires_at, row.revoked_at)

    async def revoke(self, token_id: int, at: datetime) -> bool:
        """Revoke one token if it is still active; False when it was already revoked."""
        result = await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.id == token_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=at)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def revoke_all(self, user_id: int, at: datetime) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=at)
        )
