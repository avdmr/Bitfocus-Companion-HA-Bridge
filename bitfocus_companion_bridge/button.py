"""Button platform for Bitfocus Companion Bridge."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SUBENTRY_TYPE_PAGE
from .entity_helpers import async_press_companion_location, page_device_info
from .subentries import iter_config_subentries

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Companion button entities from imported page planned_entities."""
    for subentry in iter_config_subentries(entry):
        if getattr(subentry, "subentry_type", None) != SUBENTRY_TYPE_PAGE:
            continue
        entities = _build_buttons_for_page_subentry(hass, entry, subentry)
        if not entities:
            continue
        subentry_id = getattr(subentry, "subentry_id", None)
        try:
            if subentry_id is not None:
                async_add_entities(entities, config_subentry_id=subentry_id)
            else:
                async_add_entities(entities)
        except TypeError:
            _LOGGER.debug("AddEntitiesCallback does not support config_subentry_id for buttons", exc_info=True)
            async_add_entities(entities)


def _build_buttons_for_page_subentry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    subentry: Any,
) -> list["CompanionLocationButton"]:
    """Return active button entities for one imported page."""
    page_data = dict(getattr(subentry, "data", {}) or {})
    page_number = int(page_data.get("page_number") or 0)
    page_name = str(page_data.get("page_name") or "")
    entities: list[CompanionLocationButton] = []
    for planned in page_data.get("planned_entities") or []:
        if not isinstance(planned, dict):
            continue
        if planned.get("domain") != "button":
            continue
        if planned.get("status", "active") != "active":
            continue
        entities.append(
            CompanionLocationButton(
                hass=hass,
                entry=entry,
                page_number=page_number,
                page_name=page_name,
                planned_entity=dict(planned),
            )
        )
    return entities


class CompanionLocationButton(ButtonEntity):
    """Button that triggers a Companion absolute location via HTTP."""

    _attr_should_poll = False

    def __init__(
        self,
        *,
        hass: HomeAssistant,
        entry: ConfigEntry,
        page_number: int,
        page_name: str,
        planned_entity: dict[str, Any],
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._page_number = page_number
        self._page_name = page_name
        self._planned_entity = planned_entity
        self._location = str(planned_entity["location"])
        self._attr_unique_id = str(planned_entity["unique_id"])
        self._attr_suggested_object_id = str(planned_entity.get("suggested_object_id") or "")
        self._attr_name = str(planned_entity.get("suggested_object_id") or planned_entity.get("location_key") or self._location)

    @property
    def device_info(self) -> dict[str, Any]:
        """Group the button under its Companion page device."""
        return page_device_info(self._entry, self._page_number, self._page_name)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "companion_location": self._location,
            "companion_page": self._page_number,
            "source": self._planned_entity.get("source"),
            "poc_entity_model": "location_button",
        }

    async def async_press(self) -> None:
        """Press the Companion button location."""
        try:
            await async_press_companion_location(self.hass, self._entry, self._location)
        except Exception as exc:  # pragma: no cover - network dependent
            _LOGGER.warning("Companion button press failed for %s: %s", self._location, exc)
            raise HomeAssistantError(f"Could not press Companion location {self._location}: {exc}") from exc
