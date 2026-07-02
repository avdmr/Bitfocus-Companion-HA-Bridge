"""Parser and discovery helpers for Companion page exports.

This module intentionally has no Home Assistant imports. The future entity
platforms can reuse it directly when the POC starts creating sensors/buttons.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from typing import Any, Iterable

from .entity_model import companion_location, location_key, suggested_entity_id

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency loaded by manifest
    yaml = None


class CompanionPageParseError(ValueError):
    """Raised when the uploaded/pasted data is not a Companion page export."""


SUPPORTED_HOME_ASSISTANT_DOMAINS = {
    "button",
    "switch",
    "light",
    "input_boolean",
    "scene",
    "script",
}

VARIABLE_TOKEN_RE = re.compile(r"\$\(([^)]+)\)")
PAGE_NUMBER_PATTERNS = [
    r"^pagina[_-]?(\d+)(?:\.|$)",
    r"^page[_-]?(\d+)(?:\.|$)",
    r"(?:^|[_-])pagina[_-]?(\d+)(?:\.|[_-]|$)",
    r"(?:^|[_-])page[_-]?(\d+)(?:\.|[_-]|$)",
]


@dataclass(frozen=True)
class Location:
    """Absolute Companion location."""

    page: int
    row: int
    column: int

    def protocol(self) -> str:
        """Return Companion protocol location string."""
        return f"{self.page}/{self.row}/{self.column}"


@dataclass(frozen=True)
class HomeAssistantActionRef:
    """Reference to an existing Home Assistant entity called by Companion."""

    connection_id: str
    definition_id: str
    entity_id: str
    domain: str


@dataclass
class ControlCandidate:
    """Normalized discovery data for one Companion control."""

    row: int
    column: int
    control_type: str
    text: str = ""
    action_count: int = 0
    feedback_count: int = 0
    has_png: bool = False
    has_visible_style: bool = False
    has_local_variables: bool = False
    variable_refs: list[str] = field(default_factory=list)
    ha_variable_refs: list[str] = field(default_factory=list)
    ha_action_refs: list[HomeAssistantActionRef] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    default_signature: dict[str, Any] = field(default_factory=dict)
    feedback_signatures: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_button(self) -> bool:
        return self.control_type == "button"

    @property
    def has_homeassistant_entity_action(self) -> bool:
        return bool(self.ha_action_refs)

    @property
    def has_homeassistant_variable_reference(self) -> bool:
        return bool(self.ha_variable_refs)

    @property
    def has_homeassistant_association(self) -> bool:
        return self.has_homeassistant_entity_action or self.has_homeassistant_variable_reference

    @property
    def is_navigation(self) -> bool:
        return self.control_type in {"pageup", "pagedown", "pagenum"}

    @property
    def is_functional_or_visible(self) -> bool:
        return bool(
            self.action_count
            or self.feedback_count
            or self.has_local_variables
            or self.has_png
            or self.has_visible_style
            or self.text.strip()
        )

    @property
    def is_empty_placeholder(self) -> bool:
        return not self.is_functional_or_visible and not self.is_navigation

    @property
    def ignored_entity_ids(self) -> list[str]:
        return sorted({ref.entity_id for ref in self.ha_action_refs})

    @property
    def ignored_domains(self) -> list[str]:
        return sorted({ref.domain for ref in self.ha_action_refs})

    def as_dict(self) -> dict[str, Any]:
        """Return JSON-serializable candidate data."""
        data = asdict(self)
        data["ha_action_refs"] = [asdict(ref) for ref in self.ha_action_refs]
        data["ha_variable_refs"] = list(self.ha_variable_refs)
        data["ignored_entity_ids"] = self.ignored_entity_ids
        data["ignored_domains"] = self.ignored_domains
        data["has_homeassistant_association"] = self.has_homeassistant_association
        return data


@dataclass
class PageImportPreview:
    """Parsed Companion page export plus import-preview buckets."""

    filename_hint: str | None
    companion_build: str
    version: str
    page_id: str
    page_name: str
    suggested_page_number: int | None
    target_page_number: int | None
    page_number_source: str | None
    grid_min_row: int
    grid_max_row: int
    grid_min_column: int
    grid_max_column: int
    homeassistant_connection_ids: list[str]
    controls_total: int
    buttons_total: int
    button_candidates: list[ControlCandidate]
    action_only_buttons: list[ControlCandidate]
    sensor_candidates: list[ControlCandidate]
    switch_candidates: list[ControlCandidate]
    ignored_homeassistant_actions: list[ControlCandidate]
    navigation_controls: list[ControlCandidate]
    empty_placeholders: list[ControlCandidate]

    @property
    def rows(self) -> int:
        return self.grid_max_row - self.grid_min_row + 1

    @property
    def columns(self) -> int:
        return self.grid_max_column - self.grid_min_column + 1

    def location_label(self, candidate: ControlCandidate) -> str:
        """Return the absolute Companion location in the user-facing format page/row/column."""
        page = self.target_page_number or self.suggested_page_number
        if page is None:
            return f"?/{candidate.row}/{candidate.column}"
        return companion_location(page, candidate.row, candidate.column)

    def _candidate_storage_data(self, candidate: ControlCandidate) -> dict[str, Any]:
        """Return candidate data including the absolute Companion location."""
        data = candidate.as_dict()
        page = self.target_page_number or self.suggested_page_number
        data["location"] = self.location_label(candidate)
        data["page"] = self.target_page_number
        if page is not None:
            data["location_key"] = location_key(page, candidate.row, candidate.column)
            data["future_entity_suggestions"] = {
                "button": suggested_entity_id("button", page, candidate.row, candidate.column),
                "sensor": suggested_entity_id("sensor", page, candidate.row, candidate.column),
                "switch": suggested_entity_id("switch", page, candidate.row, candidate.column),
            }
        return data

    def with_page_number(self, page_number: int, source: str = "manual override") -> "PageImportPreview":
        """Return this preview with an updated absolute Companion page number."""
        self.target_page_number = page_number
        self.page_number_source = source
        return self

    def as_storage_data(self) -> dict[str, Any]:
        """Return compact JSON-serializable data suitable for subentry storage."""
        return {
            "filename_hint": self.filename_hint,
            "companion_build": self.companion_build,
            "version": self.version,
            "page_id": self.page_id,
            "page_name": self.page_name,
            "suggested_page_number": self.suggested_page_number,
            "target_page_number": self.target_page_number,
            "page_number_source": self.page_number_source,
            "grid": {
                "min_row": self.grid_min_row,
                "max_row": self.grid_max_row,
                "min_column": self.grid_min_column,
                "max_column": self.grid_max_column,
                "rows": self.rows,
                "columns": self.columns,
            },
            "homeassistant_connection_ids": self.homeassistant_connection_ids,
            "counts": self.counts(),
            "button_candidates": [self._candidate_storage_data(candidate) for candidate in self.button_candidates],
            "action_only_buttons": [self._candidate_storage_data(candidate) for candidate in self.action_only_buttons],
            "sensor_candidates": [self._candidate_storage_data(candidate) for candidate in self.sensor_candidates],
            "switch_candidates": [self._candidate_storage_data(candidate) for candidate in self.switch_candidates],
            "ignored_homeassistant_actions": [self._candidate_storage_data(candidate) for candidate in self.ignored_homeassistant_actions],
            "navigation_controls": [self._candidate_storage_data(candidate) for candidate in self.navigation_controls],
        }

    def counts(self) -> dict[str, int]:
        """Return count summary for config-flow placeholders."""
        return {
            "controls_total": self.controls_total,
            "buttons_total": self.buttons_total,
            "button_candidates": len(self.button_candidates),
            "action_only_buttons": len(self.action_only_buttons),
            "sensor_candidates": len(self.sensor_candidates),
            "switch_candidates": len(self.switch_candidates),
            "ignored_homeassistant_actions": len(self.ignored_homeassistant_actions),
            "navigation_controls": len(self.navigation_controls),
            "empty_placeholders": len(self.empty_placeholders),
        }

    def candidate_lines(self, candidates: list[ControlCandidate], limit: int = 12) -> str:
        """Return human-readable candidate lines for a flow description."""
        if not candidates:
            return "-"

        lines: list[str] = []
        for candidate in candidates[:limit]:
            location = self.location_label(candidate)
            text = compact_text(candidate.text) or "<no text>"
            reasons = ", ".join(candidate.reasons) or "detected"
            lines.append(f"- {location}: {text} ({reasons})")

        remaining = len(candidates) - limit
        if remaining > 0:
            lines.append(f"... and {remaining} more")
        return "\n".join(lines)


def compact_text(text: str) -> str:
    """Make button text compact for config-flow descriptions."""
    return (text or "").replace("\r", "\\r").replace("\n", "\\n")


def _int_color_to_hex(value: Any) -> str | None:
    """Convert Companion integer color values to #rrggbb."""
    if value is None:
        return None
    try:
        return f"#{int(value):06x}"
    except (TypeError, ValueError):
        value_s = str(value).strip()
        if not value_s:
            return None
        if value_s.startswith("#"):
            return value_s.lower()
        if re.fullmatch(r"[0-9a-fA-F]{6}", value_s):
            return f"#{value_s.lower()}"
        return value_s


def _style_signature(style: dict[str, Any], *, source: str, fallback: dict[str, Any] | None = None, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a small render signature from a Companion style object.

    Companion feedback styles are often partial overlays. For switch mapping we
    store a resolved signature where missing feedback fields fall back to the
    default button style. This lets the later live-state reader compare the
    rendered state with likely OFF/ON looks from the export.
    """
    fallback = fallback or {}
    style = style if isinstance(style, dict) else {}
    data = {
        "source": source,
        "text": str(style.get("text") if style.get("text") is not None else fallback.get("text") or ""),
        "background": _int_color_to_hex(style.get("bgcolor") if "bgcolor" in style else fallback.get("bgcolor")),
        "text_color": _int_color_to_hex(style.get("color") if "color" in style else fallback.get("color")),
        "font_size": str(style.get("size") if style.get("size") is not None else fallback.get("size") or ""),
        "has_variable_text": bool(extract_variable_refs(str(style.get("text") if style.get("text") is not None else fallback.get("text") or ""))),
    }
    if meta:
        data.update(meta)
    return data


def _feedback_signatures(control: dict[str, Any], default_style: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract possible active/feedback signatures from Companion feedbacks."""
    signatures: list[dict[str, Any]] = []
    for index, feedback in enumerate(control.get("feedbacks") or []):
        if not isinstance(feedback, dict):
            continue
        style = feedback.get("style") or {}
        if not isinstance(style, dict) or not style:
            continue
        signatures.append(
            _style_signature(
                style,
                source="feedback",
                fallback=default_style,
                meta={
                    "feedback_index": index,
                    "feedback_id": str(feedback.get("id") or ""),
                    "definition_id": str(feedback.get("definitionId") or ""),
                    "connection_id": str(feedback.get("connectionId") or ""),
                    "is_inverted": feedback.get("isInverted"),
                },
            )
        )
    return signatures


def page_number_from_filename(filename: str | None) -> int | None:
    """Extract a Companion page number from common export filename patterns."""
    if not filename:
        return None

    name = filename.rsplit("/", 1)[-1].lower()
    for pattern in PAGE_NUMBER_PATTERNS:
        match = re.search(pattern, name)
        if match:
            return int(match.group(1))
    return None


def extract_variable_refs(text: str) -> list[str]:
    """Extract Companion variable tokens from configured button text."""
    refs: list[str] = []
    seen: set[str] = set()
    for match in VARIABLE_TOKEN_RE.findall(text or ""):
        ref = match.strip()
        if ref and ref not in seen:
            refs.append(ref)
            seen.add(ref)
    return refs


def is_homeassistant_variable_ref(ref: str) -> bool:
    """Return true if a Companion variable token points at Home Assistant.

    Companion exports can contain rendered-text templates such as
    $(homeassistant-:entity.climate.living_room.attributes.current_temperature).
    These are useful to detect, but should default to Ignore because the source
    is already a Home Assistant entity.
    """
    value = (ref or "").strip().lower()
    return value.startswith("homeassistant-:entity") or value.startswith("homeassistant:entity")


def extract_homeassistant_variable_refs(variable_refs: list[str]) -> list[str]:
    """Return variable refs that look like Home Assistant entity references."""
    return [ref for ref in variable_refs if is_homeassistant_variable_ref(ref)]


def _load_structured_content(raw: str) -> dict[str, Any]:
    """Load uploaded/pasted Companion export content as JSON or YAML."""
    errors: list[str] = []

    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise CompanionPageParseError("JSON root is not an object")
        return data
    except Exception as exc:
        errors.append(f"JSON: {exc}")

    if yaml is not None:
        try:
            data = yaml.safe_load(raw)
            if not isinstance(data, dict):
                raise CompanionPageParseError("YAML root is not an object")
            return data
        except Exception as exc:
            errors.append(f"YAML: {exc}")
    else:
        errors.append("YAML: PyYAML is not installed")

    raise CompanionPageParseError("Not valid JSON/YAML Companion page export content. " + " | ".join(errors))


def _iter_companion_actions(control: dict[str, Any]) -> Iterable[dict[str, Any]]:
    steps = control.get("steps") or {}
    if not isinstance(steps, dict):
        return

    for step in steps.values():
        if not isinstance(step, dict):
            continue
        action_sets = step.get("action_sets") or {}
        if not isinstance(action_sets, dict):
            continue
        for actions in action_sets.values():
            if not isinstance(actions, list):
                continue
            for action in actions:
                if isinstance(action, dict) and action.get("type") == "action":
                    yield action


def _count_actions(control: dict[str, Any]) -> int:
    return sum(1 for _ in _iter_companion_actions(control))


def _style_has_visible_content(style: dict[str, Any]) -> bool:
    if not isinstance(style, dict):
        return False
    if style.get("text"):
        return True
    if style.get("png64"):
        return True
    bgcolor = style.get("bgcolor")
    color = style.get("color")
    return bgcolor not in (None, 0, "0") or color not in (None, 16777215, "16777215")


def _find_homeassistant_connection_ids(data: dict[str, Any]) -> list[str]:
    instances = data.get("instances") or {}
    if not isinstance(instances, dict):
        return []

    result: list[str] = []
    for connection_id, instance in instances.items():
        if isinstance(instance, dict) and instance.get("moduleId") == "homeassistant-server":
            result.append(str(connection_id))
    return sorted(result)


def _entity_domain(entity_id: str) -> str | None:
    if "." not in entity_id:
        return None
    domain = entity_id.split(".", 1)[0].strip().lower()
    return domain or None


def _normalize_entity_id_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _extract_homeassistant_action_refs(
    control: dict[str, Any],
    homeassistant_connection_ids: set[str],
) -> list[HomeAssistantActionRef]:
    refs: dict[tuple[str, str, str], HomeAssistantActionRef] = {}
    if not homeassistant_connection_ids:
        return []

    for action in _iter_companion_actions(control):
        connection_id = str(action.get("connectionId") or "")
        if connection_id not in homeassistant_connection_ids:
            continue

        options = action.get("options") or {}
        if not isinstance(options, dict):
            continue

        entity_option = options.get("entity_id")
        if not isinstance(entity_option, dict):
            continue
        if entity_option.get("isExpression") is True:
            continue

        for entity_id in _normalize_entity_id_values(entity_option.get("value")):
            entity_id = entity_id.strip()
            domain = _entity_domain(entity_id)
            if domain not in SUPPORTED_HOME_ASSISTANT_DOMAINS:
                continue
            ref = HomeAssistantActionRef(
                connection_id=connection_id,
                definition_id=str(action.get("definitionId") or ""),
                entity_id=entity_id,
                domain=domain,
            )
            refs[(ref.connection_id, ref.definition_id, ref.entity_id)] = ref

    return list(refs.values())


def _candidate_reasons(candidate: ControlCandidate) -> list[str]:
    reasons: list[str] = []
    if candidate.action_count:
        reasons.append("actions")
    if candidate.feedback_count:
        reasons.append("feedbacks")
    if candidate.variable_refs:
        reasons.append("variable text")
    if candidate.ha_variable_refs:
        reasons.append("Home Assistant variable reference")
    if candidate.has_png:
        reasons.append("png")
    if candidate.has_local_variables:
        reasons.append("local variables")
    if candidate.has_visible_style or candidate.text.strip():
        reasons.append("visible style/text")
    if candidate.has_homeassistant_entity_action:
        reasons.append("existing Home Assistant action")
    return reasons


def _is_button_entity_candidate(candidate: ControlCandidate) -> bool:
    """Return whether a Companion button should be reviewed as an entity.

    The review step intentionally shows all functional/visible Companion
    buttons, including buttons that already call Home Assistant entities. Those
    Home Assistant-associated controls default to Ignore in the config flow, but
    the user can still explicitly import the Companion location as a button or
    prepare it as a switch.
    """
    return bool(candidate.is_button and candidate.is_functional_or_visible and not candidate.is_navigation)


def parse_companion_page_export(
    raw: str,
    filename_hint: str | None = None,
    manual_page_number: int | None = None,
) -> PageImportPreview:
    """Parse a Companion page export and build import-preview buckets."""
    data = _load_structured_content(raw)

    if data.get("type") != "page":
        raise CompanionPageParseError(f"Expected type='page', got {data.get('type')!r}")

    page = data.get("page") or {}
    if not isinstance(page, dict):
        raise CompanionPageParseError("Missing page object")

    page_controls = page.get("controls") or {}
    if not isinstance(page_controls, dict):
        raise CompanionPageParseError("Missing page.controls object")

    grid = page.get("gridSize") or {}
    if not isinstance(grid, dict):
        grid = {}

    filename_page_number = page_number_from_filename(filename_hint)
    old_page_number = data.get("oldPageNumber")
    suggested_page_number: int | None = None
    if old_page_number is not None:
        try:
            suggested_page_number = int(old_page_number)
        except (TypeError, ValueError):
            suggested_page_number = None

    page_number_source: str | None = None
    target_page_number: int | None = None
    if manual_page_number is not None:
        target_page_number = manual_page_number
        page_number_source = "manual override"
    elif filename_page_number is not None:
        target_page_number = filename_page_number
        page_number_source = "filename"
    elif suggested_page_number is not None:
        target_page_number = suggested_page_number
        page_number_source = "oldPageNumber"

    min_row = int(grid.get("minRow", 0))
    max_row = int(grid.get("maxRow", 3))
    min_column = int(grid.get("minColumn", 0))
    max_column = int(grid.get("maxColumn", 7))

    ha_connection_ids = _find_homeassistant_connection_ids(data)
    ha_connection_id_set = set(ha_connection_ids)

    candidates: list[ControlCandidate] = []

    for row_key, row_obj in page_controls.items():
        if not isinstance(row_obj, dict):
            continue
        try:
            row = int(row_key)
        except (TypeError, ValueError):
            continue

        for column_key, control in row_obj.items():
            if not isinstance(control, dict):
                continue
            try:
                column = int(column_key)
            except (TypeError, ValueError):
                continue

            style = control.get("style") or {}
            if not isinstance(style, dict):
                style = {}
            text = str(style.get("text") or "")

            candidate = ControlCandidate(
                row=row,
                column=column,
                control_type=str(control.get("type") or ""),
                text=text,
                action_count=_count_actions(control),
                feedback_count=len(control.get("feedbacks") or []),
                has_png=bool(style.get("png64")),
                has_visible_style=_style_has_visible_content(style),
                has_local_variables=bool(control.get("localVariables")),
                variable_refs=extract_variable_refs(text),
                ha_action_refs=_extract_homeassistant_action_refs(control, ha_connection_id_set),
                default_signature=_style_signature(style, source="default"),
                feedback_signatures=_feedback_signatures(control, style),
            )
            candidate.ha_variable_refs = extract_homeassistant_variable_refs(candidate.variable_refs)
            candidate.reasons = _candidate_reasons(candidate)
            candidates.append(candidate)

    buttons = [candidate for candidate in candidates if candidate.is_button]
    ignored_ha = [candidate for candidate in buttons if candidate.has_homeassistant_entity_action]
    # Sensor discovery is intentionally based on the configured button text only.
    # A button with variable text remains a sensor candidate even when it also
    # performs an existing Home Assistant action; the config flow can then
    # default it to Ignore with a clear explanation.
    sensor_candidates = [
        candidate
        for candidate in buttons
        if candidate.variable_refs
    ]
    # Historical name kept for storage/flow compatibility: this bucket now
    # contains every functional Companion button that can become either a
    # Home Assistant button or a prepared switch. Home Assistant-associated
    # controls are included and default to Ignore in the flow, instead of being
    # hidden.
    switch_candidates = [
        candidate
        for candidate in buttons
        if _is_button_entity_candidate(candidate)
    ]
    action_only_buttons = [
        candidate
        for candidate in buttons
        if candidate.action_count > 0
        and not candidate.text.strip()
        and not candidate.has_png
        and not candidate.has_visible_style
    ]
    navigation_controls = [candidate for candidate in candidates if candidate.is_navigation]
    empty_placeholders = [candidate for candidate in candidates if candidate.is_empty_placeholder]

    button_candidates = [
        candidate
        for candidate in buttons
        if _is_button_entity_candidate(candidate)
    ]

    return PageImportPreview(
        filename_hint=filename_hint,
        companion_build=str(data.get("companionBuild") or ""),
        version=str(data.get("version") or ""),
        page_id=str(page.get("id") or ""),
        page_name=str(page.get("name") or ""),
        suggested_page_number=suggested_page_number,
        target_page_number=target_page_number,
        page_number_source=page_number_source,
        grid_min_row=min_row,
        grid_max_row=max_row,
        grid_min_column=min_column,
        grid_max_column=max_column,
        homeassistant_connection_ids=ha_connection_ids,
        controls_total=len(candidates),
        buttons_total=len(buttons),
        button_candidates=button_candidates,
        action_only_buttons=action_only_buttons,
        sensor_candidates=sensor_candidates,
        switch_candidates=switch_candidates,
        ignored_homeassistant_actions=ignored_ha,
        navigation_controls=navigation_controls,
        empty_placeholders=empty_placeholders,
    )
