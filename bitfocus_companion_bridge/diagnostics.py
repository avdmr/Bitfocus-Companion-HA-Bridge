"""Diagnostics support for Bitfocus Companion Bridge POC."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_IMPORT_DECISIONS, CONF_IMPORT_PREVIEW, DOMAIN, SUBENTRY_TYPE_PAGE
from .subentries import iter_config_subentries


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for the POC.

    This intentionally exposes parsed import summaries but not raw uploaded page
    export content.
    """
    pages: list[dict[str, Any]] = []
    for subentry in iter_config_subentries(entry):
        if subentry.subentry_type != SUBENTRY_TYPE_PAGE:
            continue
        pages.append(
            {
                "subentry_id": subentry.subentry_id,
                "title": subentry.title,
                "data": {
                    "page_number": subentry.data.get("page_number"),
                    "page_name": subentry.data.get("page_name"),
                    "page_id": subentry.data.get("page_id"),
                    CONF_IMPORT_PREVIEW: subentry.data.get(CONF_IMPORT_PREVIEW),
                    CONF_IMPORT_DECISIONS: subentry.data.get(CONF_IMPORT_DECISIONS),
                    "planned_entities": subentry.data.get("planned_entities"),
                    "observer_surface": subentry.data.get("observer_surface"),
                    "import_lifecycle": subentry.data.get("import_lifecycle"),
                },
            }
        )

    return {
        "domain": DOMAIN,
        "title": entry.title,
        "config": dict(entry.data),
        "options": dict(entry.options),
        "imported_pages": pages,
        "poc_note": "POC v2 creates variable-text sensor entities and updates them from live rendered Companion text. Buttons and switches remain planned metadata.",
    }
