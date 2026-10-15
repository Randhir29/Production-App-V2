"""``prodash`` command line (agent.md §5.9, E30). Admin tasks are CLI-only, never HTTP.

Usage::

    python -m app.cli.main migrate-central-data            # dry run (default)
    python -m app.cli.main migrate-central-data --confirm  # write
    python -m app.cli.main create-admin --username NAME --email ADDRESS   # prompts for password
    python -m app.cli.main hash-api-key --generate         # new E27 key + digest for SERVICE_API_KEYS
    python -m app.cli.main import-plant-connections --php app/Database.php [--create-missing] [--confirm]
    python -m app.cli.main import-carousel-config --csv FILE [--confirm]
    python -m app.cli.main export-static-config --csv FILE [--output config/static_plant_config.yaml] [--confirm]

``LEGACY_MYSQL_URL`` is the source; ``CENTRAL_DATABASE_URL`` is the target.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import secrets
import sys
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.cli.config_commands import (
    export_static_config,
    format_import_report,
    import_carousel_config,
    import_plant_connections,
)
from app.core.config import get_settings
from app.core.db import create_central_engine, selector_loop_factory
from app.repositories.central.user_repo import UserRepository
from app.services.admin_users import AdminUserError, AdminUserService
from app.services.auth import hash_token
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


async def create_admin(username: str, email: str, password: str, fullname: str | None) -> int:
    """Create an admin in the central DB; returns the user id."""
    engine = create_central_engine(get_settings())
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await AdminUserService(UserRepository(session)).create_admin(
                username, email, password, fullname
            )
    finally:
        await engine.dispose()


def _read_password(from_stdin: bool) -> str:
    """Never taken from argv: stdin (``--password-stdin``) or a hidden prompt, entered twice."""
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("passwords do not match")
    return first


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
    adm = sub.add_parser("create-admin", help="Create a user with the admin role")
    adm.add_argument("--username", required=True)
    adm.add_argument("--email", required=True)
    adm.add_argument("--fullname")
    adm.add_argument(
        "--password-stdin", action="store_true", help="Read the password from stdin, not a prompt"
    )
    key = sub.add_parser(
        "hash-api-key", help="Print the SHA-256 digest to put in SERVICE_API_KEYS (E27 keys)"
    )
    key.add_argument("--generate", action="store_true", help="Generate a new random key and print it once")
    imp = sub.add_parser(
        "import-plant-connections",
        help="Load plant DB host/user/password/database from the legacy Database.php into plants",
    )
    imp.add_argument("--php", type=Path, required=True, help="Path to the legacy Database.php")
    imp.add_argument("--create-missing", action="store_true", help="Insert plants not in the table")
    imp.add_argument("--confirm", action="store_true", help="Write changes (default: dry run)")

    car = sub.add_parser(
        "import-carousel-config",
        help="Load carousals and breaks from the corrected CSV, matching plants.erp_id = sap_id",
    )
    car.add_argument("--csv", type=Path, required=True)
    car.add_argument("--confirm", action="store_true", help="Write changes (default: dry run)")

    exp = sub.add_parser(
        "export-static-config",
        help="Regenerate carousal_config in the static YAML from the CSV (short names via erp_id)",
    )
    exp.add_argument("--csv", type=Path, required=True)
    exp.add_argument("--output", type=Path, default=Path("config/static_plant_config.yaml"))
    exp.add_argument("--confirm", action="store_true", help="Write the file (default: dry run)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "migrate-central-data":
        report = asyncio.run(migrate_central_data(confirm=args.confirm), loop_factory=selector_loop_factory)
        print(format_report(report))
        return 0
    if args.command == "create-admin":
        password = _read_password(args.password_stdin)
        try:
            user_id = asyncio.run(
                create_admin(args.username, args.email, password, args.fullname),
                loop_factory=selector_loop_factory,
            )
        except AdminUserError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"created admin user id={user_id} username={args.username}")
        return 0
    if args.command == "hash-api-key":
        if args.generate:
            key = secrets.token_urlsafe(32)
            print(f"key (store securely, shown once): {key}")
        else:
            key = getpass.getpass("API key: ")
        print(f"SERVICE_API_KEYS digest: {hash_token(key)}")
        return 0
    if args.command == "import-plant-connections":
        report, parsed = asyncio.run(
            import_plant_connections(args.php, confirm=args.confirm, create_missing=args.create_missing),
            loop_factory=selector_loop_factory,
        )
        extra = [f"  skipped non-plant groups: {', '.join(parsed.skipped_groups) or '-'}"]
        extra += [f"  problem: {m}" for m in parsed.problems]
        print(format_import_report("import-plant-connections", report, extra))
        return 0
    if args.command == "import-carousel-config":
        report, parsed = asyncio.run(
            import_carousel_config(args.csv, confirm=args.confirm),
            loop_factory=selector_loop_factory,
        )
        extra = [f"  skipped row: {m}" for m in parsed.problems]
        extra += [f"  note: {m}" for m in parsed.warnings]
        print(format_import_report("import-carousel-config", report, extra))
        return 1 if parsed.problems else 0
    if args.command == "export-static-config":
        _, parsed, unmatched, without = asyncio.run(
            export_static_config(args.csv, args.output, confirm=args.confirm),
            loop_factory=selector_loop_factory,
        )
        mode = "WRITTEN to " + str(args.output) if args.confirm else "DRY RUN (file not written)"
        print(f"export-static-config: {mode}")
        print(f"  carousels exported: {len(parsed.carousels) - len(unmatched)} rows matched")
        for label, items in (("unmatched sap_id", unmatched), ("plants without carousel config", without)):
            if items:
                print(f"  {label}: {', '.join(items)}")
        for m in parsed.problems:
            print(f"  skipped row: {m}")
        return 1 if parsed.problems else 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
