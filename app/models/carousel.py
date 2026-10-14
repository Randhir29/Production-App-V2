"""``carousals`` and ``breaks`` (agent.md §4.1). Legacy spelling kept for table names."""

from __future__ import annotations

from datetime import time

from sqlalchemy import Index, Integer, Time
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Carousel(Base):
    __tablename__ = "carousals"
    __table_args__ = (Index("ix_carousals_plant_id_carousal_id", "plant_id", "carousal_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plant_id: Mapped[int] = mapped_column(Integer)
    carousal_id: Mapped[int] = mapped_column(Integer)
    heads: Mapped[int] = mapped_column(Integer)
    rated_productivity: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[time | None] = mapped_column(Time)
    stop_time: Mapped[time | None] = mapped_column(Time)


class CarouselBreak(Base):
    __tablename__ = "breaks"
    __table_args__ = (Index("ix_breaks_plant_id_carousal_id", "plant_id", "carousal_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plant_id: Mapped[int] = mapped_column(Integer)
    carousal_id: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[time] = mapped_column(Time)
    stop_time: Mapped[time] = mapped_column(Time)
