"""``options`` (agent.md §4.1): unused by features; kept so the data copy is lossless."""

from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Option(Base):
    __tablename__ = "options"

    name: Mapped[str] = mapped_column(String(191), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[int | None] = mapped_column(Integer)
    deleted_at: Mapped[int | None] = mapped_column(Integer)
