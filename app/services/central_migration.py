"""Legacy -> central data copy (agent.md §5.9, §7.8, E30 ``migrate-central-data``).

Primary keys are preserved so the copy is idempotent and foreign references stay valid.
Plant DB passwords are copied unchanged (plaintext, D-23): encrypting them is a separate
owner-approved step. No credential value is ever logged or placed in the report.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.carousel_config import parse_time
from app.domain.legacy_mapping import role_for_group
from app.models import Carousel, CarouselBreak, Option, Plant, User, UserRole
from app.repositories.central.migration_repo import (
    LegacySourceRepository,
    TargetRepository,
    UpsertCounts,
)

logger = logging.getLogger("prodash.services.central_migration")

PLANT_COLUMNS = (
    "id", "erp_id", "short_name", "plant_name", "zone", "host_ip",
    "db_user", "db_password", "db_database", "use_device_id",
)  # fmt: skip
CAROUSEL_COLUMNS = ("id", "plant_id", "carousal_id", "heads", "rated_productivity", "start_time", "stop_time")
BREAK_COLUMNS = ("id", "plant_id", "carousal_id", "start_time", "stop_time")
OPTION_COLUMNS = ("name", "value", "created_at", "updated_at", "deleted_at")
USER_COLUMNS = (
    "id", "email", "username", "password_hash", "reset_hash", "reset_at", "reset_expires",
    "activate_hash", "status", "status_message", "active", "force_pass_reset", "permissions",
    "deleted_at", "created_at", "updated_at", "fullname", "phone",
)  # fmt: skip
BOOL_USER_COLUMNS = ("active", "force_pass_reset")


@dataclass(slots=True)
class TableReport:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0

    def add(self, counts: UpsertCounts) -> None:
        self.inserted += counts.inserted
        self.updated += counts.updated
        self.unchanged += counts.unchanged


@dataclass(slots=True)
class MigrationReport:
    dry_run: bool
    tables: dict[str, TableReport] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def table(self, name: str) -> TableReport:
        return self.tables.setdefault(name, TableReport())


class CentralMigrationService:
    """Copies plants, carousals, breaks, options, users and group memberships."""

    def __init__(self, source: AsyncSession, target: AsyncSession) -> None:
        self._source = LegacySourceRepository(source)
        self._target = TargetRepository(target)
        self._target_session = target

    async def run(self, *, dry_run: bool) -> MigrationReport:
        """Copy everything in one target transaction; roll back when ``dry_run``."""
        report = MigrationReport(dry_run=dry_run)
        try:
            await self._copy_plants(report)
            await self._copy_carousels(report)
            await self._copy_breaks(report)
            await self._copy_options(report)
            await self._copy_users(report)
            await self._copy_memberships(report)
            await self._target.reset_sequences([Plant, Carousel, CarouselBreak, User])
        except Exception:
            await self._target_session.rollback()
            raise
        if dry_run:
            await self._target_session.rollback()
        else:
            await self._target_session.commit()
        return report

    async def _copy_plants(self, report: MigrationReport) -> None:
        rows = await self._source.fetch("plants", PLANT_COLUMNS, order_by="id")
        for row in rows:
            row["use_device_id"] = bool(row["use_device_id"])
        report.table("plants").add(await self._target.upsert(Plant, rows, ("id",)))

    async def _copy_carousels(self, report: MigrationReport) -> None:
        tr = report.table("carousals")
        rows: list[dict[str, Any]] = []
        for row in await self._source.fetch("carousals", CAROUSEL_COLUMNS, order_by="id"):
            try:
                row["start_time"] = parse_time(row["start_time"])
                row["stop_time"] = parse_time(row["stop_time"])
            except ValueError as exc:
                self._skip(report, tr, f"carousals id={row['id']}: {exc}")
                continue
            if row["heads"] is None or row["rated_productivity"] is None:
                self._skip(report, tr, f"carousals id={row['id']}: heads/rated_productivity is NULL")
                continue
            row["heads"] = int(row["heads"])
            row["rated_productivity"] = int(row["rated_productivity"])
            rows.append(row)
        tr.add(await self._target.upsert(Carousel, rows, ("id",)))

    async def _copy_breaks(self, report: MigrationReport) -> None:
        tr = report.table("breaks")
        rows: list[dict[str, Any]] = []
        for row in await self._source.fetch("breaks", BREAK_COLUMNS, order_by="id"):
            try:
                row["start_time"] = parse_time(row["start_time"])
                row["stop_time"] = parse_time(row["stop_time"])
            except ValueError as exc:
                self._skip(report, tr, f"breaks id={row['id']}: {exc}")
                continue
            if row["start_time"] is None or row["stop_time"] is None:
                self._skip(report, tr, f"breaks id={row['id']}: NULL time")
                continue
            rows.append(row)
        tr.add(await self._target.upsert(CarouselBreak, rows, ("id",)))

    async def _copy_options(self, report: MigrationReport) -> None:
        rows = await self._source.fetch("options", OPTION_COLUMNS)
        report.table("options").add(await self._target.upsert(Option, rows, ("name",)))

    async def _copy_users(self, report: MigrationReport) -> None:
        rows = await self._source.fetch("users", USER_COLUMNS, order_by="id")
        for row in rows:
            for col in BOOL_USER_COLUMNS:
                row[col] = bool(row[col])
        report.table("users").add(await self._target.upsert(User, rows, ("id",)))

    async def _copy_memberships(self, report: MigrationReport) -> None:
        tr = report.table("user_roles")
        groups = {g["id"]: g["name"] for g in await self._source.fetch("auth_groups", ("id", "name"))}
        members = await self._source.fetch("auth_groups_users", ("group_id", "user_id"))
        role_ids = await self._target.role_ids()
        user_ids = {u["id"] for u in await self._source.fetch("users", ("id",))}
        wanted: dict[tuple[int, int], dict[str, int]] = {}
        for m in members:
            group_name = groups.get(m["group_id"])
            role = role_for_group(group_name) if group_name else None
            if role is None or role not in role_ids:
                self._skip(report, tr, f"auth_groups_users group_id={m['group_id']}: no role mapping")
                continue
            if m["user_id"] not in user_ids:
                self._skip(report, tr, f"auth_groups_users user_id={m['user_id']}: unknown user")
                continue
            pair = (int(m["user_id"]), role_ids[role])
            wanted[pair] = {"user_id": pair[0], "role_id": pair[1]}
        tr.add(await self._target.upsert(UserRole, list(wanted.values()), ("user_id", "role_id")))

    @staticmethod
    def _skip(report: MigrationReport, tr: TableReport, message: str) -> None:
        tr.skipped += 1
        report.warnings.append(message)
        logger.warning("migration skipped row: %s", message)
