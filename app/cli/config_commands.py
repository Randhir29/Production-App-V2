"""CLI commands that load deployment configuration into the central DB and static YAML.

All commands are dry runs unless ``--confirm`` is given, and none of them prints credentials.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.db import create_central_engine
from app.domain.carousel_csv import (
    CsvParseResult,
    build_static_carousal_config,
    parse_carousel_csv,
    render_carousal_config_yaml,
    replace_carousal_config_section,
)
from app.domain.php_connections import PhpParseResult, parse_database_php
from app.domain.static_config import parse_static_config
from app.repositories.central.config_import_repo import ConfigImportRepository
from app.services.config_import import ConfigImportService, ImportReport


def format_import_report(title: str, report: ImportReport, extra: list[str] | None = None) -> str:
    mode = "DRY RUN (nothing written)" if report.dry_run else "APPLIED"
    lines = [f"{title}: {mode}"]
    lines.append(
        f"  inserted={len(report.inserted)} updated={len(report.updated)} "
        f"unchanged={len(report.unchanged)} unmatched={len(report.unmatched)}"
    )
    if report.inserted:
        lines.append("  inserted: " + ", ".join(report.inserted))
    if report.updated:
        lines.append("  updated: " + ", ".join(report.updated))
    if report.unmatched:
        lines.append("  unmatched (no plant in the central DB): " + ", ".join(report.unmatched))
    lines.extend(f"  warning: {w}" for w in report.warnings)
    lines.extend(extra or [])
    return "\n".join(lines)


async def import_plant_connections(
    php_path: Path, *, confirm: bool, create_missing: bool
) -> tuple[ImportReport, PhpParseResult]:
    parsed = parse_database_php(php_path.read_text(encoding="utf-8", errors="replace"))
    settings = get_settings()
    engine = create_central_engine(settings)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            service = ConfigImportService(ConfigImportRepository(session))
            report = await service.import_plant_connections(
                parsed.connections,
                dry_run=not confirm,
                create_missing=create_missing,
                expected_port=settings.plant_db_port,
            )
    finally:
        await engine.dispose()
    return report, parsed


async def import_carousel_config(csv_path: Path, *, confirm: bool) -> tuple[ImportReport, CsvParseResult]:
    parsed = parse_carousel_csv(csv_path.read_text(encoding="utf-8"))
    engine = create_central_engine(get_settings())
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            service = ConfigImportService(ConfigImportRepository(session))
            report = await service.import_carousels(parsed.carousels, dry_run=not confirm)
    finally:
        await engine.dispose()
    return report, parsed


async def export_static_config(
    csv_path: Path, output: Path, *, confirm: bool
) -> tuple[str, CsvParseResult, list[str], list[str]]:
    """Render ``carousal_config`` from the CSV using ``plants.erp_id`` to find short names.

    Returns the new YAML text, the parse result, unmatched sap ids and plants without config.
    The YAML is validated by loading it back before it is written.
    """
    parsed = parse_carousel_csv(csv_path.read_text(encoding="utf-8"))
    engine = create_central_engine(get_settings())
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            mapping = await ConfigImportService(ConfigImportRepository(session)).sap_to_short_name()
    finally:
        await engine.dispose()

    grouped, unmatched = build_static_carousal_config(parsed.carousels, mapping)
    new_text = replace_carousal_config_section(
        output.read_text(encoding="utf-8"), render_carousal_config_yaml(grouped)
    )
    config = parse_static_config(new_text)  # fail before writing anything invalid
    without_config = sorted(set(config.plants) - set(config.carousal_config))
    if confirm:
        output.write_text(new_text, encoding="utf-8")
    return new_text, parsed, unmatched, without_config
