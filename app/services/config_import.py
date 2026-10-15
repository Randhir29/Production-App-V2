"""Configuration imports: plant connections and carousel config (agent.md §4.1, §5.9, D-11, D-23).

Both imports are dry-run by default: they do all the work in one transaction and roll back
unless ``dry_run`` is False. Reports contain names and counts only, never credentials.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time

from app.domain.carousel_config import parse_time
from app.domain.carousel_csv import CsvCarousel
from app.domain.php_connections import PhpConnection
from app.repositories.central.config_import_repo import ConfigImportRepository


@dataclass(slots=True)
class ImportReport:
    dry_run: bool
    inserted: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ConfigImportService:
    def __init__(self, repo: ConfigImportRepository) -> None:
        self._repo = repo

    async def _finish(self, report: ImportReport) -> ImportReport:
        if report.dry_run:
            await self._repo.rollback()
        else:
            await self._repo.commit()
        return report

    async def import_plant_connections(
        self,
        connections: list[PhpConnection],
        *,
        dry_run: bool,
        create_missing: bool = False,
        expected_port: int = 5432,
    ) -> ImportReport:
        """Write host, user, password and database of each PHP group into ``plants``.

        Plants are matched on ``short_name``. Unknown ones are reported as ``unmatched`` and only
        created with ``create_missing`` (their name and zone stay empty). A port different from
        ``PLANT_DB_PORT`` is reported, not stored: the central table has no port column.
        """
        report = ImportReport(dry_run=dry_run)
        by_short = {p.short_name: p for p in await self._repo.list_plants()}
        try:
            for conn in connections:
                if conn.port is not None and conn.port != expected_port:
                    report.warnings.append(
                        f"{conn.short_name}: port {conn.port} differs from PLANT_DB_PORT={expected_port}"
                    )
                plant = by_short.get(conn.short_name)
                if plant is None:
                    if not create_missing:
                        report.unmatched.append(conn.short_name)
                        continue
                    await self._repo.create_plant(
                        conn.short_name, conn.host, conn.user, conn.password, conn.database
                    )
                    report.inserted.append(conn.short_name)
                    continue
                same = (
                    plant.host_ip == conn.host
                    and plant.db_user == conn.user
                    and plant.db_password == conn.password
                    and plant.db_database == conn.database
                )
                if same:
                    report.unchanged.append(conn.short_name)
                    continue
                await self._repo.set_connection(plant.id, conn.host, conn.user, conn.password, conn.database)
                report.updated.append(conn.short_name)
        except Exception:
            await self._repo.rollback()
            raise
        return await self._finish(report)

    async def import_carousels(self, carousels: list[CsvCarousel], *, dry_run: bool) -> ImportReport:
        """Upsert ``carousals`` and replace ``breaks`` per carousel; plants match on ``erp_id``."""
        report = ImportReport(dry_run=dry_run)
        by_erp = {p.erp_id: p for p in await self._repo.list_plants() if p.erp_id}
        try:
            for c in carousels:
                label = f"{c.sap_id}/{c.carousal_id}"
                plant = by_erp.get(c.sap_id)
                if plant is None:
                    report.unmatched.append(label)
                    continue
                start, stop = parse_time(c.start), parse_time(c.end)
                assert start is not None and stop is not None
                carousel_state = await self._repo.upsert_carousel(
                    plant.id, c.carousal_id, c.heads, c.rated_productivity, start, stop
                )
                breaks_state = await self._repo.replace_breaks(
                    plant.id,
                    c.carousal_id,
                    [(_t(a), _t(b)) for a, b in c.breaks],
                )
                name = f"{plant.short_name}/{c.carousal_id}"
                if carousel_state == "inserted":
                    report.inserted.append(name)
                elif carousel_state == "updated" or breaks_state == "replaced":
                    report.updated.append(name)
                else:
                    report.unchanged.append(name)
        except Exception:
            await self._repo.rollback()
            raise
        return await self._finish(report)

    async def sap_to_short_name(self) -> dict[str, str]:
        """``plants.erp_id -> plants.short_name`` for the static-config export."""
        return {p.erp_id: p.short_name for p in await self._repo.list_plants() if p.erp_id}


def _t(value: str) -> time:
    parsed = parse_time(value)
    assert parsed is not None
    return parsed
