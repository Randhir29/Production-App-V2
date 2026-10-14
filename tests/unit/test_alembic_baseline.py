"""The Alembic baseline must build exactly the schema the ORM models declare."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

import app.models  # noqa: F401
from app.db.base import Base

ROOT = Path(__file__).resolve().parents[2]


def _config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture
def migrated_db(tmp_path: Path) -> sa.Engine:
    path = tmp_path / "central.db"
    command.upgrade(_config(f"sqlite+aiosqlite:///{path}"), "head")
    return sa.create_engine(f"sqlite:///{path}")


def test_upgrade_matches_orm_metadata(migrated_db: sa.Engine) -> None:
    insp = sa.inspect(migrated_db)
    names = set(insp.get_table_names()) - {"alembic_version"}
    assert names == set(Base.metadata.tables)
    for name, table in Base.metadata.tables.items():
        cols = {c["name"]: c for c in insp.get_columns(name)}
        assert set(cols) == {c.name for c in table.columns}, name
        for col in table.columns:
            assert cols[col.name]["nullable"] == (col.nullable and not col.primary_key), f"{name}.{col.name}"
        index_names = {i["name"] for i in insp.get_indexes(name)}
        assert {i.name for i in table.indexes} <= index_names, name


def test_upgrade_seeds_roles(migrated_db: sa.Engine) -> None:
    with migrated_db.connect() as conn:
        names = {r[0] for r in conn.execute(sa.text("SELECT name FROM roles"))}
    assert names == {"admin", "officer", "viewer"}


def test_downgrade_removes_everything(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'd.db'}"
    command.upgrade(_config(url), "head")
    command.downgrade(_config(url), "base")
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'd.db'}")
    assert set(sa.inspect(engine).get_table_names()) <= {"alembic_version"}


@pytest.mark.parametrize("url", ["postgresql+psycopg://u:p@h/db", "mysql+aiomysql://u:p@h/db"])
def test_offline_sql_renders_for_both_dialects(url: str, capsys: pytest.CaptureFixture[str]) -> None:
    command.upgrade(_config(url), "head", sql=True)
    sql = capsys.readouterr().out
    for table in Base.metadata.tables:
        assert f"CREATE TABLE {table} " in sql
    assert "INSERT INTO roles" in sql
