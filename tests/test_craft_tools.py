"""Recognition tests — does Lumeri (the v3 agent loop) SEE and RECOGNISE the six
creative point libraries as callable tools?

A tool is "recognised" when: it is in the function-calling schema the model
receives (``TOOL_SCHEMAS`` / ``TOOL_NAMES``), the dispatcher routes its name to
the REAL handler (not an auto-stub), the host budget/plan-mode layers know it,
and calling it through the shared ``DISPATCHER`` returns real, deterministic
output. This mirrors ``test_vector_motion_tool``'s fixture pattern.
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest

from gemia.tools import DISPATCHER
from gemia.tools import layer as layer_module
from gemia.tools._context import AssetRegistry, ToolContext
from gemia.tools._schema import TOOL_NAMES, TOOL_SCHEMAS

CRAFT_TOOLS = ["grade", "kinetic_type", "edit_grammar", "camera", "compose", "rhythm_edit"]


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        session_id=f"test_craft_{uuid.uuid4().hex[:8]}",
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _: None,
    )


def _call(tool: str, args: dict, ctx: ToolContext) -> dict:
    return asyncio.run(DISPATCHER[tool](args, ctx))


def _brief_for(tool: str) -> dict:
    return {
        "grade": {"look": "teal_orange", "feeling": ["cinematic"], "seed": 7},
        "kinetic_type": {"text": "LUMERI", "layout": "title_card", "style": "title_hero",
                         "duration": 4, "canvas": {"width": 1920, "height": 1080}, "seed": 7},
        "edit_grammar": {"clips": [{"id": f"c{i}", "duration": 3.0, "has_action": i % 2 == 0}
                                   for i in range(4)], "style": "documentary", "seed": 7},
        "camera": {"move": "push_in", "subject": {"x": 0.5, "y": 0.4}, "style": "cinematic",
                   "duration": 5, "canvas": {"width": 1920, "height": 1080}, "seed": 7},
        "compose": {"subjects": [{"bbox": [0.3, 0.3, 0.2, 0.4], "facing": "right"}],
                    "canvas": {"width": 1920, "height": 1080}, "framing": "thirds", "seed": 7},
        "rhythm_edit": {"bpm": 128, "sections": [{"name": "drop", "bars": 8}],
                        "style": "build_drop", "seed": 7},
    }[tool]


# ── the model SEES them ─────────────────────────────────────────────────────

def test_all_six_are_in_the_function_calling_schema():
    for tool in CRAFT_TOOLS:
        assert tool in TOOL_NAMES, f"{tool} missing from TOOL_NAMES (model never sees it)"


def test_each_schema_is_a_single_op_tool_with_catalog():
    by_name = {t["function"]["name"]: t for t in TOOL_SCHEMAS}
    for tool in CRAFT_TOOLS:
        params = by_name[tool]["function"]["parameters"]
        op = params["properties"]["op"]
        assert set(["create", "adjust", "catalog"]).issubset(set(op["enum"]))
        assert params["required"] == ["op"]


# ── the dispatcher ROUTES them to real handlers (not stubs) ─────────────────

def test_dispatcher_routes_each_to_its_real_handler():
    for tool in CRAFT_TOOLS:
        fn = DISPATCHER.get(tool)
        assert fn is not None, f"{tool} has no dispatcher"
        assert fn.__module__ == f"gemia.tools.{tool}", f"{tool} routed to a stub ({fn.__module__})"


def test_no_schema_name_lacks_a_dispatcher():
    missing = [n for n in TOOL_NAMES if DISPATCHER.get(n) is None]
    assert missing == []


# ── the host layers KNOW them (budget + plan-mode) ──────────────────────────

def test_budget_guard_knows_every_craft_tool():
    from gemia.budget_guard import _TOOL_COSTS, tool_cost_usd
    for tool in CRAFT_TOOLS:
        assert tool in _TOOL_COSTS, f"{tool} missing a budget entry"
        assert tool_cost_usd(tool) == 0.0  # compute-only creative verbs are free


def test_plan_mode_blocks_only_the_doc_mutating_one():
    from gemia.plan_mode import PLAN_BLOCKED_TOOLS
    assert "kinetic_type" in PLAN_BLOCKED_TOOLS  # writes an html layer
    for tool in ["grade", "camera", "compose", "edit_grammar", "rhythm_edit"]:
        assert tool not in PLAN_BLOCKED_TOOLS  # recipe/plan only → safe while planning


# ── the model can CALL them and gets real vocabulary + output ───────────────

@pytest.mark.parametrize("tool", CRAFT_TOOLS)
def test_catalog_op_returns_real_vocabulary(tool, ctx):
    out = _call(tool, {"op": "catalog"}, ctx)
    assert out.get("applied") is True
    cat = out.get("catalog") or {}
    assert isinstance(cat, dict) and cat, f"{tool} catalog empty"
    # every library catalog advertises styles/looks the model composes from
    blob = str(cat).lower()
    assert any(k in cat for k in ("styles", "looks", "layouts", "moves", "framings",
                                  "patterns", "transitions", "vocabulary")) or len(blob) > 40


@pytest.mark.parametrize("tool", CRAFT_TOOLS)
def test_create_op_returns_real_output(tool, ctx):
    out = _call(tool, {"op": "create", "brief": _brief_for(tool)}, ctx)
    assert out.get("applied") is True, f"{tool} create failed: {out}"
    # each library returns its domain artefact
    keys = set(out)
    assert keys & {"recipe", "plan", "svg", "track", "reframe", "score", "cut_plan",
                   "layer_id", "beat_grid", "preview_svg"}, f"{tool} create returned nothing useful: {keys}"


@pytest.mark.parametrize("tool", CRAFT_TOOLS)
def test_create_is_deterministic_through_the_dispatcher(tool, ctx, tmp_path):
    a = _call(tool, {"op": "create", "brief": _brief_for(tool)}, ctx)
    ctx2 = ToolContext(session_id=f"t2_{uuid.uuid4().hex[:8]}", output_dir=tmp_path,
                       registry=AssetRegistry(), emit_progress=lambda _: None)
    b = _call(tool, {"op": "create", "brief": _brief_for(tool)}, ctx2)
    # ignore volatile layer ids for the doc-mutating tool; compare the plan/recipe
    for vol in ("layer_id", "layer_name"):
        a.pop(vol, None); b.pop(vol, None)
    assert a == b, f"{tool} create is non-deterministic through the dispatcher"


def test_unknown_op_is_a_uniform_error_not_a_crash(ctx):
    for tool in CRAFT_TOOLS:
        out = _call(tool, {"op": "banana"}, ctx)
        assert out.get("applied") is False and out.get("error_code")


# ── kinetic_type actually mutates the doc (full layer integration) ──────────

def test_kinetic_type_adds_and_readjusts_a_layer(ctx):
    before = len(layer_module._lumendoc(ctx)["root"]["children"])
    created = _call("kinetic_type", {"op": "create", "brief": _brief_for("kinetic_type")}, ctx)
    assert created["applied"] and created.get("layer_id")
    after = len(layer_module._lumendoc(ctx)["root"]["children"])
    assert after == before + 1, "kinetic_type create did not add a layer"
    # adjust the created layer from feedback and confirm it rebuilds in place
    adjusted = _call("kinetic_type", {"op": "adjust", "layer_id": created["layer_id"],
                                      "feedback": ["more energetic"]}, ctx)
    assert adjusted["applied"] and adjusted["layer_id"] == created["layer_id"]
    assert len(layer_module._lumendoc(ctx)["root"]["children"]) == after  # rebuilt, not duplicated
