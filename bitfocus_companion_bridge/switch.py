"""Switch platform for Bitfocus Companion Bridge."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, SIGNAL_LIVE_STATE_UPDATE, SUBENTRY_TYPE_PAGE
from .entity_helpers import async_press_companion_location, page_device_info, signature_matches
from .subentries import iter_config_subentries

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Companion switch entities from imported page planned_entities."""
    for subentry in iter_config_subentries(entry):
        if getattr(subentry, "subentry_type", None) != SUBENTRY_TYPE_PAGE:
            continue
        if (getattr(subentry, "data", {}) or {}).get("deleted"):
            continue
        entities = _build_switches_for_page_subentry(hass, entry, subentry)
        if not entities:
            continue
        subentry_id = getattr(subentry, "subentry_id", None)
        try:
            if subentry_id is not None:
                async_add_entities(entities, config_subentry_id=subentry_id)
            else:
                async_add_entities(entities)
        except TypeError:
            _LOGGER.debug("AddEntitiesCallback does not support config_subentry_id for switches", exc_info=True)
            async_add_entities(entities)


def _build_switches_for_page_subentry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    subentry: Any,
) -> list["CompanionRenderSignatureSwitch"]:
    """Return active switch entities for one imported page."""
    page_data = dict(getattr(subentry, "data", {}) or {})
    page_number = int(page_data.get("page_number") or 0)
    page_name = str(page_data.get("page_name") or "")
    entities: list[CompanionRenderSignatureSwitch] = []
    for planned in page_data.get("planned_entities") or []:
        if not isinstance(planned, dict):
            continue
        if planned.get("domain") != "switch":
            continue
        if planned.get("status", "active") != "active":
            continue
        entities.append(
            CompanionRenderSignatureSwitch(
                hass=hass,
                entry=entry,
                page_number=page_number,
                page_name=page_name,
                planned_entity=dict(planned),
            )
        )
    return entities


class CompanionRenderSignatureSwitch(SwitchEntity, RestoreEntity):
    """Switch whose state is derived from live rendered Companion button state."""

    _attr_should_poll = False
    _optimistic_grace_seconds = 0.5

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
        self._last_match: str | None = None
        self._last_known_is_on = self._restore_import_or_mapping_state()
        self._state_quality = "import_confirmed_previous_state" if self._last_known_is_on is not None else "unknown"
        self._optimistic_desired: bool | None = None
        self._optimistic_until: float = 0.0

        self._attr_unique_id = str(planned_entity["unique_id"])
        self._attr_suggested_object_id = str(planned_entity.get("suggested_object_id") or "")
        # Keep entity names location-based. The Companion button label can change
        # without changing what location this entity controls/observes.
        self._attr_name = str(planned_entity.get("suggested_object_id") or planned_entity.get("location_key") or self._location)

    @property
    def device_info(self) -> dict[str, Any]:
        """Group the switch under its Companion page device."""
        return page_device_info(self._entry, self._page_number, self._page_name)

    @staticmethod
    def _coerce_on_off(value: Any) -> bool | None:
        """Convert stored ON/OFF strings or bools to a bool state."""
        if isinstance(value, bool):
            return value
        if value is None:
            return None
        text = str(value).strip().lower()
        if text in {"on", "true", "1", "yes"}:
            return True
        if text in {"off", "false", "0", "no"}:
            return False
        return None

    def _mapping_value(self, key: str, default: Any = None) -> Any:
        """Return mapping data from the v0.3 flat fields or older nested storage."""
        if key in self._planned_entity and self._planned_entity.get(key) not in (None, {}, []):
            return self._planned_entity.get(key)
        mapping = self._planned_entity.get("switch_mapping")
        if isinstance(mapping, dict):
            value = mapping.get(key)
            if value not in (None, {}, []):
                return value
        return default

    def _has_mapping(self) -> bool:
        """Return true when at least one ON/OFF render signature is stored."""
        return bool(self._mapping_value("on_signature", {}) or self._mapping_value("off_signature", {}))

    def _restore_import_or_mapping_state(self) -> bool | None:
        """Return the best initial previous-known state from stored import metadata."""
        for key in ("confirmed_current_state", "guessed_current_state"):
            value = self._mapping_value(key)
            coerced = self._coerce_on_off(value)
            if coerced is not None:
                return coerced
        return None

    def _now(self) -> float:
        """Return event loop time for debounce/optimistic state windows."""
        try:
            return float(self.hass.loop.time())
        except Exception:
            return 0.0

    def _signature_state_for_live(self) -> bool | None:
        """Return the state that the current live render confirms, if any."""
        if not self._state_data:
            return None
        fields = list(self._mapping_value("match_fields", ["text", "background", "text_color"]) or ["text", "background", "text_color"])
        on_signature = self._mapping_value("on_signature", {}) or {}
        off_signature = self._mapping_value("off_signature", {}) or {}
        if signature_matches(self._state_data, on_signature, fields):
            return True
        if signature_matches(self._state_data, off_signature, fields):
            return False
        return None

    def _clear_optimistic_if_expired(self) -> None:
        """Clear an optimistic press hold after its settling window expires."""
        if self._optimistic_desired is None:
            return
        if self._now() >= self._optimistic_until:
            self._optimistic_desired = None
            self._optimistic_until = 0.0

    def _matched_state(self) -> bool | None:
        """Return confirmed state, or an educated/optimistic previous-known state."""
        if not self._state_data:
            self._last_match = "no_live_state"
            # Keep using the import-confirmed/restored previous-known state while
            # no live render has arrived yet. If the user just toggled, keep the
            # optimistic state while Companion's visual feedback settles.
            if self._optimistic_desired is not None and self._now() < self._optimistic_until:
                self._last_known_is_on = self._optimistic_desired
                self._state_quality = "optimistic_after_press_waiting_for_render"
                return self._optimistic_desired
            self._clear_optimistic_if_expired()
            return self._last_known_is_on

        live_match = self._signature_state_for_live()

        # After a Companion press, Surface mode can briefly emit the previous
        # visual state or a transient pressed/released render before the final
        # feedback state arrives. During this small settling window, keep the HA
        # switch on the requested state unless the live render already confirms
        # that same requested state. This prevents the UI from flickering
        # off/on or on/off immediately after a toggle.
        if self._optimistic_desired is not None:
            if live_match is self._optimistic_desired:
                self._last_known_is_on = live_match
                self._state_quality = "confirmed_by_render_signature_after_press"
                self._last_match = "on_signature" if live_match else "off_signature"
                self._optimistic_desired = None
                self._optimistic_until = 0.0
                return live_match
            if self._now() < self._optimistic_until:
                self._last_known_is_on = self._optimistic_desired
                self._state_quality = "optimistic_after_press_waiting_for_render_settle"
                if live_match is None:
                    self._last_match = "optimistic_hold_while_live_signature_unknown"
                else:
                    self._last_match = "optimistic_hold_ignoring_previous_render"
                return self._optimistic_desired
            self._optimistic_desired = None
            self._optimistic_until = 0.0

        if live_match is True:
            self._last_match = "on_signature"
            self._last_known_is_on = True
            self._state_quality = "confirmed_by_render_signature"
            return True
        if live_match is False:
            self._last_match = "off_signature"
            self._last_known_is_on = False
            self._state_quality = "confirmed_by_render_signature"
            return False

        self._last_match = "unknown_signature_using_previous_known_state"
        if self._last_known_is_on is not None:
            self._state_quality = "educated_guess_previous_known_state"
            return self._last_known_is_on

        self._state_quality = "unknown_no_previous_state"
        return None

    @property
    def is_on(self) -> bool | None:
        """Return current switch state, or None when not yet known."""
        return self._matched_state()

    @property
    def available(self) -> bool:
        """Available when a mapping exists and either live or previous-known state exists."""
        return self._has_mapping() and (self._state_data is not None or self._last_known_is_on is not None or self._optimistic_desired is not None)

    @property
    def assumed_state(self) -> bool:
        """Mark the entity as assumed while using an educated/optimistic state."""
        return not self._state_quality.startswith("confirmed_by_render_signature")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "companion_location": self._location,
            "companion_page": self._page_number,
            "source": self._planned_entity.get("source"),
            "state_source": self._mapping_value("state_source", "render_signature") or "render_signature",
            "match_fields": self._mapping_value("match_fields", ["text", "background", "text_color"]) or ["text", "background", "text_color"],
            "last_signature_match": self._last_match,
            "last_known_state": ("on" if self._last_known_is_on is True else "off" if self._last_known_is_on is False else None),
            "state_quality": self._state_quality,
            "optimistic_desired_state": ("on" if self._optimistic_desired is True else "off" if self._optimistic_desired is False else None),
            "optimistic_until_seconds": round(max(0.0, self._optimistic_until - self._now()), 3) if self._optimistic_desired is not None else 0,
            "poc_entity_model": "render_signature_switch",
        }
        if self._state_data:
            attrs.update(
                {
                    "live_source": self._state_data.get("source"),
                    "live_text": self._state_data.get("text"),
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
            self._matched_state()

        last_state = await self.async_get_last_state()
        if last_state is not None and str(last_state.state).lower() in {"on", "off"}:
            restored = str(last_state.state).lower() == "on"
            # Prefer an actual live signature match. Otherwise the restored HA
            # state is the best previous-known state across restarts/reloads.
            if self._last_match not in {"on_signature", "off_signature"}:
                self._last_known_is_on = restored
                self._state_quality = "restored_previous_known_state"
                self._last_match = "restored_previous_known_state"

        signal = f"{SIGNAL_LIVE_STATE_UPDATE}_{self._entry.entry_id}_{self._location}"

        @callback
        def _handle_update(state_data: dict[str, Any]) -> None:
            self._state_data = dict(state_data)
            self._matched_state()
            self.async_write_ha_state()

        self._remove_dispatcher = async_dispatcher_connect(self.hass, signal, _handle_update)
        if self._remove_dispatcher:
            self.async_on_remove(self._remove_dispatcher)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the Companion switch on by pressing only when current state is known off."""
        await self._press_if_needed(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the Companion switch off by pressing only when current state is known on."""
        await self._press_if_needed(False)

    async def _press_if_needed(self, desired: bool) -> None:
        current = self.is_on
        if current is desired:
            return
        if current is None:
            raise HomeAssistantError(
                f"Current Companion switch state for {self._location} is unknown and no previous-known state could be restored; "
                "cannot safely toggle. Re-import this location as a switch and confirm the current ON/OFF state."
            )
        try:
            await async_press_companion_location(self.hass, self._entry, self._location)
        except Exception as exc:  # pragma: no cover - network dependent
            _LOGGER.warning("Companion switch press failed for %s: %s", self._location, exc)
            raise HomeAssistantError(f"Could not press Companion location {self._location}: {exc}") from exc

        # Companion switches are toggle buttons. After a successful press we can
        # optimistically move the previous known state to the requested state; a
        # later live render update will confirm it or keep using the last known
        # state if the render no longer matches either stored signature.
        self._last_known_is_on = desired
        self._optimistic_desired = desired
        self._optimistic_until = self._now() + self._optimistic_grace_seconds
        self._state_quality = "optimistic_after_press_waiting_for_render"
        self._last_match = "optimistic_after_press"
        self.async_write_ha_state()
