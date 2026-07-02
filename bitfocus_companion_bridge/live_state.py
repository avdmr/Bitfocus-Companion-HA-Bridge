"""Short-lived live-state verification helpers for Bitfocus Companion Bridge.

The POC still does not create Home Assistant entities and does not start a
persistent observer. This module opens a temporary read-only Satellite
connection during the page-import flow to verify that the selected live-state
backend can read the imported page.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import asdict, dataclass, field
import re
import shlex
from typing import Any

from .const import OBSERVER_BACKEND_SUBSCRIPTION, OBSERVER_BACKEND_SURFACE
from .entity_model import companion_location, observer_surface_metadata


class LiveStateVerificationError(Exception):
    """Raised for unexpected live-state verification failures."""


@dataclass(frozen=True)
class LiveButtonState:
    """One rendered Companion button state received from the Satellite API."""

    source: str
    location: str | None
    page: int | None
    row: int | None
    column: int | None
    text: str = ""
    color: str | None = None
    text_color: str | None = None
    font_size: str | None = None
    pressed: bool | None = None
    raw_command: str = ""
    raw: dict[str, str] = field(default_factory=dict)

    def as_storage_data(self) -> dict[str, Any]:
        """Return JSON-serializable state data."""
        return asdict(self)


@dataclass(frozen=True)
class LiveStateVerificationResult:
    """Result stored with the page import."""

    success: bool
    backend: str
    expected_page: int
    method: str
    reason: str
    companion_url: str
    instructions: str
    observed_page: int | None = None
    subscriptions_supported: bool | None = None
    observer_surface: dict[str, str] | None = None
    sample_location: str | None = None
    sample_state: LiveButtonState | None = None

    def as_storage_data(self) -> dict[str, Any]:
        """Return JSON-serializable verification data."""
        data = asdict(self)
        if self.sample_state is not None:
            data["sample_state"] = self.sample_state.as_storage_data()
        return data


def parse_protocol_line(line: str) -> tuple[str, dict[str, str], list[str]]:
    """Parse one Companion Satellite protocol line."""
    line = line.strip()
    if not line:
        return "", {}, []

    try:
        parts = shlex.split(line)
    except ValueError:
        parts = line.split()

    command = parts[0]
    args: dict[str, str] = {}
    extras: list[str] = []
    for part in parts[1:]:
        if "=" in part:
            key, value = part.split("=", 1)
            args[key] = value
        else:
            extras.append(part)
    return command, args, extras


def decode_b64(value: str | None) -> str:
    """Decode Companion base64 text fields."""
    if not value:
        return ""
    try:
        padded = value + "=" * (-len(value) % 4)
        return base64.b64decode(padded).decode("utf-8", errors="replace")
    except Exception:
        return ""


def parse_bool(value: str | None) -> bool | None:
    """Parse a Companion boolean-ish field."""
    if value is None:
        return None
    lowered = str(value).lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return None


def normalize_protocol_color(value: str | None) -> str | None:
    """Normalize Satellite color fields to #rrggbb where possible."""
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    if value.startswith("#"):
        return value.lower()
    if re.fullmatch(r"[0-9a-fA-F]{6}", value):
        return f"#{value.lower()}"
    try:
        return f"#{int(value):06x}"
    except Exception:
        return value


def parse_location(value: str | None) -> tuple[str | None, int | None, int | None, int | None]:
    """Parse page/row/column."""
    if not value:
        return None, None, None, None
    try:
        page_s, row_s, col_s = value.split("/")
        return value, int(page_s), int(row_s), int(col_s)
    except Exception:
        return value, None, None, None


def state_from_args(source: str, args: dict[str, str], *, location_override: str | None = None, raw_command: str = "") -> LiveButtonState:
    """Create a LiveButtonState from KEY-STATE or SUB-STATE args."""
    location_s, page, row, column = parse_location(location_override or args.get("LOCATION"))
    return LiveButtonState(
        source=source,
        location=location_s,
        page=page,
        row=row,
        column=column,
        text=decode_b64(args.get("TEXT")),
        color=normalize_protocol_color(args.get("COLOR")),
        text_color=normalize_protocol_color(args.get("TEXTCOLOR")),
        font_size=args.get("FONT_SIZE"),
        pressed=parse_bool(args.get("PRESSED")),
        raw_command=raw_command,
        raw=dict(args),
    )


class TemporarySatelliteClient:
    """Small helper for one temporary Satellite TCP connection."""

    def __init__(self, host: str, port: int, *, timeout: float = 8.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None

    async def __aenter__(self) -> "TemporarySatelliteClient":
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=self.timeout,
        )
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        try:
            await self.send("QUIT")
        except Exception:
            pass
        if self.writer is not None:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except OSError:
                pass

    async def send(self, line: str) -> None:
        """Send one Satellite protocol line."""
        if self.writer is None:
            raise LiveStateVerificationError("Satellite client is not connected")
        self.writer.write((line + "\n").encode("utf-8"))
        await self.writer.drain()

    async def readline(self, timeout: float) -> tuple[str, dict[str, str], list[str], str]:
        """Read and parse one protocol line."""
        if self.reader is None:
            raise LiveStateVerificationError("Satellite client is not connected")
        raw = await asyncio.wait_for(self.reader.readline(), timeout=timeout)
        if not raw:
            raise LiveStateVerificationError("Companion closed the Satellite connection")
        line = raw.decode("utf-8", errors="replace").strip()
        command, args, extras = parse_protocol_line(line)
        return command, args, extras, line

    async def handle_common(self, command: str, extras: list[str]) -> bool:
        """Handle common ping/pong traffic."""
        if command == "PING":
            await self.send("PONG " + " ".join(extras))
            return True
        return command in {"BEGIN", "PONG"}


def _companion_url(host: str, web_ui_port: int, path: str) -> str:
    return f"http://{host}:{web_ui_port}{path}"


def _surface_instructions(host: str, web_ui_port: int, page_number: int) -> tuple[str, str]:
    url = _companion_url(host, web_ui_port, "/surfaces/configured")
    text = (
        f"Open Companion surface settings. For the Home Assistant observer surface for page {page_number}, "
        f"set Home Page / Current Page to page {page_number}. Also enable 'Restrict pages accessible "
        f"to this surface' and allow only page {page_number}. After changing it, submit this step again."
    )
    return url, text


def _subscription_instructions(host: str, web_ui_port: int) -> tuple[str, str]:
    url = _companion_url(host, web_ui_port, "/settings/protocols")
    text = (
        "Open Companion protocol settings and enable Satellite → Button Subscriptions API. "
        "Warning: The Subscriptions API is required for full functionality from the Elgato plugin, "
        "but enabling it allows any satellite client to bypass the pincode/page system and interact "
        "with any button within Companion. After enabling it, submit this step again."
    )
    return url, text


def _subid_for_location(page: int, row: int, column: int) -> str:
    """Return a Companion-safe SUBID for an absolute location.

    The working standalone POC used slashes here: p<page>/r<row>/c<column>.
    Companion allows alphanumeric characters, '-' and '/', while '_' is not
    guaranteed to be accepted. Keep this exact shape for ADD-SUB and
    SUB-STATE mapping.
    """
    return f"p{page}/r{row}/c{column}"


async def async_read_live_states_for_locations(
    *,
    entry_id: str,
    host: str,
    satellite_port: int,
    backend: str,
    page_number: int,
    rows: int,
    columns: int,
    locations: list[tuple[int, int]],
    timeout: float = 8.0,
) -> dict[str, LiveButtonState]:
    """Read current live rendered state for selected page locations.

    This is a short-lived, read-only helper used by the import flow for hybrid
    switch mapping. It does not press buttons and does not start a persistent
    observer.
    """
    if backend == OBSERVER_BACKEND_SUBSCRIPTION:
        return await _read_subscription_states(
            host=host,
            satellite_port=satellite_port,
            page_number=page_number,
            locations=locations,
            timeout=timeout,
        )

    return await _read_surface_states(
        entry_id=entry_id,
        host=host,
        satellite_port=satellite_port,
        page_number=page_number,
        rows=rows,
        columns=columns,
        locations=locations,
        timeout=timeout,
    )


async def _read_subscription_states(
    *,
    host: str,
    satellite_port: int,
    page_number: int,
    locations: list[tuple[int, int]],
    timeout: float,
) -> dict[str, LiveButtonState]:
    """Read selected locations using temporary ADD-SUB/SUB-STATE."""
    result: dict[str, LiveButtonState] = {}
    if not locations:
        return result

    subid_to_location: dict[str, str] = {}
    try:
        async with TemporarySatelliteClient(host, satellite_port, timeout=timeout) as client:
            caps_deadline = asyncio.get_running_loop().time() + min(2.0, max(0.5, timeout / 3))
            subscriptions_supported: bool | None = None
            while asyncio.get_running_loop().time() < caps_deadline:
                remaining = max(0.2, caps_deadline - asyncio.get_running_loop().time())
                try:
                    command, args, extras, _line = await client.readline(timeout=remaining)
                except Exception:
                    # Some Companion versions/setups do not deliver CAPS in this
                    # short-lived read. Continue cautiously and let ADD-SUB/SUB-STATE
                    # prove whether subscriptions actually work.
                    break
                if command == "CAPS":
                    subscriptions_supported = args.get("SUBSCRIPTIONS") == "1"
                    break
                if await client.handle_common(command, extras):
                    continue
            if subscriptions_supported is False:
                return result

            for row, column in locations:
                location = companion_location(page_number, row, column)
                subid = _subid_for_location(page_number, row, column)
                subid_to_location[subid] = location
                await client.send(f"ADD-SUB SUBID={subid} LOCATION={location} BITMAP=0 COLORS=hex TEXT=true TEXT_STYLE=true")

            deadline = asyncio.get_running_loop().time() + timeout
            while len(result) < len(subid_to_location) and asyncio.get_running_loop().time() < deadline:
                remaining = max(0.2, deadline - asyncio.get_running_loop().time())
                command, args, extras, line = await client.readline(timeout=remaining)
                if await client.handle_common(command, extras):
                    continue
                if command != "SUB-STATE":
                    continue
                subid = args.get("SUBID")
                location = subid_to_location.get(str(subid))
                if not location:
                    continue
                result[location] = state_from_args("subscription", args, location_override=location, raw_command=line)

            for subid in subid_to_location:
                try:
                    await client.send(f"REMOVE-SUB SUBID={subid}")
                except Exception:
                    pass
    except Exception:
        return result
    return result


async def _read_surface_states(
    *,
    entry_id: str,
    host: str,
    satellite_port: int,
    page_number: int,
    rows: int,
    columns: int,
    locations: list[tuple[int, int]],
    timeout: float,
) -> dict[str, LiveButtonState]:
    """Read selected locations using the integration-owned observer surface."""
    result: dict[str, LiveButtonState] = {}
    if not locations:
        return result

    wanted = {companion_location(page_number, row, column) for row, column in locations}
    surface = observer_surface_metadata(entry_id, page_number)
    device_id = surface["device_id"]
    serial = surface["serial"]
    keys_total = max(1, int(rows) * int(columns))
    keys_per_row = max(1, int(columns))

    try:
        async with TemporarySatelliteClient(host, satellite_port, timeout=timeout) as client:
            await client.send(
                f"ADD-DEVICE DEVICEID={device_id} "
                f'PRODUCT_NAME="HA Observer - Page {page_number}" '
                f'SERIAL="{serial}" '
                f"SERIAL_IS_UNIQUE=true "
                f"KEYS_TOTAL={keys_total} "
                f"KEYS_PER_ROW={keys_per_row} "
                f"BITMAPS=0 COLORS=hex TEXT=true TEXT_STYLE=true"
            )

            deadline = asyncio.get_running_loop().time() + timeout
            while len(result) < len(wanted) and asyncio.get_running_loop().time() < deadline:
                remaining = max(0.2, deadline - asyncio.get_running_loop().time())
                command, args, extras, line = await client.readline(timeout=remaining)
                if await client.handle_common(command, extras):
                    continue
                if command != "KEY-STATE" or args.get("DEVICEID") != device_id:
                    continue
                state = state_from_args("surface", args, raw_command=line)
                if state.location in wanted and state.page == page_number:
                    result[str(state.location)] = state

            try:
                await client.send(f"REMOVE-DEVICE DEVICEID={device_id}")
            except Exception:
                pass
    except Exception:
        return result
    return result


async def async_verify_live_state_access(
    *,
    entry_id: str,
    host: str,
    satellite_port: int,
    web_ui_port: int,
    backend: str,
    page_number: int,
    rows: int,
    columns: int,
    sample_row: int,
    sample_column: int,
    timeout: float = 8.0,
) -> LiveStateVerificationResult:
    """Verify temporary live-state access for one imported page."""
    if backend == OBSERVER_BACKEND_SUBSCRIPTION:
        return await _verify_subscription(
            host=host,
            satellite_port=satellite_port,
            web_ui_port=web_ui_port,
            page_number=page_number,
            sample_row=sample_row,
            sample_column=sample_column,
            timeout=timeout,
        )

    return await _verify_surface(
        entry_id=entry_id,
        host=host,
        satellite_port=satellite_port,
        web_ui_port=web_ui_port,
        page_number=page_number,
        rows=rows,
        columns=columns,
        timeout=timeout,
    )


async def _verify_surface(
    *,
    entry_id: str,
    host: str,
    satellite_port: int,
    web_ui_port: int,
    page_number: int,
    rows: int,
    columns: int,
    timeout: float,
) -> LiveStateVerificationResult:
    surface = observer_surface_metadata(entry_id, page_number)
    device_id = surface["device_id"]
    serial = surface["serial"]
    url, instructions = _surface_instructions(host, web_ui_port, page_number)
    keys_total = max(1, int(rows) * int(columns))
    keys_per_row = max(1, int(columns))

    try:
        async with TemporarySatelliteClient(host, satellite_port, timeout=timeout) as client:
            await client.send(
                f"ADD-DEVICE DEVICEID={device_id} "
                f'PRODUCT_NAME="HA Observer - Page {page_number}" '
                f'SERIAL="{serial}" '
                f"SERIAL_IS_UNIQUE=true "
                f"KEYS_TOTAL={keys_total} "
                f"KEYS_PER_ROW={keys_per_row} "
                f"BITMAPS=0 COLORS=hex TEXT=true TEXT_STYLE=true"
            )

            deadline = asyncio.get_running_loop().time() + timeout
            first_state_without_location: LiveButtonState | None = None
            while asyncio.get_running_loop().time() < deadline:
                remaining = max(0.2, deadline - asyncio.get_running_loop().time())
                command, args, extras, line = await client.readline(timeout=remaining)
                if await client.handle_common(command, extras):
                    continue
                if command == "ADD-DEVICE" and "ERROR" in args:
                    return LiveStateVerificationResult(
                        success=False,
                        backend=OBSERVER_BACKEND_SURFACE,
                        expected_page=page_number,
                        observed_page=None,
                        method="surface_add_device",
                        reason="add_device_error",
                        companion_url=url,
                        instructions=instructions,
                        observer_surface=surface,
                    )
                if command != "KEY-STATE":
                    continue
                if args.get("DEVICEID") != device_id:
                    continue
                state = state_from_args("surface", args, raw_command=line)
                if state.page is None:
                    first_state_without_location = first_state_without_location or state
                    continue
                if state.page == page_number:
                    await client.send(f"REMOVE-DEVICE DEVICEID={device_id}")
                    return LiveStateVerificationResult(
                        success=True,
                        backend=OBSERVER_BACKEND_SURFACE,
                        expected_page=page_number,
                        observed_page=state.page,
                        method="key_state_location_page_match",
                        reason="ok",
                        companion_url=url,
                        instructions="The observer surface is reading the expected page.",
                        observer_surface=surface,
                        sample_location=state.location,
                        sample_state=state,
                    )
                await client.send(f"REMOVE-DEVICE DEVICEID={device_id}")
                return LiveStateVerificationResult(
                    success=False,
                    backend=OBSERVER_BACKEND_SURFACE,
                    expected_page=page_number,
                    observed_page=state.page,
                    method="key_state_location_page_match",
                    reason="wrong_page",
                    companion_url=url,
                    instructions=instructions,
                    observer_surface=surface,
                    sample_location=state.location,
                    sample_state=state,
                )

            if first_state_without_location is not None:
                return LiveStateVerificationResult(
                    success=False,
                    backend=OBSERVER_BACKEND_SURFACE,
                    expected_page=page_number,
                    observed_page=None,
                    method="key_state_location_page_match",
                    reason="key_state_without_location",
                    companion_url=url,
                    instructions=(
                        "The observer surface returned KEY-STATE, but no LOCATION was included, so the page could not be verified. "
                        "Consider using Subscription mode or check Companion's Satellite API settings."
                    ),
                    observer_surface=surface,
                    sample_state=first_state_without_location,
                )
    except Exception:
        return LiveStateVerificationResult(
            success=False,
            backend=OBSERVER_BACKEND_SURFACE,
            expected_page=page_number,
            observed_page=None,
            method="key_state_location_page_match",
            reason="connection_failed_or_timeout",
            companion_url=url,
            instructions=instructions,
            observer_surface=surface,
        )

    return LiveStateVerificationResult(
        success=False,
        backend=OBSERVER_BACKEND_SURFACE,
        expected_page=page_number,
        observed_page=None,
        method="key_state_location_page_match",
        reason="no_key_state_received",
        companion_url=url,
        instructions=instructions,
        observer_surface=surface,
    )


async def _verify_subscription(
    *,
    host: str,
    satellite_port: int,
    web_ui_port: int,
    page_number: int,
    sample_row: int,
    sample_column: int,
    timeout: float,
) -> LiveStateVerificationResult:
    url, instructions = _subscription_instructions(host, web_ui_port)
    location = companion_location(page_number, sample_row, sample_column)
    subid = _subid_for_location(page_number, sample_row, sample_column)
    subscriptions_supported: bool | None = None

    try:
        async with TemporarySatelliteClient(host, satellite_port, timeout=timeout) as client:
            caps_deadline = asyncio.get_running_loop().time() + min(2.0, max(0.5, timeout / 3))

            # Wait briefly for CAPS. Companion usually sends BEGIN/CAPS after connect,
            # but older/dev builds can be inconsistent on short-lived connections.
            # If CAPS explicitly says SUBSCRIPTIONS=0, fail. If CAPS is not seen,
            # continue cautiously and let ADD-SUB/SUB-STATE prove support.
            while asyncio.get_running_loop().time() < caps_deadline:
                remaining = max(0.2, caps_deadline - asyncio.get_running_loop().time())
                try:
                    command, args, extras, _line = await client.readline(timeout=remaining)
                except Exception:
                    break
                if command == "CAPS":
                    subscriptions_supported = args.get("SUBSCRIPTIONS") == "1"
                    break
                if await client.handle_common(command, extras):
                    continue

            if subscriptions_supported is False:
                return LiveStateVerificationResult(
                    success=False,
                    backend=OBSERVER_BACKEND_SUBSCRIPTION,
                    expected_page=page_number,
                    method="caps_subscriptions_and_sub_state",
                    reason="subscriptions_disabled_or_caps_missing",
                    companion_url=url,
                    instructions=instructions,
                    subscriptions_supported=subscriptions_supported,
                    sample_location=location,
                )

            await client.send(f"ADD-SUB SUBID={subid} LOCATION={location} BITMAP=0 COLORS=hex TEXT=true TEXT_STYLE=true")
            deadline = asyncio.get_running_loop().time() + timeout
            while asyncio.get_running_loop().time() < deadline:
                remaining = max(0.2, deadline - asyncio.get_running_loop().time())
                command, args, extras, line = await client.readline(timeout=remaining)
                if await client.handle_common(command, extras):
                    continue
                if command == "ADD-SUB" and "ERROR" in args:
                    return LiveStateVerificationResult(
                        success=False,
                        backend=OBSERVER_BACKEND_SUBSCRIPTION,
                        expected_page=page_number,
                        method="caps_subscriptions_and_sub_state",
                        reason="add_sub_error",
                        companion_url=url,
                        instructions=instructions,
                        subscriptions_supported=True,
                        sample_location=location,
                    )
                if command == "SUB-STATE" and args.get("SUBID") == subid:
                    state = state_from_args("subscription", args, location_override=location, raw_command=line)
                    await client.send(f"REMOVE-SUB SUBID={subid}")
                    return LiveStateVerificationResult(
                        success=True,
                        backend=OBSERVER_BACKEND_SUBSCRIPTION,
                        expected_page=page_number,
                        observed_page=page_number,
                        method="caps_subscriptions_and_sub_state",
                        reason="ok",
                        companion_url=url,
                        instructions="The Button Subscriptions API returned live state for the requested absolute location.",
                        subscriptions_supported=True,
                        sample_location=location,
                        sample_state=state,
                    )
    except Exception:
        return LiveStateVerificationResult(
            success=False,
            backend=OBSERVER_BACKEND_SUBSCRIPTION,
            expected_page=page_number,
            method="caps_subscriptions_and_sub_state",
            reason="connection_failed_or_timeout",
            companion_url=url,
            instructions=instructions,
            subscriptions_supported=subscriptions_supported,
            sample_location=location,
        )

    return LiveStateVerificationResult(
        success=False,
        backend=OBSERVER_BACKEND_SUBSCRIPTION,
        expected_page=page_number,
        method="caps_subscriptions_and_sub_state",
        reason="no_sub_state_received",
        companion_url=url,
        instructions=instructions,
        subscriptions_supported=True,
        sample_location=location,
    )
