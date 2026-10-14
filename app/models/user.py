"""Users, roles, role membership and refresh tokens (agent.md §4.1, §7.8)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    """myth/auth ``users`` columns plus the custom ``fullname`` and ``phone``."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    username: Mapped[str | None] = mapped_column(String(30), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    reset_hash: Mapped[str | None] = mapped_column(String(255))
    reset_at: Mapped[datetime | None] = mapped_column(DateTime)
    reset_expires: Mapped[datetime | None] = mapped_column(DateTime)
    activate_hash: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str | None] = mapped_column(String(255))
    status_message: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    force_pass_reset: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    permissions: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    fullname: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)
    description: Mapped[str | None] = mapped_column(String(255))


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)


class RefreshToken(Base):
    """Hashed refresh tokens so they can be revoked and rotated (§7.8)."""

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime)
