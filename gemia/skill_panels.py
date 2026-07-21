"""Declarative frontend contributions from Lumeri Skill packages.

Skill panels are deliberately schema-only.  A package may describe controls
that Lumeri already knows how to render, but it cannot inject HTML, CSS,
JavaScript, URLs, or direct project mutations.  Submitting a panel creates a
normal Agent turn, so the existing session, budget, plan-mode, and tool gates
remain authoritative.

Timeline components follow the same rule.  A Skill with the explicit
``timeline.components`` permission may adjust a small allowlisted set of host
components and add schema-only action widgets.  Host actions are names, not
code, and the browser dispatches them through Lumeri's existing handlers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from gemia.ai import skill_yaml


BUILTIN_SKILLS_ROOT = Path(__file__).resolve().parent / "ai" / "skills"
_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FIELD_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_ALLOWED_ICONS = frozenset(
    {
        "bookmark",
        "brain",
        "captions",
        "clapperboard",
        "clock",
        "droplet",
        "file",
        "film",
        "folder",
        "grid",
        "image",
        "layers",
        "list-check",
        "music",
        "scissors",
        "sliders",
        "spark",
        "speed",
        "text",
        "transition",
        "wand",
        "waveform",
    }
)
_ALLOWED_FIELD_TYPES = frozenset({"text", "select", "multi_select", "slider", "toggle"})
_RESERVED_SECRET_WORDS = (
    "api-key",
    "api_key",
    "password",
    "secret",
    "token",
    "密码",
    "密钥",
    "令牌",
    "凭据",
)
_PANEL_KEYS = frozenset(
    {
        "schema_version",
        "id",
        "title",
        "description",
        "icon",
        "intent",
        "submit_label",
        "lifecycle",
        "default_size",
        "fields",
    }
)
_COMMON_FIELD_KEYS = frozenset({"id", "type", "label", "description", "required", "default"})
_FIELD_TYPE_KEYS = {
    "text": frozenset({"placeholder", "multiline", "min_length", "max_length"}),
    "select": frozenset({"options"}),
    "multi_select": frozenset({"options", "min", "max"}),
    "slider": frozenset({"min", "max", "step"}),
    "toggle": frozenset(),
}
_TIMELINE_PERMISSION = "timeline.components"
_TIMELINE_MANIFEST_KEYS = frozenset({"schema_version", "id", "edits", "widgets"})
_TIMELINE_EDIT_KEYS = frozenset({"component", "label", "visible", "placement"})
_TIMELINE_WIDGET_KEYS = frozenset(
    {"id", "kind", "label", "description", "icon", "placement", "requires_selection", "action"}
)
_TIMELINE_ACTION_KEYS = {
    "agent_turn": frozenset({"type", "intent"}),
    "host_action": frozenset({"type", "name"}),
}
_EDITABLE_TIMELINE_COMPONENTS = frozenset(
    {"redo", "marker", "snap", "export-1080p", "export-draft", "add-title", "show-layout"}
)
_QUICK_ACTION_COMPONENTS = frozenset({"export-1080p", "export-draft", "add-title", "show-layout"})
_ALLOWED_HOST_ACTIONS = frozenset(
    {"undo", "split-selected", "delete-selected", "add-marker", "toggle-snap", "zoom-in", "zoom-out"}
)


class SkillPanelError(ValueError):
    """A Skill package contains an invalid panel contribution."""


@dataclass(frozen=True)
class SkillPanelCatalog:
    panels: tuple[dict[str, Any], ...]
    timeline_components: tuple[dict[str, Any], ...]
    errors: tuple[str, ...]

    def public_payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "panels": list(self.panels),
            "timeline_components": list(self.timeline_components),
            "invalid_count": len(self.errors),
        }


def user_skill_packs_root() -> Path:
    override = os.environ.get("LUMERI_SKILL_PACKS_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".gemia" / "skill-packs"


def discover_skill_panels(roots: Iterable[Path] | None = None) -> SkillPanelCatalog:
    """Load every valid panel while isolating malformed Skill packages."""
    scan_roots = tuple(roots) if roots is not None else (BUILTIN_SKILLS_ROOT, user_skill_packs_root())
    panels: list[dict[str, Any]] = []
    timeline_components: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_modules: set[str] = set()
    seen_timeline_manifests: set[str] = set()

    for root in scan_roots:
        root = Path(root).expanduser()
        if not root.exists():
            continue
        for skill_doc in sorted(root.glob("*/SKILL.md")):
            if skill_doc.parent.name.startswith(".") or skill_doc.parent.name.startswith("_"):
                continue
            try:
                skill_id, skill_title, permissions, panel_refs, timeline_refs = _read_skill_refs(skill_doc)
            except SkillPanelError as exc:
                errors.append(f"{skill_doc}: {exc}")
                continue
            for ref in panel_refs:
                try:
                    panel_path = _resolve_json_path(skill_doc.parent, ref, "panel")
                    raw = _load_panel_json(panel_path)
                    panel = _normalize_panel(raw, skill_id=skill_id, skill_title=skill_title)
                    module_id = panel["module_id"]
                    if module_id in seen_modules:
                        raise SkillPanelError(f"duplicate module id {module_id!r}")
                    seen_modules.add(module_id)
                    panels.append(panel)
                except SkillPanelError as exc:
                    errors.append(f"{skill_doc.parent / ref}: {exc}")

            if timeline_refs and _TIMELINE_PERMISSION not in permissions:
                errors.append(
                    f"{skill_doc}: timeline_components requires permissions: [{_TIMELINE_PERMISSION}]"
                )
                continue
            for ref in timeline_refs:
                try:
                    manifest_path = _resolve_json_path(skill_doc.parent, ref, "timeline component")
                    raw = _load_panel_json(manifest_path)
                    manifest = _normalize_timeline_manifest(raw, skill_id=skill_id, skill_title=skill_title)
                    manifest_id = f"{skill_id}:{manifest['id']}"
                    if manifest_id in seen_timeline_manifests:
                        raise SkillPanelError(f"duplicate timeline manifest id {manifest_id!r}")
                    seen_timeline_manifests.add(manifest_id)
                    timeline_components.append(manifest)
                except SkillPanelError as exc:
                    errors.append(f"{skill_doc.parent / ref}: {exc}")

    panels.sort(key=lambda panel: (panel["skill_id"], panel["id"]))
    timeline_components.sort(key=lambda item: (item["skill_id"], item["id"]))
    return SkillPanelCatalog(tuple(panels), tuple(timeline_components), tuple(errors))


def _read_skill_refs(path: Path) -> tuple[str, str, set[str], list[str], list[str]]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise SkillPanelError("SKILL.md must start with YAML frontmatter")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise SkillPanelError("SKILL.md frontmatter is not closed")
    try:
        data = skill_yaml.safe_load(parts[1]) or {}
    except Exception as exc:
        raise SkillPanelError(f"invalid SKILL.md frontmatter: {exc}") from exc
    if not isinstance(data, dict):
        raise SkillPanelError("SKILL.md frontmatter must be a mapping")
    skill_id = str(data.get("id") or path.parent.name).strip()
    _require_id(skill_id, "skill id")
    if skill_id != path.parent.name:
        raise SkillPanelError("skill id must match its directory name")
    title = str(data.get("title") or skill_id).strip()
    _require_text(title, "skill title", 80)
    raw_permissions = data.get("permissions") or []
    if not isinstance(raw_permissions, list) or any(not isinstance(item, str) for item in raw_permissions):
        raise SkillPanelError("permissions must be a list of strings")
    permissions = {item.strip() for item in raw_permissions if item.strip()}
    panel_refs = _read_json_refs(data.get("panels"), "panels", 8)
    timeline_refs = _read_json_refs(data.get("timeline_components"), "timeline_components", 4)
    return skill_id, title, permissions, panel_refs, timeline_refs


def _read_json_refs(raw: Any, label: str, limit: int) -> list[str]:
    raw_refs = raw or []
    if not isinstance(raw_refs, list):
        raise SkillPanelError(f"{label} must be a list of relative JSON paths")
    if len(raw_refs) > limit:
        raise SkillPanelError(f"a Skill may contribute at most {limit} {label}")
    refs: list[str] = []
    for raw_ref in raw_refs:
        ref = str(raw_ref or "").strip()
        if not ref:
            raise SkillPanelError(f"{label} paths must be non-empty strings")
        refs.append(ref)
    return refs


def _resolve_json_path(skill_root: Path, ref: str, label: str) -> Path:
    ref_path = Path(ref)
    if ref_path.is_absolute() or ref_path.suffix != ".json" or any(part in {"", ".", ".."} for part in ref_path.parts):
        raise SkillPanelError(f"{label} path must be a relative .json path without traversal")
    if any(part.startswith(".") for part in ref_path.parts):
        raise SkillPanelError(f"hidden {label} files are not allowed")
    root = skill_root.resolve()
    resolved = (skill_root / ref_path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SkillPanelError(f"{label} path escapes the Skill directory") from exc
    if not resolved.is_file():
        raise SkillPanelError(f"{label} file does not exist")
    return resolved


def _load_panel_json(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 64 * 1024:
        raise SkillPanelError("panel file exceeds 64 KiB")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillPanelError(f"invalid UTF-8 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SkillPanelError("panel JSON must be an object")
    return data


def _normalize_panel(data: dict[str, Any], *, skill_id: str, skill_title: str) -> dict[str, Any]:
    unknown = set(data) - _PANEL_KEYS
    if unknown:
        raise SkillPanelError(f"unknown panel keys: {sorted(unknown)}")
    if data.get("schema_version") != 1:
        raise SkillPanelError("schema_version must be 1")
    panel_id = str(data.get("id") or "").strip()
    _require_id(panel_id, "panel id")
    title = _require_text(data.get("title"), "title", 48)
    description = _require_text(data.get("description"), "description", 240, allow_empty=True)
    intent = _require_text(data.get("intent"), "intent", 300)
    submit_label = _require_text(data.get("submit_label") or "交给 Lumeri", "submit_label", 24)
    lifecycle = str(data.get("lifecycle") or "persistent").strip()
    if lifecycle not in {"persistent", "temporary"}:
        raise SkillPanelError("lifecycle must be persistent or temporary")
    icon = str(data.get("icon") or "sliders").strip()
    if icon not in _ALLOWED_ICONS:
        raise SkillPanelError(f"icon must be one of {sorted(_ALLOWED_ICONS)}")
    default_size = data.get("default_size") or {"width": 38, "height": 64}
    if not isinstance(default_size, dict) or set(default_size) != {"width", "height"}:
        raise SkillPanelError("default_size must contain exactly width and height")
    width = _bounded_number(default_size.get("width"), "default_size.width", 16, 100)
    height = _bounded_number(default_size.get("height"), "default_size.height", 18, 100)
    raw_fields = data.get("fields")
    if not isinstance(raw_fields, list) or not 1 <= len(raw_fields) <= 12:
        raise SkillPanelError("fields must contain 1 to 12 field definitions")
    fields: list[dict[str, Any]] = []
    seen_fields: set[str] = set()
    for raw_field in raw_fields:
        field = _normalize_field(raw_field)
        if field["id"] in seen_fields:
            raise SkillPanelError(f"duplicate field id {field['id']!r}")
        seen_fields.add(field["id"])
        fields.append(field)
    panel = {
        "schema_version": 1,
        "skill_id": skill_id,
        "skill_title": skill_title,
        "id": panel_id,
        "module_id": f"skill-{skill_id}-{panel_id}",
        "title": title,
        "description": description,
        "icon": icon,
        "intent": intent,
        "submit_label": submit_label,
        "lifecycle": lifecycle,
        "default_size": {"width": width, "height": height},
        "fields": fields,
    }
    fingerprint = json.dumps(panel, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    panel["revision"] = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
    return panel


def _normalize_field(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SkillPanelError("every field must be an object")
    field_type = str(raw.get("type") or "").strip()
    if field_type not in _ALLOWED_FIELD_TYPES:
        raise SkillPanelError(f"unsupported field type {field_type!r}")
    unknown = set(raw) - _COMMON_FIELD_KEYS - _FIELD_TYPE_KEYS[field_type]
    if unknown:
        raise SkillPanelError(f"unknown keys for {field_type}: {sorted(unknown)}")
    field_id = str(raw.get("id") or "").strip()
    if not _FIELD_ID_RE.fullmatch(field_id):
        raise SkillPanelError(f"invalid field id {field_id!r}")
    label = _require_text(raw.get("label"), f"field {field_id} label", 48)
    if any(word in f"{field_id} {label}".lower() for word in _RESERVED_SECRET_WORDS):
        raise SkillPanelError("Skill panels must not collect credentials or secrets")
    description = _require_text(raw.get("description"), f"field {field_id} description", 160, allow_empty=True)
    field: dict[str, Any] = {
        "id": field_id,
        "type": field_type,
        "label": label,
        "description": description,
        "required": bool(raw.get("required", False)),
    }
    if field_type == "text":
        min_length = int(_bounded_number(raw.get("min_length", 0), "min_length", 0, 2000))
        max_length = int(_bounded_number(raw.get("max_length", 240), "max_length", max(1, min_length), 2000))
        field.update(
            {
                "placeholder": _require_text(raw.get("placeholder"), "placeholder", 120, allow_empty=True),
                "multiline": bool(raw.get("multiline", False)),
                "min_length": min_length,
                "max_length": max_length,
                "default": _require_text(raw.get("default"), "text default", max_length, allow_empty=True),
            }
        )
    elif field_type in {"select", "multi_select"}:
        options = _normalize_options(raw.get("options"))
        field["options"] = options
        allowed_values = {option["value"] for option in options}
        if field_type == "select":
            default = raw.get("default")
            if default is None:
                default = options[0]["value"] if field["required"] else ""
            default = str(default)
            if default and default not in allowed_values:
                raise SkillPanelError(f"default for {field_id} is not an option")
            field["default"] = default
        else:
            minimum = int(_bounded_number(raw.get("min", 0), "multi_select min", 0, len(options)))
            maximum = int(_bounded_number(raw.get("max", len(options)), "multi_select max", minimum, len(options)))
            default = raw.get("default") or []
            if not isinstance(default, list) or any(str(value) not in allowed_values for value in default):
                raise SkillPanelError(f"default for {field_id} must use declared option values")
            if not minimum <= len(default) <= maximum:
                raise SkillPanelError(f"default for {field_id} violates min/max")
            field.update({"min": minimum, "max": maximum, "default": [str(value) for value in default]})
    elif field_type == "slider":
        minimum = _bounded_number(raw.get("min", 0), "slider min", -100000, 100000)
        maximum = _bounded_number(raw.get("max", 100), "slider max", minimum, 100000)
        if maximum <= minimum:
            raise SkillPanelError("slider max must be greater than min")
        step = _bounded_number(raw.get("step", 1), "slider step", 0.000001, max(0.000001, maximum - minimum))
        default = _bounded_number(raw.get("default", minimum), "slider default", minimum, maximum)
        field.update({"min": minimum, "max": maximum, "step": step, "default": default})
    else:
        field["default"] = bool(raw.get("default", False))
    return field


def _normalize_timeline_manifest(
    data: dict[str, Any], *, skill_id: str, skill_title: str
) -> dict[str, Any]:
    unknown = set(data) - _TIMELINE_MANIFEST_KEYS
    if unknown:
        raise SkillPanelError(f"unknown timeline manifest keys: {sorted(unknown)}")
    if data.get("schema_version") != 1:
        raise SkillPanelError("timeline schema_version must be 1")
    manifest_id = str(data.get("id") or "").strip()
    _require_id(manifest_id, "timeline manifest id")

    raw_edits = data.get("edits") or []
    raw_widgets = data.get("widgets") or []
    if not isinstance(raw_edits, list) or len(raw_edits) > 12:
        raise SkillPanelError("edits must be a list with at most 12 items")
    if not isinstance(raw_widgets, list) or len(raw_widgets) > 12:
        raise SkillPanelError("widgets must be a list with at most 12 items")
    if not raw_edits and not raw_widgets:
        raise SkillPanelError("a timeline manifest must contain edits or widgets")

    edits = [_normalize_timeline_edit(item) for item in raw_edits]
    seen_edits: set[str] = set()
    for edit in edits:
        component = edit["component"]
        if component in seen_edits:
            raise SkillPanelError(f"duplicate timeline component edit {component!r}")
        seen_edits.add(component)

    widgets = [_normalize_timeline_widget(item) for item in raw_widgets]
    seen_widgets: set[str] = set()
    for widget in widgets:
        if widget["id"] in seen_widgets:
            raise SkillPanelError(f"duplicate timeline widget id {widget['id']!r}")
        seen_widgets.add(widget["id"])

    return {
        "schema_version": 1,
        "skill_id": skill_id,
        "skill_title": skill_title,
        "id": manifest_id,
        "permission": _TIMELINE_PERMISSION,
        "edits": edits,
        "widgets": widgets,
    }


def _normalize_timeline_edit(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SkillPanelError("every timeline edit must be an object")
    unknown = set(raw) - _TIMELINE_EDIT_KEYS
    if unknown:
        raise SkillPanelError(f"unknown timeline edit keys: {sorted(unknown)}")
    component = str(raw.get("component") or "").strip()
    if component not in _EDITABLE_TIMELINE_COMPONENTS:
        raise SkillPanelError(
            f"component must be one of {sorted(_EDITABLE_TIMELINE_COMPONENTS)}; core components cannot be edited"
        )
    if "label" not in raw and "visible" not in raw and "placement" not in raw:
        raise SkillPanelError("timeline edit must change label, visible, or placement")
    edit: dict[str, Any] = {"component": component}
    if "label" in raw:
        edit["label"] = _require_text(raw.get("label"), "timeline component label", 24)
    if "visible" in raw:
        if not isinstance(raw.get("visible"), bool):
            raise SkillPanelError("timeline component visible must be boolean")
        edit["visible"] = raw["visible"]
    if "placement" in raw:
        if component not in _QUICK_ACTION_COMPONENTS:
            raise SkillPanelError("only quick-action components can be repositioned")
        edit["placement"] = _normalize_timeline_placement(raw.get("placement"))
        if next(iter(edit["placement"].values())) == component:
            raise SkillPanelError("a timeline component cannot be placed relative to itself")
    return edit


def _normalize_timeline_widget(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SkillPanelError("every timeline widget must be an object")
    unknown = set(raw) - _TIMELINE_WIDGET_KEYS
    if unknown:
        raise SkillPanelError(f"unknown timeline widget keys: {sorted(unknown)}")
    widget_id = str(raw.get("id") or "").strip()
    _require_id(widget_id, "timeline widget id")
    if raw.get("kind") != "button":
        raise SkillPanelError("timeline widget kind must be button in schema v1")
    label = _require_text(raw.get("label"), "timeline widget label", 24)
    description = _require_text(
        raw.get("description"), "timeline widget description", 160, allow_empty=True
    )
    icon = str(raw.get("icon") or "spark").strip()
    if icon not in _ALLOWED_ICONS:
        raise SkillPanelError(f"icon must be one of {sorted(_ALLOWED_ICONS)}")
    action = _normalize_timeline_action(raw.get("action"))
    placement = _normalize_timeline_placement(raw.get("placement") or {"after": "show-layout"})
    requires_selection = raw.get("requires_selection", False)
    if not isinstance(requires_selection, bool):
        raise SkillPanelError("timeline widget requires_selection must be boolean")
    return {
        "id": widget_id,
        "kind": "button",
        "label": label,
        "description": description,
        "icon": icon,
        "placement": placement,
        "requires_selection": requires_selection,
        "action": action,
    }


def _normalize_timeline_action(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise SkillPanelError("timeline widget action must be an object")
    action_type = str(raw.get("type") or "").strip()
    allowed_keys = _TIMELINE_ACTION_KEYS.get(action_type)
    if allowed_keys is None:
        raise SkillPanelError("timeline widget action type must be agent_turn or host_action")
    unknown = set(raw) - allowed_keys
    if unknown:
        raise SkillPanelError(f"unknown {action_type} action keys: {sorted(unknown)}")
    if action_type == "agent_turn":
        return {
            "type": action_type,
            "intent": _require_text(raw.get("intent"), "timeline widget intent", 300),
        }
    name = str(raw.get("name") or "").strip()
    if name not in _ALLOWED_HOST_ACTIONS:
        raise SkillPanelError(f"host action must be one of {sorted(_ALLOWED_HOST_ACTIONS)}")
    return {"type": action_type, "name": name}


def _normalize_timeline_placement(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict) or len(raw) != 1 or not set(raw).issubset({"before", "after"}):
        raise SkillPanelError("placement must contain exactly one of before or after")
    direction, target_raw = next(iter(raw.items()))
    target = str(target_raw or "").strip()
    if target not in _QUICK_ACTION_COMPONENTS:
        raise SkillPanelError(f"placement target must be one of {sorted(_QUICK_ACTION_COMPONENTS)}")
    return {direction: target}


def _normalize_options(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= 24:
        raise SkillPanelError("options must contain 1 to 24 items")
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"label", "value"}:
            raise SkillPanelError("each option must contain exactly label and value")
        label = _require_text(item.get("label"), "option label", 48)
        value = _require_text(item.get("value"), "option value", 64)
        if value in seen:
            raise SkillPanelError(f"duplicate option value {value!r}")
        seen.add(value)
        out.append({"label": label, "value": value})
    return out


def _require_id(value: str, label: str) -> None:
    if not _ID_RE.fullmatch(value) or len(value) > 64:
        raise SkillPanelError(f"{label} must be ASCII kebab-case and at most 64 characters")


def _require_text(value: Any, label: str, max_length: int, *, allow_empty: bool = False) -> str:
    text = str(value or "").strip()
    if "\n" in text or "\r" in text:
        raise SkillPanelError(f"{label} must be a single line")
    if not allow_empty and not text:
        raise SkillPanelError(f"{label} is required")
    if len(text) > max_length:
        raise SkillPanelError(f"{label} exceeds {max_length} characters")
    return text


def _bounded_number(value: Any, label: str, minimum: float, maximum: float) -> int | float:
    if isinstance(value, bool):
        raise SkillPanelError(f"{label} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SkillPanelError(f"{label} must be numeric") from exc
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise SkillPanelError(f"{label} must be between {minimum} and {maximum}")
    return int(number) if number.is_integer() else number


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Validate Lumeri Skill frontend contributions")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    catalog = discover_skill_panels()
    payload = catalog.public_payload() | {"errors": list(catalog.errors)}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"valid panels: {len(catalog.panels)}")
        print(f"valid timeline manifests: {len(catalog.timeline_components)}")
        for error in catalog.errors:
            print(f"error: {error}")
    if catalog.errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
