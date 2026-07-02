"""Small connection probes for Bitfocus Companion.

This POC only validates that the configured ports are reachable. It does not
start a persistent listener yet.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


class CannotConnect(Exception):
    """Raised when Companion cannot be reached."""


@dataclass(frozen=True)
class CompanionProbeResult:
    """Result of probing Companion ports."""

    http_port_open: bool
    satellite_port_open: bool
    satellite_banner: str | None = None


async def _probe_tcp_port(host: str, port: int, timeout: float = 4.0) -> tuple[bool, str | None]:
    """Open and immediately close a TCP connection.

    For the Satellite port we opportunistically read a first line because
    Companion usually sends BEGIN/CAPS after connect. The POC does not depend on
    receiving this banner; successful TCP connect is enough.
    """
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False, None

    banner: str | None = None
    try:
        line = await asyncio.wait_for(reader.readline(), 0.75)
        if line:
            banner = line.decode("utf-8", errors="replace").strip()
    except (OSError, asyncio.TimeoutError):
        banner = None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

    return True, banner


async def async_probe_companion(
    host: str,
    http_port: int,
    satellite_port: int,
    require_satellite: bool = True,
) -> CompanionProbeResult:
    """Validate that Companion is reachable on configured ports."""
    http_open, _ = await _probe_tcp_port(host, http_port)
    satellite_open, banner = await _probe_tcp_port(host, satellite_port)

    if require_satellite and not satellite_open:
        raise CannotConnect(f"Cannot connect to Companion Satellite API at {host}:{satellite_port}")

    return CompanionProbeResult(
        http_port_open=http_open,
        satellite_port_open=satellite_open,
        satellite_banner=banner,
    )
