"""migrate-central-data: idempotency, dry-run, mapping and skipping (agent.md §5.9, §7.8)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.cli.main import build_parser, format_report
from app.models import Carousel, CarouselBreak, Plant, User, UserRole
from app.services.central_migration import CentralMigrationService
from tests.repositories.conftest import make_engine, seed_roles

LEGACY_DDL = [
    # Deliberately no use_device_id and no short_name on carousals: the legacy schema varied.
    """CREATE TABLE plants (id INTEGER PRIMARY KEY, erp_id TEXT, short_name TEXT, plant_name TEXT,
       zone TEXT, host_ip TEXT, db_user TEXT, db_password TEXT, db_database TEXT)""",
    """CREATE TABLE carousals (id INTEGER PRIMARY KEY, plant_id INT, carousal_id INT, heads INT,
       rated_productivity INT, start_time TEXT, stop_time TEXT)""",
    "CREATE TABLE breaks (id INTEGER PRIMARY KEY, plant_id INT, carousal_id INT, start_time TEXT, stop_time TEXT)",
    "CREATE TABLE options (name TEXT PRIMARY KEY, value TEXT, created_at INT, updated_at INT, deleted_at INT)",
    """CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, username TEXT, password_hash TEXT,
       active INT, force_pass_reset INT, fullname TEXT, phone TEXT)""",
    "CREATE TABLE auth_groups (id INTEGER PRIMARY KEY, name TEXT, description TEXT)",
    "CREATE TABLE auth_groups_users (group_id INT, user_id INT)",
]
LEGACY_DATA = [
    "INSERT INTO plants VALUES (1,'E1','anantapur','Anantapur','South','10.0.0.1','ro','pw1','ccc')",
    "INSERT INTO plants VALUES (2,'E2','kochi','Kochi','South','10.0.0.2','ro','pw2','ccc')",
    "INSERT INTO carousals VALUES (1,1,1,72,3000,'14:00:00','22:30:00')",
    "INSERT INTO carousals VALUES (2,1,2,24,1500,NULL,NULL)",
    "INSERT INTO carousals VALUES (3,2,1,NULL,1800,NULL,NULL)",
    "INSERT INTO carousals VALUES (4,2,2,24,1800,'30:00:00',NULL)",
    "INSERT INTO breaks VALUES (1,1,1,'19:00:00','19:30:00')",
    "INSERT INTO breaks VALUES (2,1,1,'10:00:00',NULL)",
    "INSERT INTO options VALUES ('site','prodash',1,2,NULL)",
    "INSERT INTO users VALUES (1,'a@x.org','alice','$2y$10$hash1',1,0,'Alice A','111')",
    "INSERT INTO users VALUES (2,'b@x.org','bob','$2y$10$hash2',0,1,'Bob B',NULL)",
    "INSERT INTO auth_groups VALUES (1,'admin','Admins')",
    "INSERT INTO auth_groups VALUES (2,'officers','Officers')",
    "INSERT INTO auth_groups VALUES (3,'security','Gate')",
    "INSERT INTO auth_groups VALUES (4,'mystery','???')",
    "INSERT INTO auth_groups_users VALUES (1,1)",
    "INSERT INTO auth_groups_users VALUES (3,2)",
    "INSERT INTO auth_groups_users VALUES (4,2)",
    "INSERT INTO auth_groups_users VALUES (1,99)",
]


@pytest.fixture
async def legacy() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        for stmt in [*LEGACY_DDL, *LEGACY_DATA]:
            await conn.execute(text(stmt))
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def target() -> AsyncIterator[AsyncSession]:
    engine = await make_engine()
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await seed_roles(session)
        yield session
    await engine.dispose()


async def _count(session: AsyncSession, model: type) -> int:
    return len((await session.execute(select(model))).all())


async def test_dry_run_reports_but_writes_nothing(legacy: AsyncSession, target: AsyncSession) -> None:
    report = await CentralMigrationService(legacy, target).run(dry_run=True)
    assert report.dry_run is True
    assert report.tables["plants"].inserted == 2
    assert report.tables["users"].inserted == 2
    assert await _count(target, Plant) == 0
    assert await _count(target, User) == 0


async def test_confirm_copies_data_with_ids_preserved(legacy: AsyncSession, target: AsyncSession) -> None:
    report = await CentralMigrationService(legacy, target).run(dry_run=False)

    plants = (await target.execute(select(Plant).order_by(Plant.id))).scalars().all()
    assert [(p.id, p.short_name, p.use_device_id) for p in plants] == [
        (1, "anantapur", False),
        (2, "kochi", False),
    ]
    assert plants[0].db_password == "pw1"  # copied unchanged (D-23)

    carousels = (await target.execute(select(Carousel).order_by(Carousel.id))).scalars().all()
    assert [c.id for c in carousels] == [1, 2]  # id 3 (NULL heads) and 4 (30:00:00) skipped
    assert str(carousels[0].start_time) == "14:00:00"
    assert report.tables["carousals"].skipped == 2

    breaks = (await target.execute(select(CarouselBreak))).scalars().all()
    assert [b.id for b in breaks] == [1]
    assert report.tables["breaks"].skipped == 1

    users = (await target.execute(select(User).order_by(User.id))).scalars().all()
    assert [(u.username, u.active, u.force_pass_reset) for u in users] == [
        ("alice", True, False),
        ("bob", False, True),
    ]
    assert users[0].password_hash == "$2y$10$hash1"


async def test_group_memberships_map_to_roles_and_skip_unmapped(
    legacy: AsyncSession, target: AsyncSession
) -> None:
    report = await CentralMigrationService(legacy, target).run(dry_run=False)
    pairs = (
        await target.execute(
            text(
                "SELECT u.username, r.name FROM user_roles ur "
                "JOIN users u ON u.id = ur.user_id "
                "JOIN roles r ON r.id = ur.role_id ORDER BY u.username"
            )
        )
    ).all()
    assert [tuple(p) for p in pairs] == [("alice", "admin"), ("bob", "viewer")]
    assert report.tables["user_roles"].inserted == 2
    assert report.tables["user_roles"].skipped == 2  # 'mystery' group and unknown user 99
    assert await _count(target, UserRole) == 2


async def test_rerun_is_idempotent_and_picks_up_changes(legacy: AsyncSession, target: AsyncSession) -> None:
    await CentralMigrationService(legacy, target).run(dry_run=False)
    second = await CentralMigrationService(legacy, target).run(dry_run=False)
    for name in ("plants", "carousals", "breaks", "options", "users", "user_roles"):
        t = second.tables[name]
        assert (t.inserted, t.updated) == (0, 0), name
    assert second.tables["plants"].unchanged == 2

    await legacy.execute(text("UPDATE plants SET zone='North' WHERE id=2"))
    await legacy.execute(text("INSERT INTO breaks VALUES (3,1,2,'11:00:00','11:10:00')"))
    third = await CentralMigrationService(legacy, target).run(dry_run=False)
    assert third.tables["plants"].updated == 1
    assert third.tables["breaks"].inserted == 1
    assert await _count(target, Plant) == 2
    zone = (await target.execute(select(Plant.zone).where(Plant.id == 2))).scalar_one()
    assert zone == "North"


async def test_missing_source_tables_are_tolerated(target: AsyncSession) -> None:
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with async_sessionmaker(engine)() as empty_source:
        report = await CentralMigrationService(empty_source, target).run(dry_run=False)
    await engine.dispose()
    assert all(t.inserted == 0 for t in report.tables.values())


def test_report_format_never_contains_row_values() -> None:
    from app.services.central_migration import MigrationReport

    report = MigrationReport(dry_run=True)
    report.table("plants").inserted = 3
    out = format_report(report)
    assert "DRY RUN" in out and "plants" in out and "inserted=3" in out


def test_cli_defaults_to_dry_run() -> None:
    args = build_parser().parse_args(["migrate-central-data"])
    assert args.confirm is False
    assert build_parser().parse_args(["migrate-central-data", "--confirm"]).confirm is True
