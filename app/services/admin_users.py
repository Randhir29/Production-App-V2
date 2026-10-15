"""Admin user provisioning for the ``create-admin`` CLI (agent.md §7.8, E30)."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime

from app.core.security import hash_password
from app.repositories.central.user_repo import UserRepository
from app.services.auth import utcnow

MIN_PASSWORD_LENGTH = 12
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")  # users.username is VARCHAR(30)
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AdminUserError(ValueError):
    """The admin account cannot be created; the message is safe to show to the operator."""


class AdminUserService:
    def __init__(self, users: UserRepository, clock: Callable[[], datetime] = utcnow) -> None:
        self._users = users
        self._clock = clock

    async def create_admin(
        self, username: str, email: str, password: str, fullname: str | None = None
    ) -> int:
        """Create an active user with the ``admin`` role; returns the new user id."""
        if not USERNAME_PATTERN.match(username):
            raise AdminUserError("username must be 3-30 characters of letters, digits, '_', '.', '-'")
        if len(email) > 255 or not EMAIL_PATTERN.match(email):
            raise AdminUserError("email address is not valid")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise AdminUserError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
        if await self._users.find_by_login(username) or await self._users.find_by_login(email):
            raise AdminUserError("a user with this username or email already exists")

        user_id = await self._users.create_user(
            email=email,
            username=username,
            password_hash=hash_password(password),
            fullname=fullname,
            now=self._clock(),
        )
        if not await self._users.assign_role(user_id, "admin"):
            raise AdminUserError("role 'admin' is missing; run the Alembic migrations first")
        await self._users.commit()
        return user_id
