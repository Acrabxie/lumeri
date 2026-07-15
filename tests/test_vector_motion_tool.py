"""Tests for the ``vector_motion`` tool — the vector motion-design engine.

One tool, op discriminator (create / adjust / catalog). ``create`` compiles a
creative brief into an animated-SVG ``html`` layer patched into the session's
lumenframe doc; ``adjust`` folds feedback phrases into the stored brief and
rebuilds deterministically (same seed, same layer id, same tree position);
``catalog`` exposes the creative vocabulary.

Docs are session-cached (ToolContext with project=None), no media, no network,
no rendering — mirrors the fixture pattern in ``test_lumen_time_tools.py``.
"""
from __future__ import annotations

import asyncio
import copy
import json
import uuid
from pathlib import Path

import pytest

from gemia.tools._context import AssetRegistry, ToolContext
from gemia.tools import layer as layer_module
from gemia.tools import vector_motion as vm

from lumenframe import apply_layer_patch
from lumenframe.model import find_layer
from lumenframe.vector.styles import resolve_params


# ── fixtures / helpers ─────────────────────────────────────────────────────


@pytest.fixture
def tmp_session(tmp_path: Path) -> ToolContext:
    """Minimal ToolContext; unique session_id to avoid _DOC_CACHE pollution."""
    return _fresh_session(tmp_path)


def _fresh_session(tmp_path: Path) -> ToolContext:
    session_id = f"test_vector_motion_{uuid.uuid4().hex[:8]}"
    return ToolContext(
        session_id=session_id,
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _: None,
    )


def _logo_brief(**overrides) -> dict:
    brief = {
        "subject": {"kind": "logo_text", "text": "Lumeri"},
        "intent": "reveal",
        "duration": 4.0,
        "seed": 11,
    }
    brief.update(overrides)
    return brief


def _dispatch(args: dict, ctx: ToolContext) -> dict:
    return asyncio.run(vm.dispatch(args, ctx))


def _root_layers(ctx: ToolContext) -> list[dict]:
    return layer_module._lumendoc(ctx)["root"]["children"]


def _doc_sig(ctx: ToolContext) -> str:
    return json.dumps(layer_module._lumendoc(ctx), sort_keys=True, default=str)


def _add_solid(ctx: ToolContext, lid: str = "plate") -> None:
    doc = apply_layer_patch(layer_module._lumendoc(ctx), {
        "version": 1,
        "ops": [{"op": "add_layer", "id": lid, "type": "solid",
                 "color": "#112233", "start": 0.0, "duration": 1.0}],
    })
    layer_module._save_lumendoc(ctx, doc)


def _resolved_playfulness(brief: dict) -> float:
    return resolve_params(
        style=brief.get("style"),
        feelings=list(brief.get("feeling") or []),
        overrides=dict(brief.get("params") or {}),
    ).axes["playfulness"]


# ════════════════════════════════════════════════════════════════════════
# schema / registration
# ════════════════════════════════════════════════════════════════════════


def test_vector_motion_in_tool_names_and_dispatcher():
    from gemia.tools import DISPATCHER
    from gemia.tools._schema import TOOL_NAMES

    assert "vector_motion" in TOOL_NAMES
    assert "vector_motion" in DISPATCHER
    # The dispatcher maps to the real module function, not a stub.
    assert DISPATCHER["vector_motion"] is vm.dispatch


def test_vector_motion_is_plan_mode_blocked():
    from gemia.plan_mode import PLAN_ALLOWED_TOOLS, PLAN_BLOCKED_TOOLS, is_plan_safe

    assert "vector_motion" in PLAN_BLOCKED_TOOLS
    assert "vector_motion" not in PLAN_ALLOWED_TOOLS
    assert is_plan_safe("vector_motion") is False


def test_vector_motion_has_explicit_budget_entry():
    from gemia.budget_guard import BudgetGuard

    guard = BudgetGuard()
    est = guard.estimate("vector_motion")
    assert isinstance(est, tuple) and len(est) == 2
    usd, eta = est
    assert isinstance(usd, float) and isinstance(eta, float)
    # Explicit entry, not the unknown-tool default.
    unknown = guard.estimate("definitely_not_a_registered_tool")
    assert unknown == (0.0, 5.0)
    assert est != unknown
    assert usd == 0.0 and eta == 1.0  # create/adjust only compile SVG + patch


# ════════════════════════════════════════════════════════════════════════
# op: create
# ════════════════════════════════════════════════════════════════════════


def test_create_adds_html_layer_with_brief_and_plan(tmp_session):
    brief = _logo_brief()
    result = _dispatch(
        {"op": "create", "brief": brief, "place": {"start": 1.25, "lane": 3}},
        tmp_session,
    )

    assert result["applied"] is True, result
    layer_id = result["layer_id"]
    assert layer_id

    # Layer persisted in the session's lumenframe doc.
    doc = layer_module._lumendoc(tmp_session)
    layer = find_layer(doc, layer_id)
    assert layer is not None
    assert layer["type"] == "html"
    assert "<svg" in layer["props"]["html"]
    # The stored brief is the original plus the doc canvas the tool injected
    # (so the SVG fills the real frame); every original key is preserved.
    stored = layer["props"]["vector_brief"]
    for k, v in brief.items():
        assert stored[k] == v
    assert stored["canvas"] == {"width": 1920, "height": 1080}
    assert result["svg_bytes"] == len(layer["props"]["html"])

    # Placement respected.
    assert layer["start"] == pytest.approx(1.25)
    assert layer["lane"] == 3
    assert result["start"] == pytest.approx(1.25)
    assert result["duration"] == pytest.approx(4.0)

    # Plan digest is explainable: phases with windows, and a focal node.
    plan = result["plan"]
    assert plan["phases"], plan
    for phase in plan["phases"]:
        assert set(phase) == {"phase", "behavior", "t0", "t1"}
    assert plan["focal"]
    assert plan["seed"] == 11


# ════════════════════════════════════════════════════════════════════════
# op: adjust
# ════════════════════════════════════════════════════════════════════════


def test_adjust_raises_playfulness_and_preserves_layer_identity(tmp_session):
    brief = _logo_brief()
    orig_playfulness = _resolved_playfulness(brief)

    created = _dispatch({"op": "create", "brief": brief}, tmp_session)
    assert created["applied"] is True, created
    layer_id = created["layer_id"]

    # A second layer AFTER the vector layer pins its tree index at 0.
    _add_solid(tmp_session)
    assert [child["id"] for child in _root_layers(tmp_session)].index(layer_id) == 0
    n_layers = len(_root_layers(tmp_session))

    adjusted = _dispatch(
        {"op": "adjust", "layer_id": layer_id, "feedback": ["more playful"]},
        tmp_session,
    )
    assert adjusted["applied"] is True, adjusted
    assert adjusted["layer_id"] == layer_id

    # Same layer id present exactly once; same layer count; same tree index.
    ids = [child["id"] for child in _root_layers(tmp_session)]
    assert ids.count(layer_id) == 1
    assert len(ids) == n_layers
    assert ids.index(layer_id) == 0

    # The stored brief now carries an absolute playfulness override above the
    # original resolved value ("more playful" = +0.2, clamped to 0..1).
    layer = find_layer(layer_module._lumendoc(tmp_session), layer_id)
    new_playfulness = layer["props"]["vector_brief"]["params"]["playfulness"]
    assert new_playfulness > orig_playfulness
    assert new_playfulness == pytest.approx(min(1.0, orig_playfulness + 0.2), abs=1e-3)
    assert adjusted["adjusted_params"]["playfulness"] == new_playfulness


def test_adjust_twice_accumulates_or_clamps(tmp_session):
    created = _dispatch({"op": "create", "brief": _logo_brief()}, tmp_session)
    layer_id = created["layer_id"]

    first = _dispatch(
        {"op": "adjust", "layer_id": layer_id, "feedback": ["more playful"]},
        tmp_session,
    )
    second = _dispatch(
        {"op": "adjust", "layer_id": layer_id, "feedback": ["more playful"]},
        tmp_session,
    )
    assert first["applied"] is True and second["applied"] is True
    p1 = first["adjusted_params"]["playfulness"]
    p2 = second["adjusted_params"]["playfulness"]
    assert p2 > p1 or p2 == 1.0
    assert p2 == pytest.approx(min(1.0, p1 + 0.2), abs=1e-3)


def test_create_then_adjust_is_deterministic_across_sessions(tmp_path):
    htmls = []
    for _ in range(2):
        ctx = _fresh_session(tmp_path)
        created = _dispatch(
            {"op": "create", "brief": copy.deepcopy(_logo_brief(seed=23))}, ctx
        )
        assert created["applied"] is True, created
        adjusted = _dispatch(
            {"op": "adjust", "layer_id": created["layer_id"],
             "feedback": ["more playful"]},
            ctx,
        )
        assert adjusted["applied"] is True, adjusted
        layer = find_layer(layer_module._lumendoc(ctx), created["layer_id"])
        htmls.append(layer["props"]["html"])

    assert "<svg" in htmls[0]
    assert htmls[0] == htmls[1]  # byte-identical: same brief, same seed


# ════════════════════════════════════════════════════════════════════════
# op: catalog
# ════════════════════════════════════════════════════════════════════════


def test_catalog_exposes_the_creative_vocabulary(tmp_session):
    result = _dispatch({"op": "catalog"}, tmp_session)
    assert result["applied"] is True
    cat = result["catalog"]
    assert cat["styles"]
    assert cat["behaviors"]
    assert cat["feedback_vocabulary"]
    for entry in cat["behaviors"]:
        assert entry.get("family"), entry


# ════════════════════════════════════════════════════════════════════════
# error paths (every failure: applied False + code + message, doc untouched)
# ════════════════════════════════════════════════════════════════════════


def _assert_error(result: dict, code: str) -> None:
    assert result["applied"] is False, result
    assert result["error_code"] == code
    assert result["error_message"]


def test_error_paths_leave_the_doc_unchanged(tmp_session):
    created = _dispatch({"op": "create", "brief": _logo_brief()}, tmp_session)
    assert created["applied"] is True, created
    vector_id = created["layer_id"]
    _add_solid(tmp_session, "plate")
    sig = _doc_sig(tmp_session)

    cases = [
        ({"op": "create"}, "E_ARG"),                            # missing brief
        ({"op": "create", "brief": "not a dict"}, "E_ARG"),
        ({"op": "create", "brief": {}}, "E_ARG"),                # no subject
        ({"op": "shred"}, "E_ARG"),                              # unknown op
        ({"op": "adjust", "feedback": ["more playful"]}, "E_ARG"),  # no layer_id
        ({"op": "adjust", "layer_id": "nope",
          "feedback": ["more playful"]}, "E_NOT_FOUND"),
        ({"op": "adjust", "layer_id": "plate",
          "feedback": ["more playful"]}, "E_ARG"),               # non-vector layer
        ({"op": "adjust", "layer_id": vector_id, "feedback": []}, "E_ARG"),
    ]
    for args, code in cases:
        result = _dispatch(args, tmp_session)
        _assert_error(result, code)
        assert _doc_sig(tmp_session) == sig, f"doc mutated by failed call {args}"
