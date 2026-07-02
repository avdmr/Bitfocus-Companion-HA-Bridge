"""Read-only live-state runtime for Bitfocus Companion Bridge POC v2.

The runtime keeps the selected live-state backend connected and publishes live
rendered button state to variable-text sensors. It never sends KEY-PRESS,
KEY-ROTATE, SUB-PRESS or any other control command.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_IMPORT_DECISIONS,
    CONF_PAGE_OBSERVER_BACKEND,
    DEFAULT_OBSERVER_BACKEND,
    DOMAIN,
    OBSERVER_BACKEND_SUBSCRIPTION,
    OBSERVER_BACKEND_SURFACE,
    SIGNAL_LIVE_STATE_UPDATE,
)
from .entity_model import observer_surface_metadata
from .live_state import (
    LiveButtonState,
    parse_protocol_line,
    state_from_args,
)
from .subentries import iter_config_subentries

_LOGGER = logging.getLogger(__name__)


@dataclass
class RuntimeLocation:
    """One location that should be monitored for live rendered state."""

    page: int
    row: int
    column: int
    location: str
    domains: set[str] = field(default_factory=set)


@dataclass
class RuntimePage:
    """One imported page to monitor with the selected backend."""

    page_number: int
    backend: str
    rows: int
    columns: int
    locations: dict[str, RuntimeLocation] = field(default_factory=dict)

    @property
    def has_locations(self) -> bool:
        return bool(self.locations)


class CompanionLiveStateRuntime:
    """Persistent read-only live-state runtime for imported pages."""

    def __init__(
        self,
        *,
        hass: HomeAssistant,
        entry_id: str,
        host: str,
        satellite_port: int,
        pages: list[RuntimePage],
        live_states: dict[str, dict[str, Any]],
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.host = host
        self.satellite_port = satellite_port
        self.pages = [page for page in pages if page.has_locations]
        self.live_states = live_states
        self._tasks: list[asyncio.Task[None]] = []
        self._stop_event = asyncio.Event()
        self._tracked_locations = {
            location
            for page in self.pages
            for location in page.locations
        }

    async def start(self) -> None:
        """Start runtime tasks for all selected backend groups."""
        if self._tasks or not self.pages:
            return
        self._stop_event.clear()
        surface_pages = [page for page in self.pages if page.backend == OBSERVER_BACKEND_SURFACE]
        subscription_pages = [page for page in self.pages if page.backend == OBSERVER_BACKEND_SUBSCRIPTION]
        if surface_pages:
            self._tasks.append(asyncio.create_task(self._surface_loop(surface_pages), name=f"bcb-surface-{self.entry_id[:8]}"))
        if subscription_pages:
            self._tasks.append(asyncio.create_task(self._subscription_loop(subscription_pages), name=f"bcb-subscription-{self.entry_id[:8]}"))
        _LOGGER.info(
            "Started Bitfocus Companion Bridge live-state runtime: %s surface page(s), %s subscription page(s), %s tracked location(s)",
            len(surface_pages),
            len(subscription_pages),
            len(self._tracked_locations),
        )

    async def stop(self) -> None:
        """Stop runtime tasks."""
        self._stop_event.set()
        tasks = list(self._tasks)
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                _LOGGER.debug("Error while stopping Bitfocus Companion Bridge live-state task", exc_info=True)

    @callback
    def _publish_state(self, state: LiveButtonState) -> None:
        """Publish a live button state to HA sensors."""
        if not state.location or state.location not in self._tracked_locations:
            return
        data = state.as_storage_data()
        self.live_states[str(state.location)] = data
        async_dispatcher_send(
            self.hass,
            f"{SIGNAL_LIVE_STATE_UPDATE}_{self.entry_id}_{state.location}",
            data,
        )

    async def _send(self, writer: asyncio.StreamWriter, line: str) -> None:
        writer.write((line + "\n").encode("utf-8"))
        await writer.drain()

    async def _readline(self, reader: asyncio.StreamReader, timeout: float = 2.0) -> tuple[str, dict[str, str], list[str], str] | None:
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
        except TimeoutError:
            return None
        if not raw:
            raise ConnectionError("Companion closed Satellite connection")
        line = raw.decode("utf-8", errors="replace").strip()
        command, args, extras = parse_protocol_line(line)
        return command, args, extras, line

    async def _handle_common(self, writer: asyncio.StreamWriter, command: str, extras: list[str]) -> bool:
        if command == "PING":
            await self._send(writer, "PONG " + " ".join(extras))
            return True
        return command in {"BEGIN", "CAPS", "PONG"}

    async def _surface_loop(self, pages: list[RuntimePage]) -> None:
        """Reconnect loop for Surface mode pages."""
        while not self._stop_event.is_set():
            writer: asyncio.StreamWriter | None = None
            try:
                reader, writer = await asyncio.open_connection(self.host, self.satellite_port)
                device_to_page = await self._register_surface_pages(writer, pages)
                while not self._stop_event.is_set():
                    parsed = await self._readline(reader)
                    if parsed is None:
                        continue
                    command, args, extras, line = parsed
                    if await self._handle_common(writer, command, extras):
                        continue
                    if command != "KEY-STATE":
                        continue
                    device_id = args.get("DEVICEID")
                    expected_page = device_to_page.get(str(device_id))
                    if expected_page is None:
                        continue
                    state = state_from_args("surface", args, raw_command=line)
                    if state.page is not None and state.page != expected_page:
                        _LOGGER.warning(
                            "Bitfocus Companion observer surface is on the wrong page: device=%s expected=%s observed=%s location=%s",
                            device_id,
                            expected_page,
                            state.page,
                            state.location,
                        )
                        continue
                    self._publish_state(state)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - network dependent
                _LOGGER.debug("Surface live-state runtime disconnected: %s", exc)
            finally:
                if writer is not None:
                    try:
                        for page in pages:
                            surface = observer_surface_metadata(self.entry_id, page.page_number)
                            await self._send(writer, f"REMOVE-DEVICE DEVICEID={surface['device_id']}")
                        await self._send(writer, "QUIT")
                    except Exception:
                        pass
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass
            if not self._stop_event.is_set():
                await asyncio.sleep(5)

    async def _register_surface_pages(self, writer: asyncio.StreamWriter, pages: list[RuntimePage]) -> dict[str, int]:
        """Register integration-owned observer surfaces."""
        device_to_page: dict[str, int] = {}
        for page in pages:
            surface = observer_surface_metadata(self.entry_id, page.page_number)
            device_id = surface["device_id"]
            device_to_page[device_id] = page.page_number
            keys_total = max(1, int(page.rows) * int(page.columns))
            keys_per_row = max(1, int(page.columns))
            await self._send(
                writer,
                f"ADD-DEVICE DEVICEID={device_id} "
                f'PRODUCT_NAME="HA Observer - Page {page.page_number}" '
                f'SERIAL="{surface["serial"]}" '
                f"SERIAL_IS_UNIQUE=true "
                f"KEYS_TOTAL={keys_total} "
                f"KEYS_PER_ROW={keys_per_row} "
                f"BITMAPS=0 COLORS=hex TEXT=true TEXT_STYLE=true",
            )
        return device_to_page

    async def _subscription_loop(self, pages: list[RuntimePage]) -> None:
        """Reconnect loop for Subscription API mode pages."""
        while not self._stop_event.is_set():
            writer: asyncio.StreamWriter | None = None
            subid_to_location: dict[str, str] = {}
            try:
                reader, writer = await asyncio.open_connection(self.host, self.satellite_port)
                subid_to_location = await self._register_subscription_locations(writer, pages)
                while not self._stop_event.is_set():
                    parsed = await self._readline(reader)
                    if parsed is None:
                        continue
                    command, args, extras, line = parsed
                    if await self._handle_common(writer, command, extras):
                        continue
                    if command != "SUB-STATE":
                        continue
                    subid = args.get("SUBID")
                    location = subid_to_location.get(str(subid))
                    if not location:
                        continue
                    state = state_from_args("subscription", args, location_override=location, raw_command=line)
                    self._publish_state(state)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - network dependent
                _LOGGER.debug("Subscription live-state runtime disconnected: %s", exc)
            finally:
                if writer is not None:
                    try:
                        for subid in subid_to_location:
                            await self._send(writer, f"REMOVE-SUB SUBID={subid}")
                        await self._send(writer, "QUIT")
                    except Exception:
                        pass
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass
            if not self._stop_event.is_set():
                await asyncio.sleep(5)

    async def _register_subscription_locations(self, writer: asyncio.StreamWriter, pages: list[RuntimePage]) -> dict[str, str]:
        """Register ADD-SUB for every tracked location."""
        subid_to_location: dict[str, str] = {}
        for page in pages:
            for location in sorted(page.locations.values(), key=lambda loc: (loc.page, loc.row, loc.column)):
                subid = _subid_for_location(location.page, location.row, location.column)
                subid_to_location[subid] = location.location
                await self._send(
                    writer,
                    f"ADD-SUB SUBID={subid} LOCATION={location.location} BITMAP=0 COLORS=hex TEXT=true TEXT_STYLE=true",
                )
        return subid_to_location


def _subid_for_location(page: int, row: int, column: int) -> str:
    """Return the same slash-style SUBID that worked in the standalone POC."""
    return f"p{page}/r{row}/c{column}"


def _parse_location(location: str) -> tuple[int, int, int] | None:
    try:
        page_s, row_s, column_s = location.split("/")
        return int(page_s), int(row_s), int(column_s)
    except Exception:
        return None


def live_runtime_pages_from_subentries(entry: Any) -> list[RuntimePage]:
    """Build runtime page definitions from imported page subentries."""
    pages_by_key: dict[tuple[int, str], RuntimePage] = {}
    for subentry in iter_config_subentries(entry):
        if getattr(subentry, "subentry_type", None) != "page":
            continue
        data = dict(subentry.data or {})
        if data.get("deleted"):
            continue
        page_number = data.get("page_number")
        if page_number is None:
            continue
        page_number = int(page_number)
        decisions = dict(data.get(CONF_IMPORT_DECISIONS) or {})
        backend = decisions.get(CONF_PAGE_OBSERVER_BACKEND) or entry.options.get("observer_backend") or DEFAULT_OBSERVER_BACKEND
        grid = ((data.get("import_preview") or {}).get("grid") or {})
        rows = int(grid.get("rows") or 4)
        columns = int(grid.get("columns") or 8)
        key = (page_number, backend)
        page = pages_by_key.setdefault(
            key,
            RuntimePage(
                page_number=page_number,
                backend=backend,
                rows=rows,
                columns=columns,
            ),
        )
        for entity in data.get("planned_entities") or []:
            if entity.get("domain") not in {"sensor", "switch"}:
                continue
            if entity.get("status", "active") != "active":
                continue
            location = str(entity.get("location") or "")
            parsed = _parse_location(location)
            if parsed is None:
                continue
            loc_page, row, column = parsed
            runtime_location = page.locations.setdefault(
                location,
                RuntimeLocation(page=loc_page, row=row, column=column, location=location),
            )
            runtime_location.domains.add(str(entity.get("domain")))
    return list(pages_by_key.values())
