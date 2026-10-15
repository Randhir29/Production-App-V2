"""Parser for the legacy CodeIgniter ``Database.php`` connection groups. Pure, no I/O.

Only the structural values needed to populate the central ``plants`` table are extracted. The
password is excluded from ``repr`` so it cannot leak through logs or test output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Groups that are not plants: the MySQL default, the PHPUnit group, the legacy default
# Postgre connection and the test plant (agent.md §4.3).
NON_PLANT_GROUPS = frozenset({"default", "tests", "kosan", "testplant"})

_GROUP = re.compile(r"public\s+\$([A-Za-z0-9_]+)\s*=\s*\[(.*?)\]\s*;", re.DOTALL)
_PAIR = re.compile(
    r"""['"](?P<key>\w+)['"]\s*=>\s*(?:'(?P<sq>(?:[^'\\]|\\.)*)'|"(?P<dq>(?:[^"\\]|\\.)*)"|(?P<num>-?\d+))""",
)
_COMMENT_LINE = re.compile(r"^\s*(//|#).*$", re.MULTILINE)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


@dataclass(frozen=True, slots=True)
class PhpConnection:
    short_name: str
    host: str
    user: str
    password: str = field(repr=False)
    database: str
    port: int | None


@dataclass(slots=True)
class PhpParseResult:
    connections: list[PhpConnection] = field(default_factory=list)
    skipped_groups: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _unescape(value: str) -> str:
    return re.sub(r"\\(['\"\\])", r"\1", value)


def parse_database_php(source: str) -> PhpParseResult:
    """Extract every PostgreSQL group except the non-plant groups."""
    cleaned = _BLOCK_COMMENT.sub("", _COMMENT_LINE.sub("", source))
    result = PhpParseResult()
    for match in _GROUP.finditer(cleaned):
        name, body = match.group(1), match.group(2)
        values: dict[str, str | int] = {}
        for pair in _PAIR.finditer(body):
            if pair.group("num") is not None:
                values[pair.group("key")] = int(pair.group("num"))
            else:
                raw = pair.group("sq") if pair.group("sq") is not None else pair.group("dq")
                values[pair.group("key")] = _unescape(raw or "")
        if str(values.get("DBDriver", "")).lower() != "postgre":
            continue
        if name in NON_PLANT_GROUPS:
            result.skipped_groups.append(name)
            continue
        host, user = str(values.get("hostname", "")), str(values.get("username", ""))
        database = str(values.get("database", ""))
        if not (host and user and database):
            result.problems.append(f"group {name!r}: hostname, username or database is empty")
            continue
        port = values.get("port")
        result.connections.append(
            PhpConnection(
                short_name=name,
                host=host,
                user=user,
                password=str(values.get("password", "")),
                database=database,
                port=int(port) if isinstance(port, int) else None,
            )
        )
    return result
