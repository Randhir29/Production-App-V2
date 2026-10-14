"""``prodash`` command line (agent.md §5.9, E30). Admin tasks are CLI-only, never HTTP.

Usage::

    python -m app.cli.main migrate-central-data            # dry run (default)
    python -m app.cli.main migrate-central-data --confirm  # write

``LEGACY_MYSQL_URL`` is the source; ``CENTRAL_DATABASE_URL`` is the target.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import create_central_engine, selector_loop_factory
from app.services.central_migration import CentralMigrationService, MigrationReport


def format_report(report: MigrationReport) -> str:
    """Human-readable summary; contains counts and row identifiers only, never credentials."""
    mode = "DRY RUN (nothing written)" if report.dry_run else "APPLIED"
    lines = [f"migrate-central-data: {mode}"]
    for name, t in report.tables.items():
        lines.append(
            f"  {name:<12} inserted={t.inserted} updated={t.updated} "
            f"unchanged={t.unchanged} skipped={t.skipped}"
        )
    lines.extend(f"  warning: {w}" for w in report.warnings)
    return "\n".join(lines)


async def migrate_central_data(*, confirm: bool) -> MigrationReport:
    settings = get_settings()
    if settings.legacy_mysql_url is None:
        raise SystemExit("LEGACY_MYSQL_URL is not set")
    source_engine = create_async_engine(settings.legacy_mysql_url.get_secret_value(), pool_pre_ping=True)
    target_engine = create_central_engine(settings)
    try:
        async with (
            async_sessionmaker(source_engine, expire_on_commit=False)() as source,
            async_sessionmaker(target_engine, expire_on_commit=False)() as target,
        ):
            return await _run(source, target, dry_run=not confirm)
    finally:
        await source_engine.dispose()
        await target_engine.dispose()


async def _run(source: AsyncSession, target: AsyncSession, *, dry_run: bool) -> MigrationReport:
    return await CentralMigrationService(source, target).run(dry_run=dry_run)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prodash", description="ProDash admin commands")
    sub = parser.add_subparsers(dest="command", required=True)
    mig = sub.add_parser(
        "migrate-central-data",
        help="Copy plants, carousels, breaks, options, users and roles from the legacy MySQL DB",
    )
    mig.add_argument(
        "--confirm",
        action="store_true",
        help="Write changes. Without it the command is a dry run that rolls back.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "migrate-central-data":
        report = asyncio.run(migrate_central_data(confirm=args.confirm), loop_factory=selector_loop_factory)
        print(format_report(report))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
