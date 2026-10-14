"""Meta endpoints, E04-E05 (agent.md §5.8). No HTTP objects: callers pass plain strings."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from collections.abc import Callable, Sequence

from app.domain.static_config import StaticPlantConfig

logger = logging.getLogger("prodash.services.meta")

REVERSE_DNS_TIMEOUT_S = 1.0

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def _parse_ip(value: str | None) -> IPAddress | None:
    if not value:
        return None
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _trusted(ip: IPAddress, trusted_proxies: Sequence[str]) -> bool:
    for entry in trusted_proxies:
        try:
            if ip in ipaddress.ip_network(entry.strip(), strict=False):
                return True
        except ValueError:
            continue
    return False


def resolve_client_ip(
    peer: str | None, forwarded_for: str | None, trusted_proxies: Sequence[str]
) -> str | None:
    """Client IP, honouring ``X-Forwarded-For`` only when the peer is a trusted proxy (§5.8).

    The header is walked right to left, skipping trusted proxy hops; the first untrusted
    address is the client. Invalid entries end the walk and fall back to the peer.
    """
    peer_ip = _parse_ip(peer)
    if peer_ip is None:
        return None
    if not forwarded_for or not _trusted(peer_ip, trusted_proxies):
        return str(peer_ip)
    for hop in reversed([h for h in forwarded_for.split(",") if h.strip()]):
        ip = _parse_ip(hop)
        if ip is None:
            break
        if not _trusted(ip, trusted_proxies):
            return str(ip)
    return str(peer_ip)


def _reverse_dns(ip: str) -> str | None:
    try:
        return socket.gethostbyaddr(ip)[0]
    except (OSError, UnicodeError):
        return None


class MetaService:
    """Plant display name and client device name."""

    def __init__(
        self,
        static: StaticPlantConfig,
        trusted_proxies: Sequence[str] = (),
        resolver: Callable[[str], str | None] = _reverse_dns,
    ) -> None:
        self._static = static
        self._trusted = list(trusted_proxies)
        self._resolver = resolver

    def plant_name(self) -> str:
        """``getPlantName``: the static ``plant_name``."""
        return self._static.plant_name

    async def client_device(self, peer: str | None, forwarded_for: str | None = None) -> str | None:
        """``getDeviceName``: reverse DNS of the client IP, or ``None``.

        The lookup runs in the default thread pool with a 1 s timeout so a slow resolver
        cannot stall the event loop.
        """
        ip = resolve_client_ip(peer, forwarded_for, self._trusted)
        if ip is None:
            return None
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, self._resolver, ip), timeout=REVERSE_DNS_TIMEOUT_S
            )
        except TimeoutError:
            logger.warning("reverse dns timed out")
            return None
