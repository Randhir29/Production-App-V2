"""Mapping of legacy myth/auth groups to the new roles (agent.md §7.8)."""

from __future__ import annotations

ROLE_ADMIN = "admin"
ROLE_OFFICER = "officer"
ROLE_VIEWER = "viewer"

ROLE_DESCRIPTIONS: dict[str, str] = {
    ROLE_ADMIN: "Full access, including admin endpoints",
    ROLE_OFFICER: "Read access to all plant data and reports",
    ROLE_VIEWER: "Read-only dashboard access",
}

GROUP_TO_ROLE: dict[str, str] = {
    "admin": ROLE_ADMIN,
    "officers": ROLE_OFFICER,
    "security": ROLE_VIEWER,
    "dataentry": ROLE_VIEWER,
}


def role_for_group(group_name: str) -> str | None:
    """Return the role for a legacy ``auth_groups.name``, or ``None`` if it has no mapping."""
    return GROUP_TO_ROLE.get(group_name.strip().lower())
