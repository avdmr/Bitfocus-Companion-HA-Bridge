"""Sensor platform for Bitfocus Companion Bridge POC v2."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MANUFACTURER, SIGNAL_LIVE_STATE_UPDATE, SUBENTRY_TYPE_PAGE
from .subentries import iter_config_subentries

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up variable-text sensors from imported page planned_entities.

    Important POC v23 behavior:
    sensors are added per Companion page config subentry using
    ``config_subentry_id``. This should make Home Assistant group the sensor
    entities under the matching ``Companion Page N`` subentry instead of under
    "Devices that don't belong to a sub-entry".
    """
    for subentry in iter_config_subentries(entry):
        if getattr(subentry, "subentry_type", None) != SUBENTRY_TYPE_PAGE:
            continue

        entities = _build_sensors_for_page_subentry(hass, entry, subentry)
        if not entities:
            continue

        subentry_id = getattr(subentry, "subentry_id", None)
        if subentry_id is None:
            # Compatibility fallback for older/unexpected HA subentry objects.
            _LOGGER.debug(
                "Adding %s Companion Bridge sensor(s) without config_subentry_id; "
                "the Home Assistant subentry object did not expose subentry_id",
                len(entities),
            )
            async_add_entities(entities)
            continue

        try:
            async_add_entities(entities, config_subentry_id=subentry_id)
            _LOGGER.debug(
                "Added %s Companion Bridge sensor(s) for page subentry %s",
                len(entities),
                subentry_id,
            )
        except TypeError:
            # Some HA versions may not yet accept the keyword on the callback.
            # Keep the POC usable rather than failing platform setup.
            _LOGGER.debug(
                "Home Assistant AddEntitiesCallback did not accept config_subentry_id; "
                "falling back to normal entity add for %s sensor(s)",
                len(entities),
                exc_info=True,
            )
            async_add_entities(entities)


def _build_sensors_for_page_subentry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    subentry: Any,
) -> list["CompanionVariableTextSensor"]:
    """Return active variable-text sensor entities for one page subentry."""
    page_data = dict(getattr(subentry, "data", {}) or {})
    page_number = int(page_data.get("page_number") or 0)
    page_name = str(page_data.get("page_name") or "")
    entities: list[CompanionVariableTextSensor] = []

    for planned in page_data.get("planned_entities") or []:
        if not isinstance(planned, dict):
            continue
        if planned.get("domain") != "sensor":
            continue
        if planned.get("status", "active") != "active":
            continue
        entities.append(
            CompanionVariableTextSensor(
                hass=hass,
                entry=entry,
                page_number=page_number,
                page_name=page_name,
                planned_entity=dict(planned),
            )
        )

    return entities


class CompanionVariableTextSensor(SensorEntity):
    """Sensor whose value is the live rendered Companion button text."""

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
        self._state_data: dict[str, Any] | None = None
        self._remove_dispatcher: Any = None

        self._attr_unique_id = str(planned_entity["unique_id"])
        self._attr_suggested_object_id = str(planned_entity.get("suggested_object_id") or "")
        self._attr_name = str(planned_entity.get("suggested_object_id") or planned_entity.get("location_key") or self._location)

    @property
    def native_value(self) -> str | None:
        """Return live rendered Companion text, if received."""
        if not self._state_data:
            return None
        return str(self._state_data.get("text") or "")

    @property
    def available(self) -> bool:
        """Entity is available after at least one live state update has arrived."""
        return self._state_data is not None

    @property
    def device_info(self) -> dict[str, Any]:
        """Group sensors under a Companion Page device."""
        page_label = f"Companion Page {self._page_number}"
        if self._page_name:
            page_label = f"{page_label} - {self._page_name}"
        return {
            "identifiers": {(DOMAIN, f"{self._entry.entry_id}_p{self._page_number}")},
            "name": page_label,
            "manufacturer": MANUFACTURER,
            "model": "Companion page export",
            "configuration_url": f"http://{self._entry.data.get('host')}:{self._entry.data.get('http_port', 8000)}/surfaces/configured",
        }

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return debug/trace attributes useful during the POC."""
        attrs: dict[str, Any] = {
            "companion_location": self._location,
            "companion_page": self._page_number,
            "text_template": self._planned_entity.get("text_template") or "",
            "variable_refs": self._planned_entity.get("variable_refs") or [],
            "source": self._planned_entity.get("source"),
            "poc_entity_model": "variable_text_sensor",
        }
        if self._state_data:
            attrs.update(
                {
                    "live_source": self._state_data.get("source"),
                    "background": self._state_data.get("color"),
                    "text_color": self._state_data.get("text_color"),
                    "font_size": self._state_data.get("font_size"),
                    "pressed": self._state_data.get("pressed"),
                }
            )
        return attrs

    async def async_added_to_hass(self) -> None:
        """Subscribe to runtime live-state updates."""
        data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})
        live_states = data.get("live_states") or {}
        existing = live_states.get(self._location)
        if isinstance(existing, dict):
            self._state_data = existing

        signal = f"{SIGNAL_LIVE_STATE_UPDATE}_{self._entry.entry_id}_{self._location}"

        @callback
        def _handle_update(state_data: dict[str, Any]) -> None:
            self._state_data = dict(state_data)
            self.async_write_ha_state()

        self._remove_dispatcher = async_dispatcher_connect(self.hass, signal, _handle_update)
        if self._remove_dispatcher:
            self.async_on_remove(self._remove_dispatcher)
