"""Tests for the lumenframe TIME tools: lumen_seek + lumen_render_range.

These expose ``lumenframe.seek`` and ``lumenframe.render_range`` to the agent.
The tools read the session's current lumenframe doc (via ``layer._lumendoc``),
compute state / render frames, and register outputs as session assets — the same
asset path ``lumen_render`` uses.

Docs are small synthetic solids (no media, no network, no keys), mirroring the
fixture pattern in ``test_lumenframe_seek.py`` / ``test_lumenframe_render_range.py``.
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest

from gemia.tools._context import AssetRegistry, ToolContext
from gemia.tools import layer as layer_module
from gemia.tools import lumen_seek as seek_tool
from gemia.tools import lumen_render_range as range_tool

from lumenframe import apply_layer_patch, empty_doc
from lumenframe.seek import state_at


# ── fixtures / doc builders ────────────────────────────────────────────────


@pytest.fixture
def tmp_session(tmp_path: Path) -> ToolContext:
    """Minimal ToolContext; unique session_id to avoid _DOC_CACHE pollution."""
    session_id = f"test_lumen_time_{uuid.uuid4().hex[:8]}"
    return ToolContext(
        session_id=session_id,
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _: None,
    )


def _patch(*ops):
    return {"version": 1, "ops": list(ops)}


def _add_solid(doc, lid, color, *, start=0.0, duration=1.0, **fields):
    return apply_layer_patch(doc, _patch({
        "op": "add_layer", "id": lid, "type": "solid", "color": color,
        "start": start, "duration": duration, **fields,
    }))


def _multi_layer_doc():
    """fps=10, ~2s -> 20 frames. red covers [0,1)s, green covers [1,2)s."""
    doc = empty_doc(width=64, height=48, fps=10)
    doc = _add_solid(doc, "red", "#FF0000", start=0.0, duration=1.0)    # frames 0..9
    doc = _add_solid(doc, "green", "#00FF00", start=1.0, duration=1.0)  # frames 10..19
    return doc


def _seed_doc(ctx: ToolContext) -> dict:
    """Install a synthetic doc as the session's current lumenframe document."""
    doc = _multi_layer_doc()
    layer_module._save_lumendoc(ctx, doc)
    return doc


# ════════════════════════════════════════════════════════════════════════
# lumen_seek
# ════════════════════════════════════════════════════════════════════════


def test_lumen_seek_state_matches_state_at_and_registers_preview(tmp_session):
    doc = _seed_doc(tmp_session)

    # t=1.5s -> frame 15: only "green" active (matches state_at golden).
    result = asyncio.run(seek_tool.dispatch({"seconds": 1.5}, tmp_session))

    assert result["applied"] is True, result
    assert result["frame"] == 15

    golden = state_at(doc, 1.5)
    assert result["state"]["active_layer_ids"] == golden["active_layer_ids"]
    assert result["state"]["active_layer_ids"] == ["green"]
    assert result["time"] == golden["time"]

    # Preview asset exists in the session registry and on disk.
    pid = result["preview_asset_id"]
    assert pid is not None
    assert tmp_session.registry.contains(pid)
    rec = tmp_session.registry.get(pid)
    assert rec.kind == "image"
    assert rec.path.exists()
    assert Path(result["path"]).exists()


def test_lumen_seek_by_frame(tmp_session):
    doc = _seed_doc(tmp_session)

    result = asyncio.run(seek_tool.dispatch({"frame": 5}, tmp_session))
    assert result["applied"] is True, result
    assert result["frame"] == 5
    # frame 5 -> red active.
    assert result["state"]["active_layer_ids"] == ["red"]
    assert tmp_session.registry.contains(result["preview_asset_id"])


def test_lumen_seek_state_layers_have_per_layer_fields(tmp_session):
    _seed_doc(tmp_session)
    result = asyncio.run(seek_tool.dispatch({"seconds": 0.5}, tmp_session))
    assert result["applied"] is True
    layers = result["state"]["layers"]
    assert len(layers) == 1
    rec = layers[0]
    assert rec["id"] == "red"
    for key in ("local_frame", "source_frame", "opacity", "transform"):
        assert key in rec


def test_lumen_seek_requires_a_locator(tmp_session):
    _seed_doc(tmp_session)
    result = asyncio.run(seek_tool.dispatch({}, tmp_session))
    assert result["applied"] is False
    assert result["error_code"] == "E_ARG"


def test_lumen_seek_rejects_both_locators(tmp_session):
    _seed_doc(tmp_session)
    result = asyncio.run(seek_tool.dispatch({"seconds": 0.5, "frame": 5}, tmp_session))
    assert result["applied"] is False
    assert result["error_code"] == "E_ARG"


# ════════════════════════════════════════════════════════════════════════
# lumen_render_range
# ════════════════════════════════════════════════════════════════════════


def test_lumen_render_range_export_frame_count_matches_range(tmp_session):
    _seed_doc(tmp_session)  # fps=10, 20 frames

    # [0.5, 1.5)s -> frames [5, 15) -> 10 frames.
    result = asyncio.run(
        range_tool.dispatch(
            {"t_in": 0.5, "t_out": 1.5, "export": True}, tmp_session
        )
    )

    assert result["applied"] is True, result
    assert result["frame_count"] == 10
    assert result["t_in"] == 0.5
    assert result["t_out"] == 1.5

    asset_id = result["asset_id"]
    assert tmp_session.registry.contains(asset_id)
    rec = tmp_session.registry.get(asset_id)
    assert rec.kind == "video"
    assert rec.path.exists()


def test_lumen_render_range_export_with_step(tmp_session):
    _seed_doc(tmp_session)
    # [0.0, 2.0)s -> frames [0, 20) step 2 -> 10 frames.
    result = asyncio.run(
        range_tool.dispatch(
            {"t_in": 0.0, "t_out": 2.0, "step": 2, "export": True}, tmp_session
        )
    )
    assert result["applied"] is True, result
    assert result["frame_count"] == 10
    assert tmp_session.registry.get(result["asset_id"]).kind == "video"


def test_lumen_render_range_preview_returns_frame_count_and_asset(tmp_session):
    _seed_doc(tmp_session)
    # [0.5, 1.5)s -> 10 frames; no export -> preview.
    result = asyncio.run(
        range_tool.dispatch({"t_in": 0.5, "t_out": 1.5}, tmp_session)
    )
    assert result["applied"] is True, result
    assert result["frame_count"] == 10
    assert "asset_id" not in result  # preview path, not an export
    pid = result["preview_asset_id"]
    assert pid is not None
    assert tmp_session.registry.contains(pid)
    assert tmp_session.registry.get(pid).kind == "image"


def test_lumen_render_range_requires_t_in_lt_t_out(tmp_session):
    _seed_doc(tmp_session)
    result = asyncio.run(
        range_tool.dispatch({"t_in": 1.5, "t_out": 0.5}, tmp_session)
    )
    assert result["applied"] is False
    assert result["error_code"] == "E_ARG"


def test_lumen_render_range_equal_bounds_is_error(tmp_session):
    _seed_doc(tmp_session)
    result = asyncio.run(
        range_tool.dispatch({"t_in": 1.0, "t_out": 1.0}, tmp_session)
    )
    assert result["applied"] is False
    assert result["error_code"] == "E_ARG"


def test_lumen_render_range_missing_args(tmp_session):
    _seed_doc(tmp_session)
    result = asyncio.run(range_tool.dispatch({"t_in": 0.5}, tmp_session))
    assert result["applied"] is False
    assert result["error_code"] == "E_ARG"


# ════════════════════════════════════════════════════════════════════════
# registration
# ════════════════════════════════════════════════════════════════════════


def test_time_tools_registered_in_dispatcher_and_names():
    from gemia.tools import DISPATCHER, TOOL_NAMES

    for name in (
        "lumen_seek",
        "lumen_render_range",
        "lumen_set_range",
        "lumen_set_lane",
        "lumen_retime_segment",
        "lumen_reverse",
        "lumen_time_remap",
        "lumen_speed_ramp",
        "lumen_ripple_delete",
        "lumen_merge_compositions",
        "lumen_set_work_area",
    ):
        assert name in TOOL_NAMES
        assert name in DISPATCHER


def test_time_tools_have_schemas():
    from gemia.tools import TOOL_SCHEMAS

    names = [t["function"]["name"] for t in TOOL_SCHEMAS]
    assert "lumen_seek" in names
    assert "lumen_render_range" in names

    # Range schema requires t_in/t_out; seek requires neither (validated at call).
    by_name = {t["function"]["name"]: t for t in TOOL_SCHEMAS}
    range_required = by_name["lumen_render_range"]["function"]["parameters"]["required"]
    assert "t_in" in range_required and "t_out" in range_required
    seek_props = by_name["lumen_seek"]["function"]["parameters"]["properties"]
    assert "seconds" in seek_props and "frame" in seek_props
    for name in (
        "lumen_set_range",
        "lumen_set_lane",
        "lumen_retime_segment",
        "lumen_reverse",
        "lumen_time_remap",
        "lumen_speed_ramp",
        "lumen_ripple_delete",
        "lumen_merge_compositions",
        "lumen_set_work_area",
    ):
        assert name in by_name


def test_first_class_time_edit_tools_patch_the_lumen_doc(tmp_session):
    from gemia.tools import DISPATCHER

    _seed_doc(tmp_session)

    out = asyncio.run(DISPATCHER["lumen_set_range"](
        {"layer_id": "red", "frame_in": 5, "frame_out": 15}, tmp_session
    ))
    assert out["applied"] is True, out
    red = layer_module._lumendoc(tmp_session)["root"]["children"][0]
    assert red["start"] == pytest.approx(0.5)
    assert red["duration"] == pytest.approx(1.0)

    out = asyncio.run(DISPATCHER["lumen_set_lane"](
        {"layer_id": "red", "lane": 2}, tmp_session
    ))
    assert out["applied"] is True, out
    assert layer_module._lumendoc(tmp_session)["root"]["children"][0]["lane"] == 2

    out = asyncio.run(DISPATCHER["lumen_time_remap"](
        {
            "layer_id": "red",
            "keyframes": [
                {"t": 0.0, "value": 1.0},
                {"t": 1.0, "value": 0.0},
            ],
        },
        tmp_session,
    ))
    assert out["applied"] is True, out
    red = layer_module._lumendoc(tmp_session)["root"]["children"][0]
    assert red["time_remap"]["keyframes"][0]["value"] == 1.0


def test_first_class_segment_tools_retime_reverse_and_ripple(tmp_session):
    from gemia.tools import DISPATCHER

    doc = empty_doc(width=64, height=48, fps=10)
    doc = _add_solid(doc, "clip", "#ff0000", start=0.0, duration=4.0, source_in=0.0, source_out=4.0)
    doc = _add_solid(doc, "tail", "#00ff00", start=4.0, duration=1.0, source_in=0.0, source_out=1.0)
    layer_module._save_lumendoc(tmp_session, doc)

    out = asyncio.run(DISPATCHER["lumen_retime_segment"](
        {"layer_id": "clip", "t0": 1.0, "t1": 3.0, "speed": 2.0}, tmp_session
    ))
    assert out["applied"] is True, out
    kids = layer_module._lumendoc(tmp_session)["root"]["children"]
    assert any(child["speed"] == 2.0 for child in kids)

    middle = next(child for child in kids if child["speed"] == 2.0)
    out = asyncio.run(DISPATCHER["lumen_reverse"](
        {"layer_id": middle["id"]}, tmp_session
    ))
    assert out["applied"] is True, out
    assert layer_module._lumendoc(tmp_session)["root"]["children"][1].get("time_remap")

    out = asyncio.run(DISPATCHER["lumen_ripple_delete"](
        {"layer_id": "clip"}, tmp_session
    ))
    assert out["applied"] is True, out
    remaining_ids = [child["id"] for child in layer_module._lumendoc(tmp_session)["root"]["children"]]
    assert "clip" not in remaining_ids


def test_first_class_speed_ramp_merge_and_work_area(tmp_session):
    from gemia.tools import DISPATCHER

    doc = empty_doc(width=64, height=48, fps=10)
    doc = apply_layer_patch(doc, _patch(
        {
            "op": "add_layer",
            "id": "A",
            "type": "composition",
            "duration": 1.0,
            "children": [
                {"id": "a1", "type": "solid", "props": {"color": "#ff0000"}, "start": 0.0, "duration": 1.0}
            ],
        },
        {
            "op": "add_layer",
            "id": "B",
            "type": "composition",
            "duration": 1.0,
            "children": [
                {"id": "b1", "type": "solid", "props": {"color": "#00ff00"}, "start": 0.0, "duration": 1.0}
            ],
        },
    ))
    layer_module._save_lumendoc(tmp_session, doc)

    out = asyncio.run(DISPATCHER["lumen_merge_compositions"](
        {"source_ids": ["B"], "into_id": "A", "mode": "append"}, tmp_session
    ))
    assert out["applied"] is True, out
    merged = next(child for child in layer_module._lumendoc(tmp_session)["root"]["children"] if child["id"] == "A")
    assert len(merged["children"]) == 2

    child_id = merged["children"][0]["id"]
    out = asyncio.run(DISPATCHER["lumen_speed_ramp"](
        {"layer_id": child_id, "preset": "hero"}, tmp_session
    ))
    assert out["applied"] is True, out
    assert next(c for c in layer_module._lumendoc(tmp_session)["root"]["children"] if c["id"] == "A")["children"][0].get("time_remap")

    out = asyncio.run(DISPATCHER["lumen_set_work_area"](
        {"t_in": 0.25, "t_out": 1.25}, tmp_session
    ))
    assert out["applied"] is True, out
    assert out["work_area"] == {"in": 0.25, "out": 1.25}

    out = asyncio.run(DISPATCHER["lumen_set_work_area"](
        {"clear": True}, tmp_session
    ))
    assert out["applied"] is True, out
    assert out["work_area"] is None
