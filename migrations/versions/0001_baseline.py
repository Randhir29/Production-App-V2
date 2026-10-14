"""Baseline central schema: plants, carousals, breaks, options, users, roles, user_roles,
refresh_tokens (agent.md §4.1, §7.8).

Legacy tables mirror the PHP schema one-to-one. The recommended constraints of §12.1 (unique
short_name, foreign keys, CHECKs) are intentionally absent until the owner signs them off.

Revision ID: 0001
Revises:
Create Date: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = [
    {"name": "admin", "description": "Full access, including admin endpoints"},
    {"name": "officer", "description": "Read access to all plant data and reports"},
    {"name": "viewer", "description": "Read-only dashboard access"},
]


def upgrade() -> None:
    op.create_table(
        "plants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("erp_id", sa.String(64)),
        sa.Column("short_name", sa.String(64), nullable=False),
        sa.Column("plant_name", sa.String(255)),
        sa.Column("zone", sa.String(64)),
        sa.Column("host_ip", sa.String(255)),
        sa.Column("db_user", sa.String(128)),
        sa.Column("db_password", sa.String(255)),
        sa.Column("db_database", sa.String(128)),
        sa.Column("use_device_id", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id", name="pk_plants"),
    )
    op.create_index("ix_plants_short_name", "plants", ["short_name"])

    op.create_table(
        "carousals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("plant_id", sa.Integer(), nullable=False),
        sa.Column("carousal_id", sa.Integer(), nullable=False),
        sa.Column("heads", sa.Integer(), nullable=False),
        sa.Column("rated_productivity", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time()),
        sa.Column("stop_time", sa.Time()),
        sa.PrimaryKeyConstraint("id", name="pk_carousals"),
    )
    op.create_index("ix_carousals_plant_id_carousal_id", "carousals", ["plant_id", "carousal_id"])

    op.create_table(
        "breaks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("plant_id", sa.Integer(), nullable=False),
        sa.Column("carousal_id", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("stop_time", sa.Time(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_breaks"),
    )
    op.create_index("ix_breaks_plant_id_carousal_id", "breaks", ["plant_id", "carousal_id"])

    op.create_table(
        "options",
        sa.Column("name", sa.String(191), nullable=False),
        sa.Column("value", sa.Text()),
        sa.Column("created_at", sa.Integer()),
        sa.Column("updated_at", sa.Integer()),
        sa.Column("deleted_at", sa.Integer()),
        sa.PrimaryKeyConstraint("name", name="pk_options"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("username", sa.String(30)),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("reset_hash", sa.String(255)),
        sa.Column("reset_at", sa.DateTime()),
        sa.Column("reset_expires", sa.DateTime()),
        sa.Column("activate_hash", sa.String(255)),
        sa.Column("status", sa.String(255)),
        sa.Column("status_message", sa.String(255)),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("force_pass_reset", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("permissions", sa.Text()),
        sa.Column("deleted_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.Column("fullname", sa.String(255)),
        sa.Column("phone", sa.String(32)),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )

    roles = op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("description", sa.String(255)),
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )
    op.bulk_insert(roles, ROLES)

    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_roles_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], name="fk_user_roles_role_id_roles", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "role_id", name="pk_user_roles"),
    )

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_refresh_tokens_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_refresh_tokens"),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_table("refresh_tokens")
    op.drop_table("user_roles")
    op.drop_table("roles")
    op.drop_table("users")
    op.drop_table("options")
    op.drop_table("breaks")
    op.drop_table("carousals")
    op.drop_table("plants")
