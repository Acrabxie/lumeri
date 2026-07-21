import json
from pathlib import Path

import server
from gemia.skill_panels import BUILTIN_SKILLS_ROOT, discover_skill_panels
from tests_http_harness import create_raw_request, run_server_handler


ROOT = Path(__file__).resolve().parents[1]


def _write_skill(root: Path, skill_id: str, panel: dict) -> Path:
    skill = root / skill_id
    (skill / "panels").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        f"id: {skill_id}\n"
        "description: Test Skill.\n"
        "panels:\n"
        "  - panels/main.json\n"
        "---\n\n"
        f"# {skill_id}\n",
        encoding="utf-8",
    )
    (skill / "panels" / "main.json").write_text(json.dumps(panel), encoding="utf-8")
    return skill


def _panel() -> dict:
    return {
        "schema_version": 1,
        "id": "controls",
        "title": "Test controls",
        "description": "Uses host controls.",
        "icon": "sliders",
        "intent": "Apply the selected settings to the current project.",
        "submit_label": "Run",
        "default_size": {"width": 42, "height": 60},
        "fields": [
            {
                "id": "style",
                "type": "select",
                "label": "Style",
                "required": True,
                "options": [{"label": "Clean", "value": "clean"}],
                "default": "clean",
            },
            {
                "id": "amount",
                "type": "slider",
                "label": "Amount",
                "min": 0,
                "max": 10,
                "step": 1,
                "default": 4,
            },
            {"id": "protect", "type": "toggle", "label": "Protect", "default": True},
        ],
    }


def _write_timeline_skill(
    root: Path, skill_id: str, manifest: dict, *, grant_permission: bool = True
) -> Path:
    skill = root / skill_id
    (skill / "timeline").mkdir(parents=True)
    permissions = "permissions:\n  - timeline.components\n" if grant_permission else ""
    (skill / "SKILL.md").write_text(
        "---\n"
        f"id: {skill_id}\n"
        "description: Test timeline Skill.\n"
        f"{permissions}"
        "timeline_components:\n"
        "  - timeline/main.json\n"
        "---\n\n"
        f"# {skill_id}\n",
        encoding="utf-8",
    )
    (skill / "timeline" / "main.json").write_text(json.dumps(manifest), encoding="utf-8")
    return skill


def _timeline_manifest() -> dict:
    return {
        "schema_version": 1,
        "id": "timeline-tools",
        "edits": [
            {
                "component": "add-title",
                "label": "Opening title",
                "placement": {"after": "export-draft"},
            }
        ],
        "widgets": [
            {
                "id": "grade-selection",
                "kind": "button",
                "label": "Grade clip",
                "description": "Grade the selected timeline clip.",
                "icon": "droplet",
                "placement": {"after": "add-title"},
                "requires_selection": True,
                "action": {
                    "type": "agent_turn",
                    "intent": "Grade the selected clip and inspect the result.",
                },
            }
        ],
    }


def test_skill_doc_registers_a_normalized_schema_panel(tmp_path: Path) -> None:
    _write_skill(tmp_path, "test-skill", _panel())

    catalog = discover_skill_panels([tmp_path])

    assert catalog.errors == ()
    assert len(catalog.panels) == 1
    panel = catalog.panels[0]
    assert panel["skill_id"] == "test-skill"
    assert panel["module_id"] == "skill-test-skill-controls"
    assert panel["lifecycle"] == "persistent"
    assert len(panel["revision"]) == 16
    assert panel["default_size"] == {"width": 42, "height": 60}
    assert [field["type"] for field in panel["fields"]] == ["select", "slider", "toggle"]


def test_temporary_panel_lifecycle_is_versioned_by_normalized_content(tmp_path: Path) -> None:
    temporary = _panel()
    temporary["lifecycle"] = "temporary"
    _write_skill(tmp_path, "temporary-skill", temporary)

    first = discover_skill_panels([tmp_path]).panels[0]
    second = discover_skill_panels([tmp_path]).panels[0]
    assert first["lifecycle"] == "temporary"
    assert first["revision"] == second["revision"]

    temporary["description"] = "A new debugging version."
    (tmp_path / "temporary-skill" / "panels" / "main.json").write_text(
        json.dumps(temporary), encoding="utf-8"
    )
    changed = discover_skill_panels([tmp_path]).panels[0]
    assert changed["revision"] != first["revision"]


def test_panel_lifecycle_rejects_unknown_values(tmp_path: Path) -> None:
    panel = _panel()
    panel["lifecycle"] = "session"
    _write_skill(tmp_path, "bad-lifecycle", panel)

    catalog = discover_skill_panels([tmp_path])

    assert catalog.panels == ()
    assert "lifecycle must be persistent or temporary" in catalog.errors[0]


def test_skill_withdraws_its_panel_by_removing_the_manifest_reference(tmp_path: Path) -> None:
    skill = _write_skill(tmp_path, "withdrawn-skill", _panel())
    panel_path = skill / "panels" / "main.json"
    skill_doc = skill / "SKILL.md"
    skill_doc.write_text(
        "---\n"
        "id: withdrawn-skill\n"
        "description: Test Skill.\n"
        "---\n\n"
        "# withdrawn-skill\n",
        encoding="utf-8",
    )

    catalog = discover_skill_panels([tmp_path])

    assert panel_path.is_file()
    assert catalog.panels == ()
    assert catalog.errors == ()


def test_invalid_panel_is_isolated_and_cannot_collect_secrets(tmp_path: Path) -> None:
    _write_skill(tmp_path, "good-skill", _panel())
    bad = _panel()
    bad["id"] = "bad"
    bad["fields"] = [{"id": "api-key", "type": "text", "label": "API key"}]
    _write_skill(tmp_path, "bad-skill", bad)

    catalog = discover_skill_panels([tmp_path])

    assert [panel["skill_id"] for panel in catalog.panels] == ["good-skill"]
    assert len(catalog.errors) == 1
    assert "must not collect credentials or secrets" in catalog.errors[0]


def test_panel_schema_rejects_arbitrary_frontend_code(tmp_path: Path) -> None:
    panel = _panel()
    panel["html"] = "<script>alert(1)</script>"
    _write_skill(tmp_path, "unsafe-skill", panel)

    catalog = discover_skill_panels([tmp_path])

    assert catalog.panels == ()
    assert "unknown panel keys" in catalog.errors[0]


def test_builtin_color_grade_skill_withdraws_its_panel_but_keeps_timeline_components() -> None:
    catalog = discover_skill_panels([BUILTIN_SKILLS_ROOT])

    assert catalog.errors == ()
    assert not any(
        panel["module_id"] == "skill-color-grade-grade-controls" for panel in catalog.panels
    )
    assert (BUILTIN_SKILLS_ROOT / "color-grade" / "panels" / "grade-controls.json").is_file()
    manifest = next(
        item for item in catalog.timeline_components if item["skill_id"] == "color-grade"
    )
    assert manifest["permission"] == "timeline.components"
    assert manifest["widgets"][0]["id"] == "grade-selection"


def test_timeline_components_require_permission_and_keep_panels_isolated(tmp_path: Path) -> None:
    _write_timeline_skill(tmp_path, "no-permission", _timeline_manifest(), grant_permission=False)

    catalog = discover_skill_panels([tmp_path])

    assert catalog.timeline_components == ()
    assert len(catalog.errors) == 1
    assert "requires permissions: [timeline.components]" in catalog.errors[0]


def test_timeline_manifest_normalizes_edits_and_schema_only_widgets(tmp_path: Path) -> None:
    _write_timeline_skill(tmp_path, "timeline-skill", _timeline_manifest())

    catalog = discover_skill_panels([tmp_path])

    assert catalog.errors == ()
    manifest = catalog.timeline_components[0]
    assert manifest["edits"][0] == {
        "component": "add-title",
        "label": "Opening title",
        "placement": {"after": "export-draft"},
    }
    assert manifest["widgets"][0]["requires_selection"] is True
    assert manifest["widgets"][0]["action"]["type"] == "agent_turn"


def test_timeline_manifest_rejects_core_edits_and_arbitrary_code(tmp_path: Path) -> None:
    core_edit = _timeline_manifest()
    core_edit["edits"] = [{"component": "delete", "visible": False}]
    _write_timeline_skill(tmp_path, "core-edit", core_edit)
    script_widget = _timeline_manifest()
    script_widget["widgets"][0]["javascript"] = "fetch('https://example.com')"
    _write_timeline_skill(tmp_path, "script-widget", script_widget)

    catalog = discover_skill_panels([tmp_path])

    assert catalog.timeline_components == ()
    assert len(catalog.errors) == 2
    assert any("core components cannot be edited" in error for error in catalog.errors)
    assert any("unknown timeline widget keys" in error for error in catalog.errors)


def test_timeline_host_actions_are_allowlisted(tmp_path: Path) -> None:
    manifest = _timeline_manifest()
    manifest["widgets"][0]["action"] = {"type": "host_action", "name": "delete-project"}
    _write_timeline_skill(tmp_path, "unsafe-action", manifest)

    catalog = discover_skill_panels([tmp_path])

    assert catalog.timeline_components == ()
    assert "host action must be one of" in catalog.errors[0]


def test_http_endpoint_returns_only_normalized_panels() -> None:
    response = run_server_handler(server._Handler, create_raw_request("GET", "/skill-panels"))

    assert response["status"] == 200
    payload = json.loads(response["body"])
    assert payload["schema_version"] == 1
    assert payload["invalid_count"] == 0
    assert not any(
        panel["module_id"] == "skill-color-grade-grade-controls" for panel in payload["panels"]
    )
    assert any(item["skill_id"] == "color-grade" for item in payload["timeline_components"])
    assert "errors" not in payload


def test_v3_registers_skill_panels_inside_the_existing_workspace_shell() -> None:
    source = (ROOT / "static/v3/v3.js").read_text(encoding="utf-8")
    css = (ROOT / "static/v3/v3.css").read_text(encoding="utf-8")

    assert 'fetch("/skill-panels")' in source
    assert "skillPanelSpecs.set(panel.module_id, panel)" in source
    assert "PANEL_MODULES.add(panel.module_id)" in source
    assert "orderedStageTabs().filter((k) => STAGE_VIEWS[k])" in source
    assert 'const signature = visible.join("|")' in source
    assert '`${visible.join("|")}|tasks:${bgActive}`' not in source
    assert "PANEL_MODULES.has(k) && !skillPanelSpecs.has(k)" in source
    assert "module.dataset.workspaceModule !== activeTab" in source
    assert "WorkspaceLayout.clampSize" in source
    assert "renderSkillPanel(body, skillPanelSpecs.get(view))" in source
    assert "submitTurn(buildSkillPanelMessage(panel, collected.displayValues))" in source
    assert 'toggle.setAttribute("role", "switch")' in source
    assert ".skill-panel-form" in css
    assert ".skill-panel-toggle[aria-checked=\"true\"]" in css
    assert ".skill-panel-submit:focus-visible" in css
    assert "timelineComponentSpecs.push(manifest)" in source
    assert "applyTimelineSkillComponents()" in source
    assert "TIMELINE_HOST_ACTIONS" in source
    assert 'document.getElementById(TIMELINE_HOST_ACTIONS[widget.action.name])?.click()' in source
    assert "buildTimelineWidgetMessage(manifest, widget)" in source
    assert ".pt-skill-widget" in source
    assert "updateEditHint();\n    syncTimelineSkillWidgets();" in source
    assert 'const TEMPORARY_PANEL_SEEN_KEY = "lumeri:v3:temporary-panel-seen"' in source
    assert 'panel?.lifecycle === "temporary"' in source
    assert "temporaryPanelSeen.add(temporaryPanelToken(panel))" in source
    assert 'if (panel.lifecycle !== "temporary") continue;' in source
    assert 'skillPanelSpecs.get(k)?.lifecycle !== "temporary"' in source
    assert '"临时 · 关闭后移除"' in source
    assert '"持久 · 可从 + 重开"' in source
