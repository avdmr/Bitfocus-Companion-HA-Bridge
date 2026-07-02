"""Bitfocus Companion Bridge integration.

POC v2 status:
- Creates a main config entry for a Companion instance.
- Allows page imports as config subentries.
- Re-imports update the existing page subentry keyed by Companion page number.
- Creates sensor, button and switch entities from imported page decisions.
- Publishes live rendered button state from the selected read-only live-state backend.
- Switch states are derived from confirmed render signatures.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS, SUBENTRY_TYPE_PAGE
from .cleanup import (
    async_best_effort_remove_page_subentry,
    page_number_from_device_entry,
    remove_all_managed_registry_entries,
    remove_all_page_devices,
    remove_page_device,
    remove_registry_entries_for_page,
    remove_registry_entries_for_removed_pages,
)
from .entity_model import find_page_subentry

_LOGGER = logging.getLogger(__name__)


def setup(hass: Any, config: dict[str, Any]) -> bool:
    """Synchronous package setup fallback for Home Assistant."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the integration package before config entries are loaded."""
    hass.data.setdefault(DOMAIN, {})
    return True


def _iter_config_subentries(entry: Any) -> list[Any]:
    """Return config subentries as a list across Home Assistant versions."""
    subentries = getattr(entry, "subentries", None)
    if subentries is None:
        return []
    if hasattr(subentries, "values"):
        return list(subentries.values())
    try:
        return list(subentries)
    except TypeError:
        return []


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Bitfocus Companion Bridge from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    page_subentries = []
    for subentry in _iter_config_subentries(entry):
        try:
            if getattr(subentry, "subentry_type", None) == SUBENTRY_TYPE_PAGE:
                page_subentries.append(subentry)
        except Exception:
            continue

    pages_by_number: dict[int, dict[str, Any]] = {}
    for subentry in page_subentries:
        try:
            if subentry.data.get("deleted"):
                continue
            page_number = subentry.data.get("page_number")
            if page_number is not None:
                pages_by_number[int(page_number)] = dict(subentry.data)
        except Exception:
            _LOGGER.debug("Skipping malformed Bitfocus Companion Bridge page subentry", exc_info=True)

    try:
        stale_removed = remove_registry_entries_for_removed_pages(hass, entry)
        if stale_removed:
            _LOGGER.info("Cleaned up %s stale Bitfocus Companion Bridge entit(y/ies) for removed page subentries", stale_removed)
    except Exception:
        _LOGGER.debug("Could not clean stale Bitfocus Companion Bridge entities", exc_info=True)

    runtime = None
    live_states: dict[str, dict[str, Any]] = {}
    try:
        from .live_observer import CompanionLiveStateRuntime, live_runtime_pages_from_subentries

        runtime_pages = live_runtime_pages_from_subentries(entry)
        if runtime_pages:
            runtime = CompanionLiveStateRuntime(
                hass=hass,
                entry_id=entry.entry_id,
                host=str(entry.data.get("host")),
                satellite_port=int(entry.data.get("satellite_port", 16622)),
                pages=runtime_pages,
                live_states=live_states,
            )
            await runtime.start()
    except Exception:  # pragma: no cover - defensive guard for POC runtime
        # A live observer failure must not keep the config entry from loading.
        _LOGGER.exception("Could not start Bitfocus Companion Bridge live-state runtime")
        runtime = None

    hass.data[DOMAIN][entry.entry_id] = {
        "config": dict(entry.data),
        "options": dict(entry.options),
        "pages": pages_by_number,
        "live_states": live_states,
        "live_state_runtime": runtime,
        "poc_note": "POC v3 creates location-based sensor, button and switch entities. Switch states are derived from live render signatures.",
    }

    # Forward sensor platform after hass.data is initialized so entities can read
    # planned_entities and subscribe to runtime updates.
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        _LOGGER.exception("Could not set up Bitfocus Companion Bridge platforms")
        # Keep the entry loaded for diagnostics/re-import flows.

    planned_entity_count = 0
    planned_sensor_count = 0
    for subentry in page_subentries:
        try:
            entities = subentry.data.get("planned_entities") or []
            planned_entity_count += len(entities)
            planned_sensor_count += len([e for e in entities if e.get("domain") == "sensor"])
        except Exception:
            pass

    _LOGGER.info(
        "Bitfocus Companion Bridge POC v2 loaded for %s with %s imported page(s), %s planned entities and %s live sensor(s)",
        getattr(entry, "title", "Companion"),
        len(page_subentries),
        planned_entity_count,
        planned_sensor_count,
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    unload_ok = True
    try:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    except Exception:
        _LOGGER.debug("Could not unload Bitfocus Companion Bridge platforms", exc_info=True)
        unload_ok = False

    data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, data)
    if data and (runtime := data.get("live_state_runtime")):
        try:
            await runtime.stop()
        except Exception:
            _LOGGER.debug("Error while stopping Bitfocus Companion Bridge live-state runtime", exc_info=True)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle removal of a config entry."""
    data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if data and (runtime := data.get("live_state_runtime")):
        try:
            await runtime.stop()
        except Exception:
            _LOGGER.debug("Error while removing Bitfocus Companion Bridge live-state runtime", exc_info=True)

    try:
        removed_entities = remove_all_managed_registry_entries(hass, entry)
        removed_devices = remove_all_page_devices(hass, entry)
        if removed_entities or removed_devices:
            _LOGGER.info(
                "Removed %s Bitfocus Companion Bridge entit(y/ies) and %s page device(s) while removing config entry",
                removed_entities,
                removed_devices,
            )
    except Exception:
        _LOGGER.debug("Could not clean all Bitfocus Companion Bridge registry entries", exc_info=True)


async def async_remove_config_entry_device(hass: HomeAssistant, entry: ConfigEntry, device_entry: Any) -> bool:
    """Allow the user to remove a Companion page device from the device page.

    Deleting a page device cascades to the location-based entities for that page.
    The native HA device dialog cannot be extended by the integration, so this
    function performs the cleanup after the user confirms Home Assistant's own
    delete prompt.
    """
    page_number = page_number_from_device_entry(entry, device_entry)
    if page_number is None:
        return False

    try:
        removed = remove_registry_entries_for_page(hass, entry, page_number)
        page_subentry = find_page_subentry(entry, page_number)
        removed_subentry = False
        if page_subentry is not None:
            removed_subentry = await async_best_effort_remove_page_subentry(hass, entry, page_subentry)
        remove_page_device(hass, entry, page_number)
        _LOGGER.info(
            "Removed Companion Page %s device with %s managed entit(y/ies); subentry_removed=%s",
            page_number,
            removed,
            removed_subentry,
        )
    except Exception:
        _LOGGER.debug("Could not remove Companion page device %s", page_number, exc_info=True)
        return False
    return True
