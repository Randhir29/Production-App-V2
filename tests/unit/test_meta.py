"""MetaService and client-IP resolution (agent.md §5.8, E04/E05)."""

from __future__ import annotations

import time

import pytest

from app.domain.static_config import parse_static_config
from app.services import meta as meta_module
from app.services.meta import MetaService, resolve_client_ip

STATIC = parse_static_config(
    "plants: {}\nplant_name: Mysore LPG Plant\ncarousal_speed: {1: 50}\n"
    "interruption_to_exclude_minutes: 5\nplants_with_device_id: []\n"
)


def test_plant_name() -> None:
    assert MetaService(STATIC).plant_name() == "Mysore LPG Plant"


@pytest.mark.parametrize(
    ("peer", "xff", "trusted", "expected"),
    [
        ("203.0.113.9", "1.2.3.4", [], "203.0.113.9"),  # header ignored: peer not trusted
        ("10.0.0.5", "198.51.100.7", ["10.0.0.0/8"], "198.51.100.7"),
        ("10.0.0.5", "198.51.100.7, 10.0.0.8", ["10.0.0.0/8"], "198.51.100.7"),
        ("10.0.0.5", "6.6.6.6, 198.51.100.7", ["10.0.0.0/8"], "198.51.100.7"),  # spoofed prefix
        ("10.0.0.5", "garbage", ["10.0.0.0/8"], "10.0.0.5"),
        ("10.0.0.5", None, ["10.0.0.0/8"], "10.0.0.5"),
        ("10.0.0.5", "10.0.0.6", ["10.0.0.0/8"], "10.0.0.5"),
        (None, "1.2.3.4", ["10.0.0.0/8"], None),
        ("not-an-ip", None, [], None),
    ],
)
def test_resolve_client_ip(
    peer: str | None, xff: str | None, trusted: list[str], expected: str | None
) -> None:
    assert resolve_client_ip(peer, xff, trusted) == expected


async def test_client_device_uses_resolver_result() -> None:
    seen: list[str] = []

    def resolver(ip: str) -> str | None:
        seen.append(ip)
        return "scada-pc-7.local"

    service = MetaService(STATIC, ["10.0.0.0/8"], resolver)
    assert await service.client_device("10.0.0.5", "198.51.100.7") == "scada-pc-7.local"
    assert seen == ["198.51.100.7"]


async def test_client_device_none_when_unresolvable_or_no_peer() -> None:
    service = MetaService(STATIC, resolver=lambda ip: None)
    assert await service.client_device("203.0.113.9") is None
    assert await service.client_device(None) is None


async def test_client_device_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(meta_module, "REVERSE_DNS_TIMEOUT_S", 0.05)

    def slow(ip: str) -> str | None:
        time.sleep(0.4)
        return "late"

    assert await MetaService(STATIC, resolver=slow).client_device("203.0.113.9") is None
