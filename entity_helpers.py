"""Shared helpers for Bitfocus Companion Bridge entities."""

from __future__ import annotations

from typing import Any, Mapping

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_HTTP_PORT, DEFAULT_HTTP_PORT, DOMAIN, MANUFACTURER


def page_device_info(entry: Any, page_number: int, page_name: str = "") -> dict[str, Any]:
    """Return device_info that groups entities under the imported Companion page."""
    page_label = f"Companion Page {page_number}"
    if page_name:
        page_label = f"{page_label} - {page_name}"
    host = entry.data.get(CONF_HOST) or entry.data.get("host")
    port = int(entry.data.get(CONF_HTTP_PORT, DEFAULT_HTTP_PORT))
    return {
        "identifiers": {(DOMAIN, f"{entry.entry_id}_p{page_number}")},
        "name": page_label,
        "manufacturer": MANUFACTURER,
        "model": "Companion page export",
        "configuration_url": f"http://{host}:{port}/surfaces/configured" if host else None,
    }


async def async_press_companion_location(hass: HomeAssistant, entry: Any, location: str) -> None:
    """Trigger a Companion button via HTTP Remote Control location press."""
    host = entry.data.get(CONF_HOST) or entry.data.get("host")
    port = int(entry.data.get(CONF_HTTP_PORT, DEFAULT_HTTP_PORT))
    if not host:
        raise RuntimeError("Companion host is not configured")
    # Companion HTTP Remote Control uses the same IP/port as the web UI.
    url = f"http://{host}:{port}/api/location/{location}/press"
    session = async_get_clientsession(hass)
    async with session.post(url, timeout=10) as response:
        if response.status < 200 or response.status >= 300:
            text = await response.text()
            raise RuntimeError(f"Companion location press failed with HTTP {response.status}: {text[:200]}")


def signature_value(signature: Mapping[str, Any], field: str) -> Any:
    """Return a normalized field value from a stored render signature."""
    if field == "background":
        return signature.get("background") or signature.get("color")
    return signature.get(field)


def live_value(live_state: Mapping[str, Any], field: str) -> Any:
    """Return a normalized field value from live state data."""
    if field == "background":
        return live_state.get("background") or live_state.get("color")
    return live_state.get(field)


def signature_matches(live_state: Mapping[str, Any] | None, signature: Mapping[str, Any] | None, fields: list[str] | None = None) -> bool:
    """Return whether live state matches a stored render signature.

    Empty signature fields are ignored. At least one non-empty field must match.
    """
    if not live_state or not signature:
        return False
    fields = fields or ["text", "background", "text_color"]
    compared = 0
    for field in fields:
        expected = signature_value(signature, field)
        if expected in (None, ""):
            continue
        actual = live_value(live_state, field)
        if actual in (None, ""):
            return False
        # Companion renders variable text templates like $(internal:time_hms) to
        # live values. Treat configured variable tokens as a text wildcard so a
        # button can still be matched by its stable colors/text style.
        if field == "text" and "$" in str(expected):
            compared += 1
            continue
        if str(actual).strip().lower() != str(expected).strip().lower():
            return False
        compared += 1
    return compared > 0
