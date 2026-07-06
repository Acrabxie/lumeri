"""Conditional lumenframe injection in agent_loop_v3 — real production path.

Regression background: the loop used to read ``layer._DOC_CACHE`` for the
``{{lumenframe}}`` / ``{{lumenframe_ops}}`` prompt slots, but in v3
``ctx.project`` is always set, so ``_save_lumendoc`` persisted every edit to
``<project_dir>/lumenframe.json`` and the cache was never written — the slots
were permanently empty in real sessions. The old tests masked this by
planting ``_DOC_CACHE`` directly (and by testing a copied implementation on a
``MinimalLoop``). These tests drive a REAL ``AgentLoopV3`` whose document is
saved the way tool dispatchers save it.
"""
from __future__ import annotations

from pathlib import Path
import uuid

import pytest

from gemia.agent_loop_v3 import AgentLoopV3
from gemia.tools import layer as layer_module
from lumenframe import apply_layer_patch, empty_doc


class _StubClient:
    """No turn is driven in these tests; the loop just needs a client object
    so its constructor skips the credential lookup."""


@pytest.fixture
def loop(tmp_path: Path) -> AgentLoopV3:
    """A real AgentLoopV3 (opens a real ProjectHandle under tmp_path)."""
    session_id = f"test_session_{uuid.uuid4().hex[:8]}"
    return AgentLoopV3(
        session_id=session_id,
        output_dir=tmp_path / "outputs",
        gemini_client=_StubClient(),  # type: ignore[arg-type]
        max_visual_inspections=2,
        budget_max_usd=1.0,
        budget_max_seconds=60.0,
    )


def _doc_with_layer() -> dict:
    doc = empty_doc()
    return apply_layer_patch(
        doc,
        {"version": 1, "ops": [{"op": "add_layer", "type": "solid", "name": "bg"}]},
    )


def _save_via_production_path(loop: AgentLoopV3, doc: dict) -> None:
    """Persist exactly like tool dispatchers do (project file, not cache)."""
    layer_module._save_lumendoc(loop._tool_ctx, doc)


def test_no_doc_yet_prompt_is_placeholder_and_ops_minimal(loop: AgentLoopV3) -> None:
    """Fresh session: no lumenframe.json → placeholder text + short pointer."""
    assert loop._get_lumenframe_prompt_text() == "(no lumenframe document in session yet)"
    ops = loop._get_lumenframe_ops_catalog()
    assert "lumen_get" in ops
    assert len(ops) < 200
    # Prompt building must not create the file as a side effect.
    lf_path = layer_module._lumenframe_file_path(loop._tool_ctx)
    assert lf_path is not None and not lf_path.exists()


def test_empty_saved_doc_keeps_ops_minimal(loop: AgentLoopV3) -> None:
    """A saved but layer-less doc still gets the short pointer, not the catalog."""
    _save_via_production_path(loop, empty_doc())
    ops = loop._get_lumenframe_ops_catalog()
    assert "lumen_get" in ops
    assert len(ops) < 200


def test_saved_doc_with_layers_injects_summary_and_full_catalog(loop: AgentLoopV3) -> None:
    """The core regression: an edit saved through the production path must be
    visible to the next prompt build."""
    _save_via_production_path(loop, _doc_with_layer())

    # The file (not the cache) is what got written.
    lf_path = layer_module._lumenframe_file_path(loop._tool_ctx)
    assert lf_path is not None and lf_path.exists()
    assert loop.session_id not in layer_module._DOC_CACHE

    text = loop._get_lumenframe_prompt_text()
    assert "Layer tree:" in text
    assert "bg" in text

    ops = loop._get_lumenframe_ops_catalog()
    assert len(ops) > 1000
    assert "add_layer" in ops or "set_opacity" in ops


def test_full_catalog_much_larger_than_pointer(loop: AgentLoopV3) -> None:
    """Size contract: pointer stays tiny, full catalog is substantial."""
    ops_empty = loop._get_lumenframe_ops_catalog()
    _save_via_production_path(loop, _doc_with_layer())
    ops_full = loop._get_lumenframe_ops_catalog()

    assert len(ops_empty) < 200
    assert len(ops_full) > 1500
    assert (len(ops_full) - len(ops_empty)) > 1500


def test_prompt_text_is_size_capped(loop: AgentLoopV3) -> None:
    """A huge layer tree must not balloon the prompt."""
    doc = empty_doc()
    ops = [
        {"op": "add_layer", "type": "solid", "name": f"layer_{i:04d}_{'x' * 40}"}
        for i in range(200)
    ]
    doc = apply_layer_patch(doc, {"version": 1, "ops": ops})
    _save_via_production_path(loop, doc)

    text = loop._get_lumenframe_prompt_text()
    cap = AgentLoopV3._LUMENFRAME_PROMPT_CAP
    assert len(text) <= cap + 100  # cap + truncation notice
    assert "truncated" in text


def test_clear_session_purges_both_caches(loop: AgentLoopV3) -> None:
    """clear_lumenframe_session drops the doc cache AND the per-session
    (session_id, project_id) path-cache entries."""
    _save_via_production_path(loop, _doc_with_layer())
    # Populate the path cache.
    assert layer_module._lumenframe_file_path(loop._tool_ctx) is not None
    assert any(k[0] == loop.session_id for k in layer_module._LUMENFRAME_PATH_CACHE)

    layer_module.clear_lumenframe_session(loop.session_id)
    assert loop.session_id not in layer_module._DOC_CACHE
    assert not any(k[0] == loop.session_id for k in layer_module._LUMENFRAME_PATH_CACHE)


def test_projectless_context_still_uses_memory_cache(tmp_path: Path) -> None:
    """Embedded/project-less contexts (ctx.project is None) fall back to the
    in-memory cache — peek must see what _save_lumendoc stored there."""
    from gemia.tools._context import AssetRegistry, ToolContext

    sid = f"test_session_{uuid.uuid4().hex[:8]}"
    ctx = ToolContext(
        session_id=sid,
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _u: None,
    )
    assert layer_module.peek_lumendoc(ctx) is None
    layer_module._save_lumendoc(ctx, _doc_with_layer())
    doc = layer_module.peek_lumendoc(ctx)
    assert doc is not None and doc["root"]["children"]
    layer_module.clear_lumenframe_session(sid)
