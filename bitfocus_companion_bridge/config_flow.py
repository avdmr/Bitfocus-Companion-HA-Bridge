"""Config flow for Bitfocus Companion Bridge."""

from __future__ import annotations

from collections.abc import Mapping
import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    FlowType,
    OptionsFlowWithReload,
    SOURCE_USER,
    SubentryFlowContext,
    SubentryFlowResult,
)
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    FileSelector,
    FileSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from .api import CannotConnect, async_probe_companion
from .const import (
    CONF_HTTP_PORT,
    CONF_WEB_UI_PORT,
    CONF_IMPORT_DECISIONS,
    CONF_IMPORT_FILE,
    CONF_IMPORT_PREVIEW,
    CONF_IMPORT_SENSOR_CANDIDATES,
    CONF_SENSOR_IMPORT_MODE,
    CONF_IMPORT_TEXT,
    CONF_LEARNING_MODE,
    CONF_LIVE_STATE_VERIFICATION,
    CONF_OBSERVER_BACKEND,
    CONF_PAGE_ID,
    CONF_PAGE_NAME,
    CONF_PAGE_NUMBER,
    CONF_PAGE_OBSERVER_BACKEND,
    CONF_PAGE_MANAGE_ACTION,
    CONF_SENSOR_MANAGE_ACTION,
    CONF_SATELLITE_PORT,
    CONF_START_PAGE_IMPORT,
    CONF_VERIFY_LIVE_ACCESS,
    CONF_SWITCH_IMPORT_MODE,
    CONF_SWITCH_CURRENT_STATE,
    CONF_SWITCH_INITIAL_STATE,
    CONF_SWITCH_MAPPING_MODE,
    CONF_TEST_CONNECTION,
    CONF_VISUAL_AUTODETECT,
    DEFAULT_HTTP_PORT,
    DEFAULT_WEB_UI_PORT,
    DEFAULT_NAME,
    DEFAULT_OBSERVER_BACKEND,
    DEFAULT_SATELLITE_PORT,
    DOMAIN,
    MAX_DYNAMIC_CANDIDATE_FIELDS,
    PAGE_ACTION_MANAGE_SENSORS,
    PAGE_ACTION_REIMPORT,
    OBSERVER_BACKENDS,
    OBSERVER_BACKEND_SURFACE,
    SENSOR_IMPORT_IGNORE,
    SENSOR_IMPORT_IMPORT,
    SENSOR_IMPORT_MODES,
    SENSOR_MANAGE_DELETE,
    SENSOR_MANAGE_DISABLE,
    SENSOR_MANAGE_ENABLE,
    SENSOR_MANAGE_NO_CHANGE,
    SUBENTRY_TYPE_PAGE,
    SWITCH_IMPORT_BUTTON,
    SWITCH_IMPORT_IGNORE,
    SWITCH_IMPORT_MODES,
    SWITCH_IMPORT_SWITCH,
    SWITCH_INITIAL_STATES,
    SWITCH_INITIAL_UNKNOWN,
    SWITCH_MAPPING_DEFAULT_OFF_FEEDBACK_ON,
    SWITCH_MAPPING_MODES,
)
from .entity_model import (
    build_page_registry_data,
    find_page_subentry,
    future_entity_metadata,
    observer_surface_metadata,
    page_unique_id,
)
from .live_state import (
    LiveButtonState,
    LiveStateVerificationResult,
    async_read_live_states_for_locations,
    async_verify_live_state_access,
)
from .parser import CompanionPageParseError, ControlCandidate, PageImportPreview, parse_companion_page_export

_LOGGER = logging.getLogger(__name__)


def _port_selector(default: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=1,
            max=65535,
            mode=NumberSelectorMode.BOX,
            step=1,
        )
    )


class BitfocusCompanionBridgeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the main config flow."""

    VERSION = 1
    MINOR_VERSION = 0

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlowWithReload:
        """Create the options flow."""
        return BitfocusCompanionBridgeOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls,
        config_entry: ConfigEntry,
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentries supported by this integration."""
        return {SUBENTRY_TYPE_PAGE: PageImportSubentryFlow}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Create the main Companion instance configuration."""
        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {}

        if user_input is not None:
            data = dict(user_input)
            # Companion HTTP Remote Control uses the same host/port as the Companion web app.
            # Keep both keys in storage for forward compatibility with the later entity/control layer.
            data[CONF_HTTP_PORT] = int(data[CONF_HTTP_PORT])
            data[CONF_WEB_UI_PORT] = data[CONF_HTTP_PORT]
            # Satellite API is a separate TCP port used by Surface mode and Subscription API mode.
            data[CONF_SATELLITE_PORT] = int(data[CONF_SATELLITE_PORT])

            if data.get(CONF_TEST_CONNECTION, True):
                try:
                    probe = await async_probe_companion(
                        data[CONF_HOST],
                        data[CONF_HTTP_PORT],
                        data[CONF_SATELLITE_PORT],
                    )
                    description_placeholders["probe"] = probe.satellite_banner or "connected"
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                except Exception:  # pragma: no cover - defensive for unknown network errors
                    _LOGGER.exception("Unexpected error while probing Companion")
                    errors["base"] = "unknown"

            if not errors:
                # Companion does not give us a stable instance ID in this POC. We use
                # the user-provided connection tuple to prevent accidental duplicates.
                await self.async_set_unique_id(
                    f"{data[CONF_HOST]}:{data[CONF_HTTP_PORT]}"
                )
                self._abort_if_unique_id_configured()

                title = data.get(CONF_NAME) or DEFAULT_NAME
                options = {
                    CONF_OBSERVER_BACKEND: data.pop(CONF_OBSERVER_BACKEND),
                    # Kept in storage for forward compatibility, but hidden in the POC UI
                    # because it does not do anything until live visual-state discovery exists.
                    CONF_VISUAL_AUTODETECT: False,
                    CONF_LEARNING_MODE: data.pop(CONF_LEARNING_MODE),
                }
                start_page_import = bool(data.pop(CONF_START_PAGE_IMPORT, True))
                data.pop(CONF_TEST_CONNECTION, None)

                result = self.async_create_entry(title=title, data=data, options=options)
                if start_page_import:
                    self._start_page_import_after_create = True
                return result

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=DEFAULT_NAME): TextSelector(),
                    vol.Required(CONF_HOST): TextSelector(),
                    vol.Required(CONF_HTTP_PORT, default=DEFAULT_HTTP_PORT): _port_selector(DEFAULT_HTTP_PORT),
                    vol.Required(CONF_SATELLITE_PORT, default=DEFAULT_SATELLITE_PORT): _port_selector(DEFAULT_SATELLITE_PORT),
                    vol.Required(CONF_OBSERVER_BACKEND, default=DEFAULT_OBSERVER_BACKEND): SelectSelector(
                        SelectSelectorConfig(
                            options=OBSERVER_BACKENDS,
                            mode=SelectSelectorMode.DROPDOWN,
                            translation_key="observer_backend",
                        )
                    ),
                    vol.Required(CONF_LEARNING_MODE, default=False): BooleanSelector(),
                    vol.Required(CONF_TEST_CONNECTION, default=True): BooleanSelector(),
                    vol.Required(CONF_START_PAGE_IMPORT, default=True): BooleanSelector(),
                }
            ),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def async_on_create_entry(self, result: ConfigFlowResult) -> ConfigFlowResult:
        """Optionally continue straight into the page-import subentry flow."""
        if not getattr(self, "_start_page_import_after_create", False):
            return result

        subentry_result = await self.hass.config_entries.subentries.async_init(
            (result["result"].entry_id, SUBENTRY_TYPE_PAGE),
            context=SubentryFlowContext(source=SOURCE_USER),
        )
        result["next_flow"] = (
            FlowType.CONFIG_SUBENTRIES_FLOW,
            subentry_result["flow_id"],
        )
        return result


class BitfocusCompanionBridgeOptionsFlow(OptionsFlowWithReload):
    """Options flow for changing main Companion settings."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = dict(self.config_entry.options)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_OBSERVER_BACKEND,
                        default=current.get(CONF_OBSERVER_BACKEND, DEFAULT_OBSERVER_BACKEND),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=OBSERVER_BACKENDS,
                            mode=SelectSelectorMode.DROPDOWN,
                            translation_key="observer_backend",
                        )
                    ),
                    vol.Required(
                        CONF_LEARNING_MODE,
                        default=current.get(CONF_LEARNING_MODE, False),
                    ): BooleanSelector(),
                }
            ),
        )


class PageImportSubentryFlow(ConfigSubentryFlow):
    """Handle adding or reconfiguring a Companion page import."""

    _raw_export_text: str | None = None
    _filename_hint: str | None = None
    _preview: PageImportPreview | None = None
    _decisions: dict[str, Any] | None = None
    _existing_page_subentry: Any | None = None
    _live_state_verification: LiveStateVerificationResult | None = None
    _page_observer_backend: str | None = None
    _page_satellite_port: int | None = None
    _page_web_ui_port: int | None = None
    _switch_state_context: dict[str, dict[str, Any]] | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Upload or paste a Companion page export."""
        errors: dict[str, str] = {}

        if user_input is not None:
            uploaded_file_value = user_input.get(CONF_IMPORT_FILE)
            uploaded_file_id, filename_hint = self._extract_uploaded_file_info(uploaded_file_value)
            pasted_text = (user_input.get(CONF_IMPORT_TEXT) or "").strip()

            if uploaded_file_id and pasted_text:
                errors["base"] = "choose_one_source"
            elif not uploaded_file_id and not pasted_text:
                errors["base"] = "missing_import_source"
            else:
                try:
                    if uploaded_file_id:
                        raw_text = await self.hass.async_add_executor_job(
                            self._read_uploaded_file,
                            uploaded_file_id,
                        )
                    else:
                        raw_text = pasted_text

                    self._raw_export_text = raw_text
                    self._filename_hint = filename_hint
                    self._preview = await self.hass.async_add_executor_job(
                        parse_companion_page_export,
                        raw_text,
                        filename_hint,
                        None,
                    )
                except CompanionPageParseError:
                    errors["base"] = "invalid_page_export"
                except Exception:  # pragma: no cover - defensive for file errors
                    _LOGGER.exception("Unexpected error while parsing Companion page export")
                    errors["base"] = "unknown"
                else:
                    entry = self._get_entry()
                    self._page_observer_backend = str(
                        entry.options.get(CONF_OBSERVER_BACKEND, DEFAULT_OBSERVER_BACKEND)
                    )
                    self._page_satellite_port = int(entry.data.get(CONF_SATELLITE_PORT, DEFAULT_SATELLITE_PORT))
                    self._page_web_ui_port = int(
                        entry.data.get(CONF_WEB_UI_PORT, entry.data.get(CONF_HTTP_PORT, DEFAULT_WEB_UI_PORT))
                    )
                    return await self.async_step_page_observer_backend()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_IMPORT_FILE): FileSelector(
                        FileSelectorConfig(
                            accept=".json,.yaml,.yml,.companionconfig,application/json,text/yaml,text/plain"
                        )
                    ),
                    vol.Optional(CONF_IMPORT_TEXT): TextSelector(TextSelectorConfig(multiline=True)),
                }
            ),
            errors=errors,
        )

    async def async_step_page_observer_backend(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Choose how this imported page will be read live."""
        assert self._preview is not None
        entry = self._get_entry()
        default_backend = str(entry.options.get(CONF_OBSERVER_BACKEND, DEFAULT_OBSERVER_BACKEND))
        if default_backend not in OBSERVER_BACKENDS:
            default_backend = DEFAULT_OBSERVER_BACKEND
        # Relevant ports are configured once in the main setup and reused here.
        # The page flow only asks which live-state access mode to use for this page.
        default_satellite_port = int(self._page_satellite_port or entry.data.get(CONF_SATELLITE_PORT, DEFAULT_SATELLITE_PORT))
        default_web_ui_port = int(
            self._page_web_ui_port
            or entry.data.get(CONF_WEB_UI_PORT, entry.data.get(CONF_HTTP_PORT, DEFAULT_WEB_UI_PORT))
        )

        errors: dict[str, str] = {}
        if user_input is not None:
            backend = str(user_input.get(CONF_PAGE_OBSERVER_BACKEND, default_backend))
            if backend not in OBSERVER_BACKENDS:
                backend = DEFAULT_OBSERVER_BACKEND

            self._page_observer_backend = backend
            self._page_satellite_port = default_satellite_port
            self._page_web_ui_port = default_web_ui_port
            self._sync_fixed_connection_data()
            return await self.async_step_confirm_page()

        return self.async_show_form(
            step_id="page_observer_backend",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PAGE_OBSERVER_BACKEND, default=self._page_observer_backend or default_backend): SelectSelector(
                        SelectSelectorConfig(
                            options=OBSERVER_BACKENDS,
                            mode=SelectSelectorMode.DROPDOWN,
                            translation_key="observer_backend",
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "surface_mode": "Surface mode creates one Home Assistant observer surface for this imported page. This is the most secure/default approach because the integration only sees the page assigned to that observer surface.",
                "subscription_mode": "Subscription API mode reads absolute Companion button locations without observer surfaces. It is advanced, easier for full multi-page state reads, and requires Companion's Button Subscriptions API.",
                "subscription_warning": "The Subscriptions API is required for full functionality from the Elgato plugin, but enabling it allows any satellite client to bypass the pincode/page system and interact with any button within Companion.",
            },
        )

    async def async_step_confirm_page(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Confirm or correct the Companion page number."""
        assert self._preview is not None
        errors: dict[str, str] = {}
        preview = self._preview

        if user_input is not None:
            try:
                page_number = int(user_input[CONF_PAGE_NUMBER])
                if page_number < 1:
                    raise ValueError
            except (TypeError, ValueError):
                errors[CONF_PAGE_NUMBER] = "invalid_page_number"
            else:
                preview.with_page_number(page_number)
                self._existing_page_subentry = find_page_subentry(self._get_entry(), page_number)
                return await self.async_step_verify_live_access()

        default_page_number = preview.target_page_number or preview.suggested_page_number or 1
        counts = preview.counts()
        return self.async_show_form(
            step_id="confirm_page",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PAGE_NUMBER, default=default_page_number): NumberSelector(
                        NumberSelectorConfig(min=1, max=999, mode=NumberSelectorMode.BOX, step=1)
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "page_name": preview.page_name or "-",
                "page_id": preview.page_id or "-",
                "page_number_source": preview.page_number_source or "manual",
                "companion_build": preview.companion_build or "-",
                "grid": f"{preview.columns} x {preview.rows}",
                "controls_total": str(counts["controls_total"]),
                "buttons_total": str(counts["buttons_total"]),
                "button_candidates": str(counts["button_candidates"]),
                "sensor_candidates": str(counts["sensor_candidates"]),
                "switch_candidates": str(counts["switch_candidates"]),
                "ignored_homeassistant_actions": str(counts["ignored_homeassistant_actions"]),
                "import_mode": "Re-import / update existing page" if find_page_subentry(self._get_entry(), int(default_page_number)) else "New page import",
            },
        )


    async def async_step_verify_live_access(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Verify that the configured live-state backend can read this page.

        This step is intentionally invisible when the check succeeds. The flow
        only shows this page when Companion settings need user attention; submit
        then retries the same read-only check.
        """
        assert self._preview is not None
        assert self._preview.target_page_number is not None
        entry = self._get_entry()
        backend = str(self._page_observer_backend or entry.options.get(CONF_OBSERVER_BACKEND, DEFAULT_OBSERVER_BACKEND))
        host = str(entry.data[CONF_HOST])
        satellite_port = int(self._page_satellite_port or entry.data.get(CONF_SATELLITE_PORT, DEFAULT_SATELLITE_PORT))
        web_ui_port = int(self._page_web_ui_port or entry.data.get(CONF_WEB_UI_PORT, entry.data.get(CONF_HTTP_PORT, DEFAULT_WEB_UI_PORT)))
        page_number = self._preview.target_page_number

        cached_result = self._runtime_live_state_verification(
            backend=backend,
            host=host,
            web_ui_port=web_ui_port,
            page_number=page_number,
        )
        if cached_result is not None and cached_result.success:
            self._live_state_verification = cached_result
            return await self.async_step_sensor_options()

        paused_runtime = None
        if backend == OBSERVER_BACKEND_SURFACE:
            paused_runtime = await self._stop_surface_runtime_for_verification()

        try:
            sample_row, sample_column = self._verification_sample_location()
            result = await async_verify_live_state_access(
                entry_id=entry.entry_id,
                host=host,
                satellite_port=satellite_port,
                web_ui_port=web_ui_port,
                backend=backend,
                page_number=page_number,
                rows=self._preview.rows,
                columns=self._preview.columns,
                sample_row=sample_row,
                sample_column=sample_column,
            )
        except Exception:  # pragma: no cover - defensive around network/protocol errors
            _LOGGER.exception("Unexpected error during Companion live-state verification")
            result = None
        finally:
            if paused_runtime is not None:
                try:
                    await paused_runtime.start()
                except Exception:  # pragma: no cover - runtime restart is best-effort
                    _LOGGER.debug("Could not restart live-state runtime after verification", exc_info=True)

        if result is not None:
            self._live_state_verification = result
            if result.success:
                return await self.async_step_sensor_options()
            status = self._verification_status_text(result)
            companion_url = result.companion_url
            instructions = result.instructions
            errors = {"base": "live_access_failed"}
        else:
            if backend == "subscription":
                companion_url = f"http://{host}:{web_ui_port}/settings/protocols"
                instructions = "Enable Satellite → Button Subscriptions API in Companion, then submit this page to retry."
            else:
                companion_url = f"http://{host}:{web_ui_port}/surfaces/configured"
                instructions = f"Set the Home Page / Current Page of the Home Assistant observer surface to page {page_number}, then submit this page to retry."
            status = "Unexpected error while checking live state access."
            errors = {"base": "live_access_unknown"}

        return self.async_show_form(
            step_id="verify_live_access",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={
                "backend": backend,
                "page_number": str(page_number),
                "companion_url": companion_url,
                "instructions": instructions,
                "status": status,
            },
        )

    async def async_step_discovery_options(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Compatibility alias for older in-progress POC flows."""
        return await self.async_step_sensor_options(user_input)

    async def async_step_sensor_options(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Review only sensor candidates in their own flow step."""
        assert self._preview is not None
        preview = self._preview
        self._ensure_decisions()

        if not preview.sensor_candidates:
            self._store_ignored_homeassistant_action_decisions()
            return await self.async_step_switch_options()

        if user_input is not None:
            self._decisions["sensors"] = self._parse_sensor_decisions(user_input)
            self._store_ignored_homeassistant_action_decisions()
            return await self.async_step_switch_options()

        return self.async_show_form(
            step_id="sensor_options",
            data_schema=self._build_sensor_schema(preview),
            description_placeholders={
                "sensor_candidates": self._sensor_candidate_lines(preview.sensor_candidates),
                "sensor_count": str(len(preview.sensor_candidates)),
                "max_dynamic_fields": str(MAX_DYNAMIC_CANDIDATE_FIELDS),
            },
        )

    async def async_step_switch_options(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Review Companion buttons as Ignore, Button, or Prepare as switch."""
        assert self._preview is not None
        preview = self._preview
        self._ensure_decisions()

        if not preview.switch_candidates:
            # Store ignored Home Assistant actions silently. Showing a form with no
            # fields is confusing when the frontend does not render step descriptions.
            return self._create_page_subentry()

        if user_input is not None:
            self._decisions["switches"] = self._parse_switch_decisions(user_input)
            if self._selected_switch_candidates():
                return await self.async_step_switch_state()
            # No switch was prepared, so save immediately. Ignored Home Assistant
            # actions have already been stored as explicit ignore decisions.
            return self._create_page_subentry()

        return self.async_show_form(
            step_id="switch_options",
            data_schema=self._build_switch_schema(preview),
            description_placeholders={
                "switch_candidates": self._switch_candidate_lines(preview.switch_candidates),
                "switch_count": str(len(preview.switch_candidates)),
                "max_dynamic_fields": str(MAX_DYNAMIC_CANDIDATE_FIELDS),
            },
        )

    async def async_step_ignored_homeassistant_actions(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Show controls that are ignored because they already call Home Assistant."""
        assert self._preview is not None
        self._ensure_decisions()
        self._store_ignored_homeassistant_action_decisions()

        if user_input is not None:
            return self._create_page_subentry()

        return self.async_show_form(
            step_id="ignored_homeassistant_actions",
            data_schema=vol.Schema({}),
            description_placeholders={
                "ignored_homeassistant_actions": self._ignored_homeassistant_action_lines(self._preview.ignored_homeassistant_actions),
                "ignored_count": str(len(self._preview.ignored_homeassistant_actions)),
            },
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Choose whether to re-import the page or manage existing entities."""
        page_subentry = self._current_page_subentry()
        if page_subentry is None:
            # Fallback for older/in-progress flows: run the import path.
            return await self.async_step_user(user_input)

        if user_input is not None:
            action = str(user_input.get(CONF_PAGE_MANAGE_ACTION) or PAGE_ACTION_REIMPORT)
            if action == PAGE_ACTION_MANAGE_SENSORS:
                return await self.async_step_manage_sensors()
            return await self.async_step_user()

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PAGE_MANAGE_ACTION, default=PAGE_ACTION_MANAGE_SENSORS): SelectSelector(
                        SelectSelectorConfig(
                            options=self._page_manage_options(),
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            description_placeholders={
                "page_title": getattr(page_subentry, "title", "Companion page"),
                "page_number": str((page_subentry.data or {}).get("page_number") or "?"),
                "sensor_count": str(len(self._planned_sensor_entities(page_subentry))),
            },
        )

    async def async_step_manage_sensors(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Disable or permanently delete imported entities for this page."""
        page_subentry = self._current_page_subentry()
        if page_subentry is None:
            return self.async_abort(reason="page_not_found")

        sensors = self._planned_sensor_entities(page_subentry)
        if not sensors:
            return self.async_show_form(
                step_id="manage_sensors",
                data_schema=vol.Schema({}),
                errors={"base": "no_entities"},
                description_placeholders={
                    "page_title": getattr(page_subentry, "title", "Companion page"),
                    "sensor_lines": "No imported entities are stored for this page.",
                },
            )

        if user_input is not None:
            return await self._apply_sensor_management(page_subentry, user_input)

        return self.async_show_form(
            step_id="manage_sensors",
            data_schema=self._build_manage_sensors_schema(sensors),
            description_placeholders={
                "page_title": getattr(page_subentry, "title", "Companion page"),
                "sensor_lines": self._manage_sensor_lines(sensors),
            },
        )

    def _current_page_subentry(self) -> Any | None:
        """Best-effort lookup of the page subentry being reconfigured.

        Home Assistant passes different context fields across releases. The
        POC accepts all known shapes and falls back to the only page subentry
        when there is just one imported page.
        """
        entry = self._get_entry()
        context = getattr(self, "context", {}) or {}
        possible_ids = {
            str(value)
            for key in ("subentry_id", "subentry_unique_id", "unique_id", "config_subentry_id")
            for value in [context.get(key)]
            if value
        }
        page_subentries = []
        for subentry in getattr(entry, "subentries", {}).values() if hasattr(getattr(entry, "subentries", {}), "values") else getattr(entry, "subentries", []) or []:
            if getattr(subentry, "subentry_type", None) != SUBENTRY_TYPE_PAGE:
                continue
            page_subentries.append(subentry)
            identifiers = {
                str(getattr(subentry, "subentry_id", "")),
                str(getattr(subentry, "unique_id", "")),
                str((getattr(subentry, "data", {}) or {}).get("page_unique_id", "")),
            }
            if possible_ids and identifiers & possible_ids:
                return subentry

        if self._existing_page_subentry is not None:
            return self._existing_page_subentry
        if self._preview and self._preview.target_page_number:
            return find_page_subentry(entry, self._preview.target_page_number)
        if len(page_subentries) == 1:
            return page_subentries[0]
        return None

    @staticmethod
    def _planned_sensor_entities(page_subentry: Any) -> list[dict[str, Any]]:
        """Return stored planned entities for a page subentry.

        The method name is kept for in-progress flow compatibility; it now
        returns sensors, buttons and switches.
        """
        data = getattr(page_subentry, "data", {}) or {}
        entities: list[dict[str, Any]] = []
        for planned in data.get("planned_entities") or []:
            if isinstance(planned, Mapping) and planned.get("domain") in {"sensor", "button", "switch"}:
                if planned.get("status") != "removed":
                    entities.append(dict(planned))
        return entities

    @staticmethod
    def _page_manage_options() -> list[dict[str, str]]:
        """Return page reconfigure action options."""
        return [
            {"value": PAGE_ACTION_MANAGE_SENSORS, "label": "Manage imported entities"},
            {"value": PAGE_ACTION_REIMPORT, "label": "Re-import page export"},
        ]

    @staticmethod
    def _sensor_manage_options() -> list[dict[str, str]]:
        """Return entity management options.

        Constant names are kept for backwards compatibility with in-progress POC
        flows, but the options apply to sensors, buttons and switches.
        """
        return [
            {"value": SENSOR_MANAGE_NO_CHANGE, "label": "No change"},
            {"value": SENSOR_MANAGE_ENABLE, "label": "Enable / keep active"},
            {"value": SENSOR_MANAGE_DISABLE, "label": "Disable entity"},
            {"value": SENSOR_MANAGE_DELETE, "label": "Delete permanently"},
        ]

    @staticmethod
    def _manage_sensor_key(entity: Mapping[str, Any]) -> str:
        """Return a stable dynamic field label for one managed entity."""
        domain = str(entity.get("domain") or "entity")
        location = str(entity.get("location") or "?")
        entity_id = str(entity.get("suggested_entity_id") or entity.get("suggested_object_id") or domain)
        status = str(entity.get("status") or "active")
        return f"{domain.title()} {location} — {entity_id} — {status}"

    def _build_manage_sensors_schema(self, entities: list[dict[str, Any]]) -> vol.Schema:
        """Build a dynamic management form for existing imported entities."""
        schema: dict[Any, Any] = {}
        for entity in entities[:MAX_DYNAMIC_CANDIDATE_FIELDS]:
            schema[vol.Required(self._manage_sensor_key(entity), default=SENSOR_MANAGE_NO_CHANGE)] = SelectSelector(
                SelectSelectorConfig(
                    options=self._sensor_manage_options(),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        return vol.Schema(schema)

    @staticmethod
    def _sensor_status_label(entity: Mapping[str, Any]) -> str:
        status = str(entity.get("status") or "active")
        if status == "disabled_by_integration":
            return "Disabled"
        if status == "pending_removal":
            return "Pending removal"
        return "Active"

    def _manage_sensor_lines(self, entities: list[dict[str, Any]]) -> str:
        """Return Markdown lines for the Manage entities page."""
        if not entities:
            return "- No imported entities are stored for this page."
        lines: list[str] = []
        for entity in entities[:MAX_DYNAMIC_CANDIDATE_FIELDS]:
            domain = str(entity.get("domain") or "entity")
            location = str(entity.get("location") or "?")
            entity_id = str(entity.get("suggested_entity_id") or entity.get("suggested_object_id") or domain)
            status = self._sensor_status_label(entity)
            source = str(entity.get("source") or "")
            extra = ""
            if entity.get("missing_from_latest_import"):
                extra = " Preserved from an earlier import and not detected in the latest export."
            lines.append(f"- `{location}` — `{entity_id}` — type: **{domain}** — status: **{status}** — source: `{source}`.{extra}")
        remaining = len(entities) - MAX_DYNAMIC_CANDIDATE_FIELDS
        if remaining > 0:
            lines.append(f"- ... and {remaining} more")
        return "\n".join(lines)

    async def _apply_sensor_management(self, page_subentry: Any, user_input: Mapping[str, Any]) -> SubentryFlowResult:
        """Apply disable/delete entity management choices and reload the entry."""
        entry = self._get_entry()
        data = dict(page_subentry.data or {})
        planned_entities = [dict(item) for item in data.get("planned_entities") or [] if isinstance(item, Mapping)]
        entity_reg = er.async_get(self.hass)
        changed = False
        updated_entities: list[dict[str, Any]] = []

        for planned in planned_entities:
            domain = str(planned.get("domain") or "")
            if domain not in {"sensor", "button", "switch"} or planned.get("status") == "removed":
                updated_entities.append(planned)
                continue
            action = str(user_input.get(self._manage_sensor_key(planned), SENSOR_MANAGE_NO_CHANGE))
            unique_id = str(planned.get("unique_id") or "")
            entity_id = None
            if unique_id:
                entity_id = entity_reg.async_get_entity_id(domain, DOMAIN, unique_id)

            if action == SENSOR_MANAGE_DELETE:
                if entity_id:
                    entity_reg.async_remove(entity_id)
                changed = True
                # Do not keep the planned entity in subentry data after explicit deletion.
                continue

            if action == SENSOR_MANAGE_DISABLE:
                planned["status"] = "disabled_by_integration"
                planned["removal_reason"] = "disabled_from_manage_entities"
                if entity_id:
                    try:
                        entity_reg.async_update_entity(entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION)
                    except Exception:
                        _LOGGER.debug("Could not mark entity %s disabled in registry", entity_id, exc_info=True)
                changed = True
            elif action == SENSOR_MANAGE_ENABLE:
                planned["status"] = "active"
                planned.pop("removal_reason", None)
                planned.pop("missing_from_latest_import", None)
                planned.pop("preserved_from_previous_import", None)
                if entity_id:
                    try:
                        entity_reg.async_update_entity(entity_id, disabled_by=None)
                    except Exception:
                        _LOGGER.debug("Could not enable entity %s in registry", entity_id, exc_info=True)
                changed = True

            updated_entities.append(planned)

        if not changed:
            return self.async_abort(reason="no_changes")

        data["planned_entities"] = updated_entities
        lifecycle = dict(data.get("import_lifecycle") or {})
        lifecycle["last_entity_management"] = "updated"
        data["import_lifecycle"] = lifecycle

        return self.async_update_reload_and_abort(
            entry,
            page_subentry,
            title=getattr(page_subentry, "title", data.get("title", "Companion page")),
            data=data,
            unique_id=getattr(page_subentry, "unique_id", data.get("page_unique_id")),
            reload_even_if_entry_is_unchanged=True,
        )


    def _runtime_live_state_verification(
        self,
        *,
        backend: str,
        host: str,
        web_ui_port: int,
        page_number: int,
    ) -> LiveStateVerificationResult | None:
        """Return a successful verification result from the already-running runtime cache.

        During re-import the persistent runtime may already have the integration-
        owned Surface observer connected and receiving KEY-STATE for this page.
        In that case we should not open a second connection and re-register the
        same DEVICEID, because Companion can reject or ignore that duplicate
        observer and the short-lived verification will time out.
        """
        data = self.hass.data.get(DOMAIN, {}).get(self._get_entry().entry_id, {})
        if not isinstance(data, Mapping):
            return None

        live_states = data.get("live_states") or {}
        if not isinstance(live_states, Mapping):
            return None

        for location, raw_state in live_states.items():
            if not isinstance(raw_state, Mapping):
                continue
            try:
                state_page = raw_state.get("page")
                if state_page is None:
                    state_page = int(str(location).split("/", 1)[0])
                if int(state_page) != int(page_number):
                    continue

                state_backend = str(raw_state.get("source") or backend)
                if state_backend != backend:
                    continue

                state = LiveButtonState(
                    source=state_backend,
                    location=str(raw_state.get("location") or location),
                    page=int(state_page),
                    row=raw_state.get("row"),
                    column=raw_state.get("column"),
                    text=str(raw_state.get("text") or ""),
                    color=raw_state.get("color"),
                    text_color=raw_state.get("text_color"),
                    font_size=raw_state.get("font_size"),
                    pressed=raw_state.get("pressed"),
                    raw_command=str(raw_state.get("raw_command") or ""),
                    raw=dict(raw_state.get("raw") or {}),
                )
                if backend == OBSERVER_BACKEND_SURFACE:
                    url = f"http://{host}:{web_ui_port}/surfaces/configured"
                    instructions = f"The already-running Home Assistant observer surface is receiving state for page {page_number}."
                    surface = observer_surface_metadata(self._get_entry().entry_id, page_number)
                    method = "runtime_cache_key_state_page_match"
                else:
                    url = f"http://{host}:{web_ui_port}/settings/protocols"
                    instructions = f"The already-running Subscription API listener is receiving state for page {page_number}."
                    surface = None
                    method = "runtime_cache_sub_state_page_match"

                return LiveStateVerificationResult(
                    success=True,
                    backend=backend,
                    expected_page=page_number,
                    observed_page=page_number,
                    method=method,
                    reason="ok_from_runtime_cache",
                    companion_url=url,
                    instructions=instructions,
                    observer_surface=surface,
                    sample_location=state.location,
                    sample_state=state,
                )
            except Exception:
                continue
        return None

    async def _stop_surface_runtime_for_verification(self) -> Any | None:
        """Temporarily stop the persistent runtime before short-lived Surface verification.

        Re-importing a page can otherwise leave the integration-owned observer
        device registered on the runtime connection while the short-lived
        verification connection tries to register the same DEVICEID. The caller
        restarts the returned runtime after the verification attempt.
        """
        data = self.hass.data.get(DOMAIN, {}).get(self._get_entry().entry_id, {})
        runtime = data.get("live_state_runtime") if isinstance(data, Mapping) else None
        if runtime is not None:
            try:
                await runtime.stop()
                return runtime
            except Exception:  # pragma: no cover - defensive around runtime cleanup
                _LOGGER.debug("Could not stop live-state runtime before verification", exc_info=True)
        return None

    async def _async_delayed_reload(self, entry_id: str) -> None:
        """Reload the parent config entry after a new subentry was saved.

        This starts the POC Surface observer runtime for newly imported pages so
        the HA observer surface does not stay offline after the import completes.
        """
        await asyncio.sleep(1)
        try:
            await self.hass.config_entries.async_reload(entry_id)
        except Exception:  # pragma: no cover - defensive for HA reload edge cases
            _LOGGER.debug("Could not reload Bitfocus Companion Bridge entry after page import", exc_info=True)



    def _sync_fixed_connection_data(self) -> None:
        """Keep derived connection values normalized on the parent entry.

        Companion web UI and HTTP Remote Control share the same port, so
        CONF_WEB_UI_PORT is mirrored from CONF_HTTP_PORT for existing helper
        code and future control APIs. The Satellite API port remains configurable
        in the main setup and is reused by all page imports.
        """
        entry = self._get_entry()
        data = dict(entry.data)
        changed = False
        http_port = int(data.get(CONF_HTTP_PORT, DEFAULT_HTTP_PORT))
        if int(data.get(CONF_WEB_UI_PORT, http_port)) != http_port:
            data[CONF_WEB_UI_PORT] = http_port
            changed = True
        if changed:
            self.hass.config_entries.async_update_entry(entry, data=data)

    def _verification_sample_location(self) -> tuple[int, int]:
        """Return a stable sample location for temporary subscription checks."""
        assert self._preview is not None
        buckets = (
            self._preview.sensor_candidates,
            self._preview.switch_candidates,
            self._preview.button_candidates,
            self._preview.action_only_buttons,
        )
        for bucket in buckets:
            if bucket:
                candidate = bucket[0]
                return candidate.row, candidate.column
        return self._preview.grid_min_row, self._preview.grid_min_column

    @staticmethod
    def _verification_status_text(result: LiveStateVerificationResult) -> str:
        """Return compact verification status for flow descriptions."""
        if result.success:
            return "Live-state verification passed."
        if result.reason == "wrong_page":
            return f"Wrong page: expected page {result.expected_page}, observed page {result.observed_page}."
        if result.reason == "subscriptions_disabled_or_caps_missing":
            return "Button Subscriptions API is disabled or Companion did not report subscription support."
        if result.reason == "key_state_without_location":
            return "KEY-STATE was received, but no LOCATION field was included, so the page could not be verified."
        if result.reason == "no_key_state_received":
            return "No KEY-STATE was received from the Home Assistant observer surface."
        if result.reason == "no_sub_state_received":
            return "No SUB-STATE was received for the requested absolute Companion location."
        if result.reason == "connection_failed_or_timeout":
            return "Connection failed or timed out while checking live state access."
        return f"Live-state verification failed: {result.reason}."

    def _read_uploaded_file(self, uploaded_file_id: str) -> str:
        """Read an uploaded Companion page export from Home Assistant's upload storage."""
        with process_uploaded_file(self.hass, uploaded_file_id) as file_path:
            return file_path.read_text(encoding="utf-8")

    def _ensure_decisions(self) -> None:
        """Create the decisions structure used by later entity platforms."""
        assert self._preview is not None
        if self._decisions is not None:
            return
        self._decisions = {
            "sensors": [],
            "switches": [],
            "switch_state_mappings": [],
            "ignored_homeassistant_actions": [],
            "bulk_mode": False,
            "entity_identity_note": "Future entities are location based, e.g. sensor.companion_p1r1c3 with unique_id based on entry_id + page/row/column + domain.",
            CONF_LIVE_STATE_VERIFICATION: {},
            CONF_PAGE_OBSERVER_BACKEND: self._page_observer_backend or OBSERVER_BACKEND_SURFACE,
            CONF_SATELLITE_PORT: int(self._page_satellite_port or self._get_entry().data.get(CONF_SATELLITE_PORT, DEFAULT_SATELLITE_PORT)),
            CONF_WEB_UI_PORT: int(self._page_web_ui_port or self._get_entry().data.get(CONF_WEB_UI_PORT, self._get_entry().data.get(CONF_HTTP_PORT, DEFAULT_WEB_UI_PORT))),
        }

    def _build_sensor_schema(self, preview: PageImportPreview) -> vol.Schema:
        """Build a sensor-only form."""
        schema: dict[Any, Any] = {}
        if len(preview.sensor_candidates) > MAX_DYNAMIC_CANDIDATE_FIELDS:
            schema[vol.Required(CONF_IMPORT_SENSOR_CANDIDATES, default=True)] = BooleanSelector()
            return vol.Schema(schema)

        for candidate in preview.sensor_candidates:
            schema[vol.Required(self._sensor_key(candidate), default=self._default_sensor_mode(candidate))] = SelectSelector(
                SelectSelectorConfig(
                    options=self._sensor_import_options(),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        return vol.Schema(schema)

    def _build_switch_schema(self, preview: PageImportPreview) -> vol.Schema:
        """Build a switch-only form."""
        schema: dict[Any, Any] = {}
        if len(preview.switch_candidates) > MAX_DYNAMIC_CANDIDATE_FIELDS:
            schema[vol.Required(CONF_SWITCH_IMPORT_MODE, default=SWITCH_IMPORT_BUTTON)] = SelectSelector(
                SelectSelectorConfig(
                    options=self._switch_import_options(),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            return vol.Schema(schema)

        for candidate in preview.switch_candidates:
            schema[vol.Required(self._switch_key(candidate), default=self._default_switch_mode(candidate))] = SelectSelector(
                SelectSelectorConfig(
                    options=self._switch_import_options(),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        return vol.Schema(schema)

    def _parse_sensor_decisions(self, user_input: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Convert sensor review input into stored decisions."""
        assert self._preview is not None
        preview = self._preview
        if len(preview.sensor_candidates) > MAX_DYNAMIC_CANDIDATE_FIELDS:
            import_sensors = bool(user_input.get(CONF_IMPORT_SENSOR_CANDIDATES, True))
            mode = SENSOR_IMPORT_IMPORT if import_sensors else SENSOR_IMPORT_IGNORE
            self._decisions["bulk_mode"] = True
            return [self._candidate_decision(candidate, "sensor", mode) for candidate in preview.sensor_candidates]

        return [
            self._candidate_decision(
                candidate,
                "sensor",
                str(user_input.get(self._sensor_key(candidate), self._default_sensor_mode(candidate))),
            )
            for candidate in preview.sensor_candidates
        ]

    def _parse_switch_decisions(self, user_input: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Convert switch review input into stored decisions."""
        assert self._preview is not None
        preview = self._preview
        if len(preview.switch_candidates) > MAX_DYNAMIC_CANDIDATE_FIELDS:
            mode = str(user_input.get(CONF_SWITCH_IMPORT_MODE, SWITCH_IMPORT_BUTTON))
            self._decisions["bulk_mode"] = True
            return [self._candidate_decision(candidate, "switch", mode) for candidate in preview.switch_candidates]

        return [
            self._candidate_decision(
                candidate,
                "switch",
                str(user_input.get(self._switch_key(candidate), self._default_switch_mode(candidate))),
            )
            for candidate in preview.switch_candidates
        ]

    def _store_ignored_homeassistant_action_decisions(self) -> None:
        """Store ignored Home Assistant action buttons exactly once."""
        assert self._preview is not None
        assert self._decisions is not None
        self._decisions["ignored_homeassistant_actions"] = [
            self._candidate_decision(candidate, "existing_homeassistant_action", SWITCH_IMPORT_IGNORE)
            for candidate in self._preview.ignored_homeassistant_actions
        ]

    async def async_step_switch_state(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Confirm guessed current state for selected switches.

        The technical detection happens in the background: export default and
        feedback styles are compared with a temporary live render read. The user
        only confirms/corrects the guessed ON/OFF meaning.
        """
        assert self._preview is not None
        assert self._decisions is not None

        selected = self._selected_switch_candidates()
        if not selected:
            return self._create_page_subentry()

        if user_input is not None:
            self._decisions["switch_state_mappings"] = self._parse_switch_state_mappings(user_input, selected)
            return self._create_page_subentry()

        self._switch_state_context = await self._prepare_switch_state_context(selected)
        return self.async_show_form(
            step_id="switch_state",
            data_schema=self._build_switch_state_schema(selected),
            description_placeholders={
                "switch_candidates": self._switch_state_candidate_lines(selected),
                "switch_count": str(len(selected)),
            },
        )


    def _reenable_active_imported_sensor_registry_entries(self, page_data: Mapping[str, Any]) -> bool:
        """Re-enable HA registry entries explicitly imported in this flow.

        The method name is kept for in-progress flow compatibility. It now
        applies to sensors, buttons and switches. When a user re-imports a page
        and explicitly chooses to import/keep an entity, that should clear a
        previous Home Assistant disabled-by-user/integration state for that same
        unique_id.
        """
        entity_reg = er.async_get(self.hass)
        changed = False

        for planned in page_data.get("planned_entities") or []:
            if not isinstance(planned, Mapping):
                continue
            domain = str(planned.get("domain") or "")
            if domain not in {"sensor", "button", "switch"}:
                continue
            if planned.get("status", "active") != "active":
                continue
            if planned.get("preserved_from_previous_import") or planned.get("missing_from_latest_import"):
                continue

            unique_id = str(planned.get("unique_id") or "")
            if not unique_id:
                continue

            entity_id = entity_reg.async_get_entity_id(domain, DOMAIN, unique_id)
            if not entity_id:
                continue

            registry_entry = entity_reg.async_get(entity_id)
            if registry_entry is None or registry_entry.disabled_by is None:
                continue

            try:
                entity_reg.async_update_entity(entity_id, disabled_by=None)
                changed = True
                _LOGGER.debug("Re-enabled imported Companion entity %s from entity registry", entity_id)
            except Exception:
                _LOGGER.debug("Could not re-enable imported Companion entity %s", entity_id, exc_info=True)

        return changed


    def _create_page_subentry(self) -> SubentryFlowResult:
        """Persist the page import as a config subentry or update an existing page import."""
        assert self._preview is not None
        assert self._preview.target_page_number is not None
        entry = self._get_entry()
        decisions = dict(self._decisions or {})
        decisions[CONF_PAGE_OBSERVER_BACKEND] = self._page_observer_backend or OBSERVER_BACKEND_SURFACE
        decisions[CONF_SATELLITE_PORT] = int(self._page_satellite_port or entry.data.get(CONF_SATELLITE_PORT, DEFAULT_SATELLITE_PORT))
        decisions[CONF_WEB_UI_PORT] = int(self._page_web_ui_port or entry.data.get(CONF_WEB_UI_PORT, entry.data.get(CONF_HTTP_PORT, DEFAULT_WEB_UI_PORT)))
        if self._live_state_verification is not None:
            decisions[CONF_LIVE_STATE_VERIFICATION] = self._live_state_verification.as_storage_data()
        preview_data = self._preview.as_storage_data()
        page_number = self._preview.target_page_number

        title = f"Companion Page {page_number}"
        if self._preview.page_name:
            title = f"{title} - {self._preview.page_name}"

        existing = find_page_subentry(entry, page_number)
        data = build_page_registry_data(
            entry_id=entry.entry_id,
            page_number=page_number,
            title=title,
            preview_data=preview_data,
            decisions=decisions,
            existing_data=dict(existing.data) if existing else None,
        )
        reenabled_registry_entries = self._reenable_active_imported_sensor_registry_entries(data)

        if existing is not None:
            # Re-import is idempotent on parent config entry + Companion page number.
            # The existing page subentry is updated instead of adding another
            # "Companion Page X" row. Future entity platforms can use the stored
            # import_lifecycle.diff to update/create/disable entities where needed.
            return self.async_update_reload_and_abort(
                entry,
                existing,
                title=title,
                data=data,
                unique_id=page_unique_id(page_number),
                reload_even_if_entry_is_unchanged=reenabled_registry_entries,
            )

        self.hass.async_create_task(self._async_delayed_reload(entry.entry_id))
        return self.async_create_entry(
            title=title,
            unique_id=page_unique_id(page_number),
            data=data,
        )


    def _extract_uploaded_file_info(self, uploaded_file_value: Any) -> tuple[str | None, str | None]:
        """Return uploaded file id and best-effort original filename hint.

        Home Assistant frontends may return either a plain upload id or a small
        mapping containing the id/name. The POC handles both forms defensively.
        """
        if not uploaded_file_value:
            return None, None

        if isinstance(uploaded_file_value, str):
            return uploaded_file_value, None

        if isinstance(uploaded_file_value, Mapping):
            uploaded_file_id = (
                uploaded_file_value.get("file_id")
                or uploaded_file_value.get("id")
                or uploaded_file_value.get("value")
            )
            filename_hint = (
                uploaded_file_value.get("name")
                or uploaded_file_value.get("filename")
            )
            return (str(uploaded_file_id) if uploaded_file_id else None, str(filename_hint) if filename_hint else None)

        return str(uploaded_file_value), None

    @staticmethod
    def _sensor_import_options() -> list[dict[str, str]]:
        """Return labeled sensor import options without relying on dynamic translations."""
        return [
            {"value": SENSOR_IMPORT_IMPORT, "label": "Import"},
            {"value": SENSOR_IMPORT_IGNORE, "label": "Ignore"},
        ]

    @staticmethod
    def _switch_import_options() -> list[dict[str, str]]:
        """Return labeled button/switch import options without relying on dynamic translations."""
        return [
            {"value": SWITCH_IMPORT_IGNORE, "label": "Ignore"},
            {"value": SWITCH_IMPORT_BUTTON, "label": "Button"},
            {"value": SWITCH_IMPORT_SWITCH, "label": "Prepare as switch"},
        ]

    @staticmethod
    def _switch_current_state_options() -> list[dict[str, str]]:
        """Return labeled guessed-state confirmation options."""
        return [
            {"value": "on", "label": "Current state is ON"},
            {"value": "off", "label": "Current state is OFF"},
            {"value": SWITCH_IMPORT_BUTTON, "label": "Button"},
        ]

    def _location_label(self, candidate: ControlCandidate) -> str:
        """Return the absolute Companion location in page/row/column format."""
        if self._preview is not None:
            return self._preview.location_label(candidate)
        return f"?/{candidate.row}/{candidate.column}"

    @staticmethod
    def _candidate_has_homeassistant_association(candidate: ControlCandidate) -> bool:
        """Return true when the candidate is already tied to Home Assistant."""
        return bool(candidate.has_homeassistant_entity_action or candidate.ha_variable_refs)

    @staticmethod
    def _is_transient_feedback_signature(signature: Mapping[str, Any]) -> bool:
        """Return true for feedbacks that are not stable ON/OFF state.

        Companion's internal bank_pushed feedback is a short press highlight. It
        may show up in live render immediately after a press, but it must not be
        learned as the switch ON/OFF state.
        """
        definition_id = str(signature.get("definition_id") or "").lower()
        connection_id = str(signature.get("connection_id") or "").lower()
        return connection_id == "internal" and definition_id in {"bank_pushed", "bank_pressed"}

    @classmethod
    def _stable_feedback_signatures(cls, feedbacks: list[Mapping[str, Any]] | None) -> list[tuple[int, Mapping[str, Any]]]:
        """Return non-transient feedback signatures with their original index."""
        stable: list[tuple[int, Mapping[str, Any]]] = []
        for index, signature in enumerate(feedbacks or []):
            if not isinstance(signature, Mapping):
                continue
            if cls._is_transient_feedback_signature(signature):
                continue
            stable.append((index, signature))
        return stable

    def _existing_switch_mapping(self, candidate: ControlCandidate) -> dict[str, Any]:
        """Return the previous switch mapping for this location during re-import.

        This makes re-imports conservative: if live-state probing cannot produce
        a reliable new signature, the flow can keep the previously confirmed
        ON/OFF mapping instead of silently degrading the switch to a button or to
        an unknown state.
        """
        subentry = self._existing_page_subentry
        if subentry is None and self._preview and self._preview.target_page_number:
            subentry = find_page_subentry(self._get_entry(), self._preview.target_page_number)
        if subentry is None:
            return {}

        location = self._location_label(candidate)
        decisions = subentry.data.get(CONF_IMPORT_DECISIONS) or {}
        for item in decisions.get("switch_state_mappings") or []:
            if not isinstance(item, Mapping):
                continue
            if item.get("location") == location or (item.get("row") == candidate.row and item.get("column") == candidate.column):
                return dict(item)

        for planned in subentry.data.get("planned_entities") or []:
            if not isinstance(planned, Mapping):
                continue
            if planned.get("domain") != "switch":
                continue
            if planned.get("location") != location and not (planned.get("row") == candidate.row and planned.get("column") == candidate.column):
                continue
            mapping = dict(planned.get("switch_mapping") or {})
            for key in (
                "on_signature",
                "off_signature",
                "confirmed_current_state",
                "guessed_current_state",
                "match_fields",
                "state_source",
            ):
                if key in planned and key not in mapping:
                    mapping[key] = planned.get(key)
            return mapping

        return {}

    def _existing_decision_mode(self, bucket: str, candidate: ControlCandidate) -> str | None:
        """Return the previous import decision for this location during re-import."""
        subentry = self._existing_page_subentry
        if subentry is None and self._preview and self._preview.target_page_number:
            subentry = find_page_subentry(self._get_entry(), self._preview.target_page_number)
        if subentry is None:
            return None

        location = self._location_label(candidate)
        decisions = subentry.data.get(CONF_IMPORT_DECISIONS) or {}
        for item in decisions.get(bucket) or []:
            if item.get("location") == location or (item.get("row") == candidate.row and item.get("column") == candidate.column):
                mode = item.get("mode")
                return str(mode) if mode else None

        # Fallback for older POC imports that created button/switch entities but
        # did not yet store a complete per-location switch decision list. This
        # avoids a re-import defaulting an existing button/switch back to Ignore.
        if bucket == "switches":
            for planned in subentry.data.get("planned_entities") or []:
                if not isinstance(planned, Mapping):
                    continue
                if planned.get("status") == "removed":
                    continue
                if planned.get("location") != location and not (planned.get("row") == candidate.row and planned.get("column") == candidate.column):
                    continue
                domain = str(planned.get("domain") or "")
                if domain == "switch":
                    return SWITCH_IMPORT_SWITCH
                if domain == "button":
                    return SWITCH_IMPORT_BUTTON

        return None

    def _default_sensor_mode(self, candidate: ControlCandidate) -> str:
        """Default only Home Assistant-sourced variables to Ignore.

        Sensor discovery is based on the configured Companion button text.
        Existing Home Assistant actions on the same button do not by themselves
        make a non-Home-Assistant variable unsafe to import. During re-import,
        keep the user's previous choice for the same location when available.
        """
        if existing_mode := self._existing_decision_mode("sensors", candidate):
            return existing_mode
        return SENSOR_IMPORT_IGNORE if candidate.ha_variable_refs else SENSOR_IMPORT_IMPORT

    @staticmethod
    def _sensor_default_reason(candidate: ControlCandidate) -> str:
        """Return a concise explanation for the sensor default choice."""
        if candidate.ha_variable_refs:
            return "Default: **Ignore** because the variable value already comes from Home Assistant: " + ", ".join(candidate.ha_variable_refs) + "."
        if candidate.has_homeassistant_entity_action:
            entity_ids = ", ".join(candidate.ignored_entity_ids) or "Home Assistant"
            return "Default: **Import**. This button also calls " + entity_ids + ", but sensor discovery is based on the button text variable, not on button actions."
        return "Default: **Import**."

    def _default_switch_mode(self, candidate: ControlCandidate) -> str:
        """Default Home Assistant-related buttons to Ignore, otherwise Button.

        During re-import, keep the user's previous switch/button/ignore choice
        for the same location when available.
        """
        if existing_mode := self._existing_decision_mode("switches", candidate):
            return existing_mode
        return SWITCH_IMPORT_IGNORE if self._candidate_has_homeassistant_association(candidate) else SWITCH_IMPORT_BUTTON

    @staticmethod
    def _candidate_association_reason(candidate: ControlCandidate) -> str:
        """Human-readable reason why a candidate is already associated with HA."""
        parts: list[str] = []
        if candidate.ha_variable_refs:
            parts.append("Home Assistant variable reference: " + ", ".join(candidate.ha_variable_refs))
        if candidate.ignored_entity_ids:
            parts.append("existing Home Assistant action: " + ", ".join(candidate.ignored_entity_ids))
        return "; ".join(parts)

    def _sensor_candidate_lines(self, candidates: list[ControlCandidate]) -> str:
        """Return Markdown lines for the sensor review page."""
        if not candidates:
            return "-"
        lines: list[str] = []
        for candidate in candidates[:MAX_DYNAMIC_CANDIDATE_FIELDS]:
            name = self._candidate_human_name(candidate)
            reason_text = self._sensor_default_reason(candidate)
            refs = ", ".join(candidate.variable_refs) if candidate.variable_refs else "-"
            lines.append(f"- {self._location_label(candidate)}: {name}. Variables: `{refs}`. {reason_text}")
        remaining = len(candidates) - MAX_DYNAMIC_CANDIDATE_FIELDS
        if remaining > 0:
            lines.append(f"- ... and {remaining} more")
        return "\n".join(lines)

    def _switch_candidate_lines(self, candidates: list[ControlCandidate]) -> str:
        """Return Markdown lines for the button/switch review page."""
        if not candidates:
            return "-"
        lines: list[str] = []
        for candidate in candidates[:MAX_DYNAMIC_CANDIDATE_FIELDS]:
            default = self._default_switch_mode(candidate)
            name = self._candidate_human_name(candidate)
            reason = self._candidate_association_reason(candidate)
            if reason:
                default_text = f"Default: **Ignore** because {reason}."
            elif default == SWITCH_IMPORT_BUTTON:
                default_text = "Default: **Button**."
            elif default == SWITCH_IMPORT_SWITCH:
                default_text = "Default: **Prepare as switch**."
            else:
                default_text = "Default: **Ignore**."
            reasons = ", ".join(candidate.reasons) or "detected"
            kind_hint = "switch-capable" if candidate.feedback_count and candidate.action_count else "button"
            lines.append(f"- {self._location_label(candidate)}: {name}. Type: {kind_hint}. Reasons: {reasons}. {default_text}")
        remaining = len(candidates) - MAX_DYNAMIC_CANDIDATE_FIELDS
        if remaining > 0:
            lines.append(f"- ... and {remaining} more")
        return "\n".join(lines)

    def _ignored_homeassistant_action_lines(self, candidates: list[ControlCandidate]) -> str:
        """Return Markdown lines for the ignored HA-action summary page."""
        if not candidates:
            return "-"
        lines: list[str] = []
        for candidate in candidates:
            name = self._candidate_human_name(candidate)
            entity_ids = ", ".join(candidate.ignored_entity_ids) or "Home Assistant entity"
            lines.append(
                f"- {self._location_label(candidate)}: {name}. Ignored because this Companion control already calls {entity_ids}."
            )
        return "\n".join(lines)

    def _selected_switch_candidates(self) -> list[ControlCandidate]:
        """Return switch candidates the user chose to prepare as future switches."""
        assert self._preview is not None
        if not self._decisions:
            return []

        selected_locations = {
            (item.get("row"), item.get("column"))
            for item in self._decisions.get("switches", [])
            if item.get("mode") == SWITCH_IMPORT_SWITCH
        }
        return [
            candidate
            for candidate in self._preview.switch_candidates
            if (candidate.row, candidate.column) in selected_locations
        ]

    async def _prepare_switch_state_context(self, selected: list[ControlCandidate]) -> dict[str, dict[str, Any]]:
        """Read live switch states and build guessed-state context."""
        assert self._preview is not None
        assert self._preview.target_page_number is not None
        entry = self._get_entry()
        backend = str(self._page_observer_backend or entry.options.get(CONF_OBSERVER_BACKEND, DEFAULT_OBSERVER_BACKEND))
        live_states = await async_read_live_states_for_locations(
            entry_id=entry.entry_id,
            host=str(entry.data[CONF_HOST]),
            satellite_port=int(self._page_satellite_port or entry.data.get(CONF_SATELLITE_PORT, DEFAULT_SATELLITE_PORT)),
            backend=backend,
            page_number=self._preview.target_page_number,
            rows=self._preview.rows,
            columns=self._preview.columns,
            locations=[(candidate.row, candidate.column) for candidate in selected],
        )

        context: dict[str, dict[str, Any]] = {}
        for candidate in selected:
            location = self._location_label(candidate)
            live_state = live_states.get(location)
            previous_mapping = self._existing_switch_mapping(candidate)
            guess_data = self._guess_current_switch_state(candidate, live_state, previous_mapping)
            context[location] = {
                "location": location,
                "live_state": live_state.as_storage_data() if live_state else None,
                "guess": guess_data["guess"],
                "matched_signature": guess_data["matched_signature"],
                "confidence": guess_data["confidence"],
                "default_signature": dict(candidate.default_signature),
                "feedback_signatures": list(candidate.feedback_signatures),
                "previous_mapping": previous_mapping,
            }
        return context

    def _guess_current_switch_state(
        self,
        candidate: ControlCandidate,
        live_state: LiveButtonState | None,
        previous_mapping: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return guessed ON/OFF based on export signatures + live render.

        If the live render does not match reliably during a re-import, keep using
        the previous confirmed state as the educated guess. That is much safer
        than silently converting a previously working switch to Button.
        """
        previous_mapping = previous_mapping or {}
        previous_state = str(previous_mapping.get("confirmed_current_state") or previous_mapping.get("guessed_current_state") or "").lower()
        if previous_state not in {"on", "off"}:
            previous_state = ""

        if live_state is None:
            if previous_state:
                return {"guess": previous_state, "matched_signature": "previous_mapping_no_live_state", "confidence": 0}
            return {"guess": SWITCH_IMPORT_BUTTON, "matched_signature": "none", "confidence": 0}

        default_score = self._signature_match_score(candidate.default_signature, live_state)
        stable_feedbacks = self._stable_feedback_signatures(list(candidate.feedback_signatures))
        feedback_scores = [
            (index, self._signature_match_score(signature, live_state))
            for index, signature in stable_feedbacks
        ]
        best_feedback_index: int | None = None
        best_feedback_score = 0
        if feedback_scores:
            best_feedback_index, best_feedback_score = max(feedback_scores, key=lambda item: item[1])

        # Feedback/active styles are the strongest signal for ON, but transient
        # internal press highlights such as bank_pushed have been filtered out.
        if best_feedback_score >= 2 and best_feedback_score >= default_score:
            return {
                "guess": "on",
                "matched_signature": f"feedback[{best_feedback_index}]",
                "confidence": best_feedback_score,
            }
        if default_score >= 2:
            return {"guess": "off", "matched_signature": "default", "confidence": default_score}
        if previous_state:
            return {
                "guess": previous_state,
                "matched_signature": "previous_mapping_no_reliable_live_match",
                "confidence": max(default_score, best_feedback_score),
            }
        return {"guess": SWITCH_IMPORT_BUTTON, "matched_signature": "none", "confidence": max(default_score, best_feedback_score)}

    @staticmethod
    def _signature_match_score(signature: Mapping[str, Any] | None, live_state: LiveButtonState) -> int:
        """Score how well an export signature matches a live render."""
        if not signature:
            return 0
        score = 0
        if signature.get("background") and live_state.color and str(signature.get("background")).lower() == live_state.color.lower():
            score += 2
        if signature.get("text_color") and live_state.text_color and str(signature.get("text_color")).lower() == live_state.text_color.lower():
            score += 1
        sig_text = str(signature.get("text") or "").strip()
        if sig_text:
            if "$" in sig_text:
                if live_state.text.strip():
                    score += 1
            elif live_state.text.strip() == sig_text:
                score += 2
        return score

    def _switch_state_candidate_lines(self, selected: list[ControlCandidate]) -> str:
        """Return Markdown lines with live render + guessed state for switches."""
        if not selected:
            return "-"
        context = self._switch_state_context or {}
        lines: list[str] = []
        for candidate in selected[:MAX_DYNAMIC_CANDIDATE_FIELDS]:
            location = self._location_label(candidate)
            data = context.get(location, {})
            live = data.get("live_state") or {}
            guess = data.get("guess") or SWITCH_IMPORT_BUTTON
            guess_text = "ON" if guess == "on" else "OFF" if guess == "off" else "not reliable; Button by default"
            matched = data.get("matched_signature") or "none"
            text = str(live.get("text") or "<no live text>").replace("\n", "\\n")
            background = live.get("color") or "-"
            text_color = live.get("text_color") or "-"
            lines.append(
                f"- {location}: {self._candidate_human_name(candidate)}. "
                f"Live render: text `{text}`, background `{background}`, text color `{text_color}`. "
                f"Matched export signature: `{matched}`. Suggested current state: **{guess_text}**."
            )
        remaining = len(selected) - MAX_DYNAMIC_CANDIDATE_FIELDS
        if remaining > 0:
            lines.append(f"- ... and {remaining} more")
        return "\n".join(lines)

    @staticmethod
    def _signature_has_payload(signature: Mapping[str, Any] | None) -> bool:
        """Return true when a stored signature contains enough matching data."""
        if not signature:
            return False
        for key in ("background", "color", "text_color", "text"):
            value = signature.get(key)
            if value not in (None, ""):
                return True
        return False

    @classmethod
    def _mapping_has_usable_on_off_signatures(cls, mapping: Mapping[str, Any]) -> bool:
        """Return true when both ON and OFF signatures have usable data."""
        return cls._signature_has_payload(mapping.get("on_signature")) and cls._signature_has_payload(mapping.get("off_signature"))

    @classmethod
    def _confirmed_on_off_signatures(
        cls,
        confirmed_state: str,
        matched_signature: str | None,
        default_signature: Mapping[str, Any] | None,
        feedback_signatures: list[Mapping[str, Any]] | None,
        live_signature: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Derive ON/OFF signatures from the confirmed current state.

        The user confirms the current semantic state. The integration uses the
        export signature that matched the live render as the confirmed state's
        signature. For multi-step buttons, the counterpart should usually be the
        other stable feedback signature, not the default style. Transient press
        highlights such as internal bank_pushed are ignored.
        """
        if confirmed_state not in {"on", "off"}:
            return {}

        default_sig = dict(default_signature or {})
        feedbacks = [dict(item) for item in (feedback_signatures or [])]
        stable_feedbacks = cls._stable_feedback_signatures(feedbacks)
        live_sig = dict(live_signature or {})
        matched = matched_signature or "none"

        current_sig: dict[str, Any]
        counterpart_sig: dict[str, Any] = {}
        if matched == "default" and default_sig:
            current_sig = default_sig
            counterpart_sig = dict(stable_feedbacks[0][1]) if stable_feedbacks else {}
        elif matched.startswith("feedback[") and feedbacks:
            try:
                idx = int(matched.split("[", 1)[1].split("]", 1)[0])
            except Exception:
                idx = -1
            current_sig = feedbacks[idx] if 0 <= idx < len(feedbacks) else {}
            # Prefer another stable feedback as counterpart for two-step buttons.
            # If no stable counterpart exists, fall back to the default style.
            other_stable = [dict(sig) for sig_index, sig in stable_feedbacks if sig_index != idx]
            counterpart_sig = other_stable[0] if other_stable else default_sig
        else:
            current_sig = {
                "source": "live_confirmation",
                "text": live_sig.get("text") or "",
                "background": live_sig.get("color"),
                "text_color": live_sig.get("text_color"),
                "font_size": live_sig.get("font_size"),
            }
            counterpart_sig = dict(stable_feedbacks[0][1]) if stable_feedbacks else default_sig

        if confirmed_state == "on":
            return {"on_signature": current_sig, "off_signature": counterpart_sig}
        return {"on_signature": counterpart_sig, "off_signature": current_sig}

    def _build_switch_state_schema(self, selected: list[ControlCandidate]) -> vol.Schema:
        """Build dynamic guessed-state confirmation fields."""
        schema: dict[Any, Any] = {}
        context = self._switch_state_context or {}

        if len(selected) > MAX_DYNAMIC_CANDIDATE_FIELDS:
            schema[vol.Required(CONF_SWITCH_CURRENT_STATE, default=SWITCH_IMPORT_BUTTON)] = SelectSelector(
                SelectSelectorConfig(
                    options=self._switch_current_state_options(),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            return vol.Schema(schema)

        for candidate in selected:
            location = self._location_label(candidate)
            default = str(context.get(location, {}).get("guess") or SWITCH_IMPORT_BUTTON)
            schema[vol.Required(self._switch_current_state_key(candidate), default=default)] = SelectSelector(
                SelectSelectorConfig(
                    options=self._switch_current_state_options(),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )

        return vol.Schema(schema)

    def _parse_switch_state_mappings(
        self,
        user_input: Mapping[str, Any],
        selected: list[ControlCandidate],
    ) -> list[dict[str, Any]]:
        """Convert guessed-state confirmation into stored switch mapping data."""
        context = self._switch_state_context or {}

        def selected_value(candidate: ControlCandidate) -> str:
            if len(selected) > MAX_DYNAMIC_CANDIDATE_FIELDS:
                return str(user_input.get(CONF_SWITCH_CURRENT_STATE, SWITCH_IMPORT_BUTTON))
            return str(user_input.get(self._switch_current_state_key(candidate), SWITCH_IMPORT_BUTTON))

        mappings: list[dict[str, Any]] = []
        keep_as_button_locations: set[tuple[int, int]] = set()

        for candidate in selected:
            state = selected_value(candidate)
            location = self._location_label(candidate)
            data = context.get(location, {})
            if state == SWITCH_IMPORT_BUTTON:
                keep_as_button_locations.add((candidate.row, candidate.column))
            default_signature = data.get("default_signature") or dict(candidate.default_signature)
            feedback_signatures = data.get("feedback_signatures") or list(candidate.feedback_signatures)
            live_signature = data.get("live_state")
            signature_mapping = self._confirmed_on_off_signatures(
                state,
                str(data.get("matched_signature") or "none"),
                default_signature,
                feedback_signatures,
                live_signature,
            )
            previous_mapping = dict(data.get("previous_mapping") or {})
            previous_mapping_reused = False
            if state in {"on", "off"} and previous_mapping and not self._mapping_has_usable_on_off_signatures(signature_mapping):
                previous_signatures = {
                    "on_signature": previous_mapping.get("on_signature") or {},
                    "off_signature": previous_mapping.get("off_signature") or {},
                }
                if self._mapping_has_usable_on_off_signatures(previous_signatures):
                    signature_mapping = previous_signatures
                    previous_mapping_reused = True
            mapping = {
                "row": candidate.row,
                "column": candidate.column,
                "location": location,
                "human_name": self._candidate_human_name(candidate),
                "confirmed_current_state": state,
                "guessed_current_state": data.get("guess"),
                "matched_export_signature": data.get("matched_signature"),
                "confidence": data.get("confidence"),
                "previous_mapping_reused": previous_mapping_reused,
                "mapping_mode": "export_plus_live_confirmation",
                "state_source": "render_signature",
                "match_fields": previous_mapping.get("match_fields") or ["text", "background", "text_color"],
                "live_signature_at_confirmation": live_signature,
                "export_default_signature": default_signature,
                "export_feedback_signatures": feedback_signatures,
                "text": candidate.text,
                "reasons": list(candidate.reasons),
            }
            mapping.update(signature_mapping)
            mappings.append(mapping)

        # If the user corrects a prepared switch to "Button", update the stored
        # import decision before planned_entities are derived.
        if keep_as_button_locations and self._decisions:
            for decision in self._decisions.get("switches") or []:
                if (decision.get("row"), decision.get("column")) in keep_as_button_locations:
                    decision["mode"] = SWITCH_IMPORT_BUTTON
                    if "future_entity" in decision:
                        decision.pop("future_entity", None)

        return mappings

    def _candidate_decision(self, candidate: ControlCandidate, kind: str, mode: str) -> dict[str, Any]:
        """Return JSON-serializable decision data for one candidate."""
        entity_domain: str | None = None
        if kind == "sensor" and mode == SENSOR_IMPORT_IMPORT:
            entity_domain = "sensor"
        elif kind == "switch" and mode == SWITCH_IMPORT_SWITCH:
            entity_domain = "switch"
        elif kind == "switch" and mode == SWITCH_IMPORT_BUTTON:
            entity_domain = "button"

        decision = {
            "kind": kind,
            "row": candidate.row,
            "column": candidate.column,
            "location": self._location_label(candidate),
            "mode": mode,
            "text": candidate.text,
            "variable_refs": list(candidate.variable_refs),
            "reasons": list(candidate.reasons),
            "homeassistant_entity_ids": candidate.ignored_entity_ids,
            "homeassistant_domains": candidate.ignored_domains,
            "homeassistant_variable_refs": list(candidate.ha_variable_refs),
            "has_homeassistant_association": self._candidate_has_homeassistant_association(candidate),
        }

        if entity_domain is not None and self._preview and self._preview.target_page_number:
            entry = self._get_entry()
            decision["future_entity"] = future_entity_metadata(
                entry_id=entry.entry_id,
                domain=entity_domain,
                page=self._preview.target_page_number,
                row=candidate.row,
                column=candidate.column,
                source=f"{kind}_decision",
                human_name=self._candidate_human_name(candidate),
            )

        return decision

    @staticmethod
    def _switch_mapping_decision(
        candidate: ControlCandidate,
        mapping_mode: str,
        initial_state: str,
    ) -> dict[str, Any]:
        """Return JSON-serializable future switch mapping data for one candidate."""
        return {
            "row": candidate.row,
            "column": candidate.column,
            "mapping_mode": mapping_mode,
            "initial_state_hint": initial_state,
            "text": candidate.text,
            "reasons": list(candidate.reasons),
        }

    def _candidate_human_name(self, candidate: ControlCandidate) -> str:
        text = candidate.text.replace("\r", " ").replace("\n", " ").strip()
        return text or f"button {self._location_label(candidate)}"

    def _candidate_label(self, candidate: ControlCandidate, prefix: str) -> str:
        text = candidate.text.replace("\r", " ").replace("\n", " ").strip() or "<no text>"
        if len(text) > 70:
            text = text[:67] + "..."
        return f"{prefix} {self._location_label(candidate)} — {text}"

    def _sensor_key(self, candidate: ControlCandidate) -> str:
        return self._candidate_label(candidate, "Variable-text sensor")

    def _switch_key(self, candidate: ControlCandidate) -> str:
        return self._candidate_label(candidate, "Switch-like button")

    def _switch_current_state_key(self, candidate: ControlCandidate) -> str:
        return self._candidate_label(candidate, "Current real-world state for")

    def _switch_mapping_key(self, candidate: ControlCandidate) -> str:
        return self._candidate_label(candidate, "Switch state mapping")

    def _switch_initial_key(self, candidate: ControlCandidate) -> str:
        return self._candidate_label(candidate, "Initial state hint")

    def _ignored_homeassistant_action_key(self, candidate: ControlCandidate) -> str:
        entity_ids = ", ".join(candidate.ignored_entity_ids) or ", ".join(candidate.ignored_domains) or "Home Assistant"
        if len(entity_ids) > 58:
            entity_ids = entity_ids[:55] + "..."
        return self._candidate_label(candidate, f"Ignore: existing Home Assistant action → {entity_ids}")
