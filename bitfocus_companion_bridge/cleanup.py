"""Cleanup helpers for Bitfocus Companion Bridge page/device deletion."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import logging
import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, SUBENTRY_TYPE_PAGE
from .entity_model import page_unique_id
from .subentries import iter_config_subentries

_LOGGER = logging.getLogger(__name__)

_MANAGED_DOMAINS = {"sensor", "button", "switch"}
_UNIQUE_ID_PAGE_RE = re.compile(rf"^{re.escape(DOMAIN)}_(?P<entry_id>.+)_p(?P<page>\d+)r\d+c\d+_(?P<domain>sensor|button|switch)$")


def page_number_from_device_entry(entry: ConfigEntry, device_entry: dr.DeviceEntry) -> int | None:
    """Return the Companion page number represented by a HA device entry."""
    prefix = f"{entry.entry_id}_p"
    for identifier in getattr(device_entry, "identifiers", set()) or set():
        try:
            domain, value = identifier
        except Exception:
            continue
        if domain != DOMAIN:
            continue
        text = str(value)
        if not text.startswith(prefix):
            continue
        suffix = text[len(prefix) :]
        if suffix.isdigit():
            return int(suffix)
    return None


def imported_page_numbers(entry: ConfigEntry) -> set[int]:
    """Return page numbers that still exist as active page subentries."""
    pages: set[int] = set()
    for subentry in iter_config_subentries(entry):
        if getattr(subentry, "subentry_type", None) != SUBENTRY_TYPE_PAGE:
            continue
        data = getattr(subentry, "data", {}) or {}
        if data.get("deleted") and not data.get("keep_entities_on_delete"):
            continue
        try:
            pages.add(int(data.get("page_number")))
        except Exception:
            continue
    return pages


def planned_entities_for_page(entry: ConfigEntry, page_number: int) -> list[dict[str, Any]]:
    """Return stored planned entities for one imported page."""
    out: list[dict[str, Any]] = []
    for subentry in iter_config_subentries(entry):
        if getattr(subentry, "subentry_type", None) != SUBENTRY_TYPE_PAGE:
            continue
        data = getattr(subentry, "data", {}) or {}
        try:
            if int(data.get("page_number")) != int(page_number):
                continue
        except Exception:
            continue
        for planned in data.get("planned_entities") or []:
            if isinstance(planned, Mapping):
                out.append(dict(planned))
    return out


def _entity_entries_for_config_entry(entity_reg: er.EntityRegistry, entry_id: str) -> Iterable[Any]:
    """Return entity registry entries for a config entry across HA versions."""
    helper = getattr(er, "async_entries_for_config_entry", None)
    if helper is not None:
        try:
            return list(helper(entity_reg, entry_id))
        except Exception:
            _LOGGER.debug("Could not use er.async_entries_for_config_entry", exc_info=True)

    entities = getattr(entity_reg, "entities", {})
    try:
        values = entities.values()
    except Exception:
        values = []
    return [item for item in values if getattr(item, "config_entry_id", None) == entry_id]


def _entry_unique_id(entry: Any) -> str:
    return str(getattr(entry, "unique_id", "") or "")


def _entry_domain(entry: Any) -> str:
    platform = getattr(entry, "platform", None)
    if platform:
        return str(platform)
    entity_id = str(getattr(entry, "entity_id", "") or "")
    if "." in entity_id:
        return entity_id.split(".", 1)[0]
    return ""


def _unique_id_page(entry_id: str, unique_id: str) -> int | None:
    match = _UNIQUE_ID_PAGE_RE.match(unique_id)
    if not match or match.group("entry_id") != entry_id:
        return None
    try:
        return int(match.group("page"))
    except Exception:
        return None


def _remove_entity_id(entity_reg: er.EntityRegistry, entity_id: str) -> bool:
    """Remove one entity registry entry."""
    try:
        entity_reg.async_remove(entity_id)
        return True
    except KeyError:
        return False
    except Exception:
        _LOGGER.debug("Could not remove entity registry entry %s", entity_id, exc_info=True)
        return False


def remove_registry_entries_for_page(hass: HomeAssistant, entry: ConfigEntry, page_number: int) -> int:
    """Remove all managed entity registry entries for a Companion page."""
    entity_reg = er.async_get(hass)
    removed = 0

    # First use stored planned entity unique IDs. This catches older or renamed
    # entity_ids because unique_id is stable and location-based.
    for planned in planned_entities_for_page(entry, page_number):
        domain = str(planned.get("domain") or "")
        unique_id = str(planned.get("unique_id") or "")
        if domain not in _MANAGED_DOMAINS or not unique_id:
            continue
        entity_id = entity_reg.async_get_entity_id(domain, DOMAIN, unique_id)
        if entity_id and _remove_entity_id(entity_reg, entity_id):
            removed += 1

    # Then sweep by unique_id pattern, so native page/subentry delete also cleans
    # entries after the page subentry has already disappeared from storage.
    for registry_entry in list(_entity_entries_for_config_entry(entity_reg, entry.entry_id)):
        unique_id = _entry_unique_id(registry_entry)
        if _unique_id_page(entry.entry_id, unique_id) != int(page_number):
            continue
        domain = _entry_domain(registry_entry)
        if domain and domain not in _MANAGED_DOMAINS:
            continue
        entity_id = str(getattr(registry_entry, "entity_id", "") or "")
        if entity_id and _remove_entity_id(entity_reg, entity_id):
            removed += 1

    return removed


def remove_registry_entries_for_removed_pages(hass: HomeAssistant, entry: ConfigEntry) -> int:
    """Remove stale registry entries whose page subentry no longer exists."""
    active_pages = imported_page_numbers(entry)
    entity_reg = er.async_get(hass)
    stale_pages: set[int] = set()
    removed = 0

    for registry_entry in list(_entity_entries_for_config_entry(entity_reg, entry.entry_id)):
        unique_id = _entry_unique_id(registry_entry)
        page_number = _unique_id_page(entry.entry_id, unique_id)
        if page_number is None or page_number in active_pages:
            continue
        entity_id = str(getattr(registry_entry, "entity_id", "") or "")
        if entity_id and _remove_entity_id(entity_reg, entity_id):
            removed += 1
            stale_pages.add(page_number)

    for page_number in stale_pages:
        remove_page_device(hass, entry, page_number)

    return removed


def remove_all_managed_registry_entries(hass: HomeAssistant, entry: ConfigEntry) -> int:
    """Remove all managed entity registry entries for this integration entry."""
    entity_reg = er.async_get(hass)
    removed = 0
    for registry_entry in list(_entity_entries_for_config_entry(entity_reg, entry.entry_id)):
        unique_id = _entry_unique_id(registry_entry)
        if _unique_id_page(entry.entry_id, unique_id) is None:
            continue
        domain = _entry_domain(registry_entry)
        if domain and domain not in _MANAGED_DOMAINS:
            continue
        entity_id = str(getattr(registry_entry, "entity_id", "") or "")
        if entity_id and _remove_entity_id(entity_reg, entity_id):
            removed += 1
    return removed


def remove_page_device(hass: HomeAssistant, entry: ConfigEntry, page_number: int) -> bool:
    """Remove the Companion Page N device registry entry if present."""
    device_reg = dr.async_get(hass)
    identifier = (DOMAIN, f"{entry.entry_id}_p{page_number}")
    try:
        device = device_reg.async_get_device(identifiers={identifier})
    except Exception:
        device = None
    if device is None:
        return False

    try:
        remove_device = getattr(device_reg, "async_remove_device", None)
        if remove_device is not None:
            remove_device(device.id)
            return True
        update_device = getattr(device_reg, "async_update_device", None)
        if update_device is not None:
            update_device(device_id=device.id, remove_config_entry_id=entry.entry_id)
            return True
    except Exception:
        _LOGGER.debug("Could not remove Companion page device %s", identifier, exc_info=True)
    return False


def remove_all_page_devices(hass: HomeAssistant, entry: ConfigEntry) -> int:
    """Remove all Companion page devices for this entry."""
    device_reg = dr.async_get(hass)
    removed = 0
    try:
        devices = list(device_reg.devices.values())
    except Exception:
        devices = []
    for device in devices:
        page_number = page_number_from_device_entry(entry, device)
        if page_number is None:
            continue
        if remove_page_device(hass, entry, page_number):
            removed += 1
    return removed


async def async_best_effort_remove_page_subentry(hass: HomeAssistant, entry: ConfigEntry, page_subentry: Any) -> bool:
    """Best-effort remove of a config subentry from a subentry flow.

    Home Assistant owns the native subentry delete UI. This helper tries public
    and known manager shapes when available; if none exist, callers can still
    remove entities and mark the page import metadata as deleted.
    """
    manager = getattr(hass.config_entries, "subentries", None)
    if manager is None:
        return False

    subentry_id = getattr(page_subentry, "subentry_id", None)
    subentry_type = getattr(page_subentry, "subentry_type", SUBENTRY_TYPE_PAGE)
    if not subentry_id:
        return False

    call_shapes = [
        ((entry.entry_id, subentry_type), subentry_id),
        (entry.entry_id, subentry_id),
        (entry, page_subentry),
        ((entry.entry_id, subentry_type, subentry_id),),
    ]
    for method_name in ("async_remove", "async_delete", "async_remove_subentry", "async_delete_subentry"):
        method = getattr(manager, method_name, None)
        if method is None:
            continue
        for args in call_shapes:
            try:
                result = method(*args)
                if hasattr(result, "__await__"):
                    await result
                return True
            except TypeError:
                continue
            except Exception:
                _LOGGER.debug("Could not remove config subentry via %s%r", method_name, args, exc_info=True)
                continue
    return False
