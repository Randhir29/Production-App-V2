"""``plants`` (agent.md §4.1). Mirrors the legacy table; §12.1 constraints need owner sign-off."""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Plant(Base):
    __tablename__ = "plants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    erp_id: Mapped[str | None] = mapped_column(String(64))
    short_name: Mapped[str] = mapped_column(String(64), index=True)
    plant_name: Mapped[str | None] = mapped_column(String(255))
    zone: Mapped[str | None] = mapped_column(String(64))
    host_ip: Mapped[str | None] = mapped_column(String(255))
    db_user: Mapped[str | None] = mapped_column(String(128))
    db_password: Mapped[str | None] = mapped_column(String(255))  # plaintext today, D-23
    db_database: Mapped[str | None] = mapped_column(String(128))
    use_device_id: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
