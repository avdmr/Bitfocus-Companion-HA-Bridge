"""Future entity identity and page-registry helpers for Bitfocus Companion Bridge.

The current POC does not create Home Assistant entities yet. This module stores
all naming and re-import decisions in one place so the future sensor/button/switch
platforms can reuse exactly the same location-based identity model.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .const import DOMAIN, SUBENTRY_TYPE_PAGE
from .subentries import iter_config_subentries


def companion_location(page: int, row: int, column: int) -> str:
    """Return Companion's absolute location format: page/row/column."""
    return f"{page}/{row}/{column}"


def location_key(page: int, row: int, column: int) -> str:
    """Return the compact stable location key used in entity object ids."""
    return f"p{page}r{row}c{column}"


def page_unique_id(page_number: int) -> str:
    """Return the subentry unique id for a Companion page import.

    Subentry unique IDs are scoped to the parent config entry, so page_1 can
    exist once under each Companion instance.
    """
    return f"page_{page_number}"


def future_entity_object_id(page: int, row: int, column: int) -> str:
    """Return the future HA entity object id without the domain prefix.

    Example: companion_p1r1c3
    """
    return f"companion_{location_key(page, row, column)}"


def suggested_entity_id(domain: str, page: int, row: int, column: int) -> str:
    """Return the future suggested entity_id.

    Example: sensor.companion_p1r1c3
    """
    return f"{domain}.{future_entity_object_id(page, row, column)}"


def future_unique_id(entry_id: str, domain: str, page: int, row: int, column: int) -> str:
    """Return the future stable entity unique_id.

    This must never be based on Companion button text, action labels, feedback
    labels or user-editable names. Only the Companion instance + absolute button
    position + HA domain define identity.
    """
    return f"{DOMAIN}_{entry_id}_{location_key(page, row, column)}_{domain}"


def future_entity_metadata(
    *,
    entry_id: str,
    domain: str,
    page: int,
    row: int,
    column: int,
    source: str,
    human_name: str | None = None,
) -> dict[str, Any]:
    """Return storage metadata for a future HA entity."""
    return {
        "domain": domain,
        "location": companion_location(page, row, column),
        "location_key": location_key(page, row, column),
        "suggested_object_id": future_entity_object_id(page, row, column),
        "suggested_entity_id": suggested_entity_id(domain, page, row, column),
        "unique_id": future_unique_id(entry_id, domain, page, row, column),
        "source": source,
        "page": page,
        "row": row,
        "column": column,
        "status": "active",
        "removal_policy": "soft_on_reimport",
        "human_name": human_name or future_entity_object_id(page, row, column),
    }


def observer_surface_metadata(entry_id: str, page_number: int) -> dict[str, str]:
    """Return the integration-owned observer surface IDs for Surface mode.

    The integration should not monitor or guess existing Companion surfaces. In
    the future Surface backend it should create only these observer surfaces and
    verify that their KEY-STATE locations match the expected page.
    """
    short_entry = entry_id[:8]
    return {
        "device_id": f"ha-bcb-{short_entry}-page-{page_number}-observer",
        "serial": f"homeassistant:{DOMAIN}:{entry_id}:page:{page_number}",
        "expected_page": str(page_number),
    }


def control_fingerprint(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Return the parts of a parsed control that matter for re-import diffs."""
    return {
        "control_type": candidate.get("control_type"),
        "text": candidate.get("text"),
        "action_count": candidate.get("action_count"),
        "feedback_count": candidate.get("feedback_count"),
        "has_png": candidate.get("has_png"),
        "has_visible_style": candidate.get("has_visible_style"),
        "has_local_variables": candidate.get("has_local_variables"),
        "variable_refs": candidate.get("variable_refs") or [],
        "homeassistant_entity_ids": candidate.get("ignored_entity_ids")
        or candidate.get("homeassistant_entity_ids")
        or [],
        "homeassistant_variable_refs": candidate.get("ha_variable_refs")
        or candidate.get("homeassistant_variable_refs")
        or [],
        "reasons": candidate.get("reasons") or [],
    }


def _controls_by_location(preview: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Index preview candidates by absolute Companion location."""
    if not preview:
        return {}

    controls: dict[str, dict[str, Any]] = {}
    buckets = (
        "button_candidates",
        "action_only_buttons",
        "sensor_candidates",
        "switch_candidates",
        "ignored_homeassistant_actions",
        "navigation_controls",
    )
    for bucket in buckets:
        for candidate in preview.get(bucket) or []:
            if not isinstance(candidate, Mapping):
                continue
            location = candidate.get("location")
            if not isinstance(location, str) or not location:
                continue
            existing = controls.setdefault(
                location,
                {
                    "location": location,
                    "buckets": [],
                    "fingerprint": {},
                    "text": candidate.get("text") or "",
                },
            )
            if bucket not in existing["buckets"]:
                existing["buckets"].append(bucket)
            # Merge fingerprints so a control that is both sensor and switch-like is
            # compared as one physical Companion location.
            existing["fingerprint"] |= control_fingerprint(candidate)
    return controls


def preview_diff(
    old_preview: Mapping[str, Any] | None,
    new_preview: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a re-import diff between the previous and new parsed page export."""
    old_controls = _controls_by_location(old_preview)
    new_controls = _controls_by_location(new_preview)

    old_locations = set(old_controls)
    new_locations = set(new_controls)
    unchanged: list[str] = []
    changed: list[str] = []

    for location in sorted(old_locations & new_locations):
        if old_controls[location]["fingerprint"] == new_controls[location]["fingerprint"]:
            unchanged.append(location)
        else:
            changed.append(location)

    return {
        "new_locations": sorted(new_locations - old_locations),
        "changed_locations": changed,
        "unchanged_locations": unchanged,
        "missing_locations": sorted(old_locations - new_locations),
        "summary": {
            "new": len(new_locations - old_locations),
            "changed": len(changed),
            "unchanged": len(unchanged),
            "missing": len(old_locations - new_locations),
        },
    }


def candidate_human_name(candidate: Mapping[str, Any]) -> str:
    """Return a compact human-readable name for stored candidate metadata."""
    text = str(candidate.get("text") or "").replace("\r", " ").replace("\n", " ").strip()
    return text or str(candidate.get("location") or "Companion button")


def _decision_by_location(decisions: Mapping[str, Any], bucket: str) -> dict[str, Mapping[str, Any]]:
    """Return import decisions indexed by absolute Companion location."""
    indexed: dict[str, Mapping[str, Any]] = {}
    for decision in decisions.get(bucket) or []:
        if not isinstance(decision, Mapping):
            continue
        location = decision.get("location")
        if isinstance(location, str) and location:
            indexed[location] = decision
    return indexed


def _switch_mapping_by_location(decisions: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Return confirmed switch render-signature mappings indexed by location."""
    indexed: dict[str, Mapping[str, Any]] = {}
    for mapping in decisions.get("switch_state_mappings") or []:
        if not isinstance(mapping, Mapping):
            continue
        location = mapping.get("location")
        if isinstance(location, str) and location:
            indexed[location] = mapping
    return indexed


def planned_entities_from_import(
    *,
    entry_id: str,
    preview_data: Mapping[str, Any],
    decisions: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return planned entity metadata from the current import decisions.

    The entity model is intentionally location-based. If a switch-like location
    is explicitly prepared as a switch, it replaces the normal button entity for
    that same physical Companion button. Variable-text sensors may still coexist
    with a button or switch on the same location because they expose the rendered
    text as a separate Home Assistant sensor.
    """
    page = int(preview_data.get("target_page_number") or 0)
    entities: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    switch_decisions = _decision_by_location(decisions, "switches")
    switch_mappings = _switch_mapping_by_location(decisions)

    switch_locations = {
        location
        for location, decision in switch_decisions.items()
        if decision.get("mode") == "switch"
    }
    ignored_switch_locations = {
        location
        for location, decision in switch_decisions.items()
        if decision.get("mode") == "ignore"
    }
    reviewed_button_locations = set(switch_decisions)

    def add(
        domain: str,
        row: int,
        column: int,
        source: str,
        human_name: str | None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        location = companion_location(page, row, column)
        key = (domain, location)
        if key in seen:
            return
        seen.add(key)
        metadata = future_entity_metadata(
            entry_id=entry_id,
            domain=domain,
            page=page,
            row=row,
            column=column,
            source=source,
            human_name=human_name,
        )
        if extra:
            metadata.update(dict(extra))
        entities.append(metadata)

    # Button entities from normal/action-only button discovery. In v0.3.1 every
    # functional Companion button is reviewed in the button/switch decision step.
    # This legacy auto-button path therefore only handles older stored imports
    # that do not yet have per-location button/switch decisions.
    for bucket, source in (
        ("button_candidates", "page_import_button_candidate"),
        ("action_only_buttons", "page_import_action_only_button"),
    ):
        for candidate in preview_data.get(bucket) or []:
            if not isinstance(candidate, Mapping):
                continue
            location = str(candidate.get("location") or "")
            if location in reviewed_button_locations or location in switch_locations or location in ignored_switch_locations:
                continue
            add(
                "button",
                int(candidate["row"]),
                int(candidate["column"]),
                source,
                candidate_human_name(candidate),
                {
                    "text_template": candidate.get("text") or "",
                    "action_count": candidate.get("action_count") or 0,
                    "feedback_count": candidate.get("feedback_count") or 0,
                },
            )

    for decision in decisions.get("sensors") or []:
        if not isinstance(decision, Mapping) or decision.get("mode") != "import":
            continue
        add(
            "sensor",
            int(decision["row"]),
            int(decision["column"]),
            "sensor_decision",
            candidate_human_name(decision),
            {
                "text_template": decision.get("text") or "",
                "variable_refs": decision.get("variable_refs") or [],
                "homeassistant_variable_refs": decision.get("homeassistant_variable_refs") or [],
                "homeassistant_entity_ids": decision.get("homeassistant_entity_ids") or [],
            },
        )

    for decision in decisions.get("switches") or []:
        if not isinstance(decision, Mapping):
            continue
        mode = decision.get("mode")
        row = int(decision["row"])
        column = int(decision["column"])
        location = str(decision.get("location") or companion_location(page, row, column))
        if mode == "switch":
            mapping = dict(switch_mappings.get(location) or {})
            add(
                "switch",
                row,
                column,
                "switch_decision",
                candidate_human_name(decision),
                {
                    "text_template": decision.get("text") or "",
                    "switch_mapping": mapping,
                    "state_source": mapping.get("state_source") or "render_signature",
                    "match_fields": mapping.get("match_fields") or ["text", "background", "text_color"],
                    "on_signature": mapping.get("on_signature") or {},
                    "off_signature": mapping.get("off_signature") or {},
                    "confirmed_current_state": mapping.get("confirmed_current_state"),
                    "guessed_current_state": mapping.get("guessed_current_state"),
                },
            )
        elif mode == "button":
            add(
                "button",
                row,
                column,
                "switch_kept_as_button",
                candidate_human_name(decision),
                {
                    "text_template": decision.get("text") or "",
                    "action_count": decision.get("action_count") or 0,
                    "feedback_count": decision.get("feedback_count") or 0,
                },
            )

    return entities

def _ignored_sensor_locations(decisions: Mapping[str, Any]) -> set[str]:
    """Return locations where the sensor candidate was explicitly ignored."""
    locations: set[str] = set()
    for decision in decisions.get("sensors") or []:
        if not isinstance(decision, Mapping) or decision.get("mode") != "ignore":
            continue
        location = decision.get("location")
        if isinstance(location, str) and location:
            locations.add(location)
    return locations


def _button_switch_modes_by_location(decisions: Mapping[str, Any]) -> dict[str, str]:
    """Return reviewed button/switch import mode per Companion location."""
    modes: dict[str, str] = {}
    for decision in decisions.get("switches") or []:
        if not isinstance(decision, Mapping):
            continue
        location = decision.get("location")
        mode = decision.get("mode")
        if isinstance(location, str) and location and isinstance(mode, str):
            modes[location] = mode
    return modes


def merge_planned_entities_for_reimport(
    *,
    entry_id: str,
    preview_data: Mapping[str, Any],
    decisions: Mapping[str, Any],
    existing_data: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Build planned entities while preserving existing entities safely.

    New import decisions produce active entities. During re-import, existing
    sensors/buttons/switches that are not in the new active list are preserved by
    default so a wrong export does not break dashboards/automations. If the user
    explicitly sets the same location to Ignore during this import, the old
    entity is kept in metadata but marked disabled_by_integration.

    Permanent deletion is handled only by the explicit Manage imported entities
    flow.
    """
    planned = planned_entities_from_import(
        entry_id=entry_id,
        preview_data=preview_data,
        decisions=decisions,
    )
    if not existing_data:
        return planned

    seen_unique_ids = {str(item.get("unique_id")) for item in planned if item.get("unique_id")}
    ignored_sensor_locations = _ignored_sensor_locations(decisions)
    button_switch_modes = _button_switch_modes_by_location(decisions)

    for existing in existing_data.get("planned_entities") or []:
        if not isinstance(existing, Mapping):
            continue
        domain = str(existing.get("domain") or "")
        if domain not in {"sensor", "button", "switch"}:
            continue
        unique_id = str(existing.get("unique_id") or "")
        if not unique_id or unique_id in seen_unique_ids:
            continue
        if existing.get("status") == "removed":
            continue

        preserved = dict(existing)
        location = str(preserved.get("location") or "")
        button_switch_mode = button_switch_modes.get(location)

        if domain == "sensor" and location in ignored_sensor_locations:
            preserved["status"] = "disabled_by_integration"
            preserved["removal_reason"] = "user_ignored_sensor_on_reimport"
        elif domain in {"button", "switch"} and button_switch_mode == "ignore":
            preserved["status"] = "disabled_by_integration"
            preserved["removal_reason"] = "user_ignored_button_on_reimport"
        elif domain == "button" and button_switch_mode == "switch":
            preserved["status"] = "disabled_by_integration"
            preserved["removal_reason"] = "replaced_by_switch_on_reimport"
        elif domain == "switch" and button_switch_mode == "button":
            preserved["status"] = "disabled_by_integration"
            preserved["removal_reason"] = "replaced_by_button_on_reimport"
        else:
            preserved.setdefault("status", "active")
            preserved["missing_from_latest_import"] = True
            preserved["preserved_from_previous_import"] = True
        planned.append(preserved)
        seen_unique_ids.add(unique_id)

    return planned


def build_page_registry_data(
    *,
    entry_id: str,
    page_number: int,
    title: str,
    preview_data: Mapping[str, Any],
    decisions: Mapping[str, Any],
    existing_data: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build the full subentry data payload for a page import/re-import."""
    existing_preview = existing_data.get("import_preview") if existing_data else None
    previous_lifecycle = existing_data.get("import_lifecycle") if existing_data else None
    previous_count = 0
    if isinstance(previous_lifecycle, Mapping):
        previous_count = int(previous_lifecycle.get("import_count") or 0)

    mode = "reimport_update" if existing_data else "new_import"
    diff = preview_diff(existing_preview if isinstance(existing_preview, Mapping) else None, preview_data)

    return {
        "page_number": page_number,
        "page_name": preview_data.get("page_name") or "",
        "page_id": preview_data.get("page_id") or "",
        "page_unique_id": page_unique_id(page_number),
        "title": title,
        "import_preview": dict(preview_data),
        "import_decisions": dict(decisions),
        "planned_entities": merge_planned_entities_for_reimport(
            entry_id=entry_id,
            preview_data=preview_data,
            decisions=decisions,
            existing_data=existing_data,
        ),
        "observer_surface": observer_surface_metadata(entry_id, page_number),
        "import_lifecycle": {
            "mode": mode,
            "import_count": previous_count + 1,
            "imported_at": datetime.now(UTC).isoformat(),
            "diff": diff,
            "rules": [
                "Page identity is parent config entry + Companion page number.",
                "Re-import updates the existing page subentry instead of adding a duplicate.",
                "Future entity unique IDs are based on entry_id + page/row/column + domain.",
                "Future entity_id suggestions use companion_p<page>r<row>c<column> without an extra 'page' word.",
                "Surface mode uses integration-owned observer surfaces only.",
            ],
        },
    }


def find_page_subentry(entry: Any, page_number: int) -> Any | None:
    """Return an existing page subentry for a Companion page number."""
    target_unique_id = page_unique_id(page_number)
    for subentry in iter_config_subentries(entry):
        if subentry.subentry_type != SUBENTRY_TYPE_PAGE:
            continue
        if subentry.unique_id == target_unique_id:
            return subentry
        if subentry.data.get("page_number") == page_number:
            return subentry
    return None
