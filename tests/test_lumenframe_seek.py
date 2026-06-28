"""SEEK — frame_at / seek / state_at.

Three guarantees, all golden-compared against the existing compile + render
path (``compile_to_layer_stack(...).render_frame(...)``):

* ``frame_at`` == ``int(round(t * fps))`` with clamping into ``[0, total-1]``.
* ``seek(doc, seconds=t)`` is **byte-identical** to a direct
  ``compile_to_layer_stack(doc).render_frame(round(t * fps))``.
* ``state_at`` reports active ids matching which layers' ``[start,
  start + duration]`` cover ``t`` (== the compiler's ``is_active`` gating), and
  ``source_frame`` honours a ``time_remap`` curve via ``eval_time_remap``.

Docs are small synthetic solids/shapes — no media, no network, no keys.
"""
from __future__ import annotations

import numpy as np
import pytest

from lumenframe import apply_layer_patch, empty_doc, model
from lumenframe.compile import compile_to_layer_stack
from lumenframe.seek import frame_at, seek, state_at


# ── doc builders ──────────────────────────────────────────────────────────


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def base_doc(w=64, h=48, fps=10):
    return empty_doc(width=w, height=h, fps=fps)


def add_solid(doc, lid, color, *, start=0.0, duration=1.0, **fields):
    return apply_layer_patch(doc, patch({
        "op": "add_layer", "id": lid, "type": "solid", "color": color,
        "start": start, "duration": duration, **fields,
    }))


def add_shape(doc, lid, *, start=0.0, duration=1.0, **fields):
    """A shape layer needs no media; the default resolver rasterises it."""
    return apply_layer_patch(doc, patch({
        "op": "add_layer", "id": lid, "type": "shape", "start": start,
        "duration": duration,
        "props": {"shape": {"kind": "rectangle", "w": 0.5, "h": 0.5},
                  "fill": "#3366FF"},
        **fields,
    }))


def multi_layer_doc():
    """fps=10, ~2s -> 20 frames. red covers [0,1)s, green covers [1,2)s.

    Distinct windows so active-set and pixels differ across the timeline.
    """
    doc = base_doc(w=64, h=48, fps=10)
    doc = add_solid(doc, "red", "#FF0000", start=0.0, duration=1.0)    # frames 0..9
    doc = add_solid(doc, "green", "#00FF00", start=1.0, duration=1.0)  # frames 10..19
    return doc


# ════════════════════════════════════════════════════════════════════════
# frame_at
# ════════════════════════════════════════════════════════════════════════


def test_frame_at_is_round_t_times_fps():
    doc = multi_layer_doc()  # fps=10, total_frames=20
    fps = 10
    for t in (0.0, 0.04, 0.05, 0.1, 0.14, 0.15, 0.99, 1.0, 1.23):
        assert frame_at(doc, t) == int(round(t * fps)), t


def test_frame_at_clamps_out_of_range():
    doc = multi_layer_doc()  # total_frames=20 -> last index 19
    assert frame_at(doc, -5.0) == 0          # negative -> 0
    assert frame_at(doc, -0.001) == 0
    assert frame_at(doc, 100.0) == 19        # far future -> last
    assert frame_at(doc, 2.0) == 19          # exactly past the end -> last
    assert frame_at(doc, 1.9) == 19          # round(19) within range


def test_frame_at_empty_doc_is_single_frame():
    # Empty / None docs compile to a 1-frame stack; every time clamps to 0.
    assert frame_at(None, 0.0) == 0
    assert frame_at(None, 5.0) == 0
    assert frame_at(empty_doc(), -1.0) == 0


def test_frame_at_matches_compiled_total_frames():
    doc = multi_layer_doc()
    stack = compile_to_layer_stack(doc)
    assert stack.total_frames == 20
    assert frame_at(doc, 1000.0) == stack.total_frames - 1


# ════════════════════════════════════════════════════════════════════════
# seek == compile + render_frame (byte-identical)
# ════════════════════════════════════════════════════════════════════════


def test_seek_seconds_byte_identical_to_direct_render():
    doc = multi_layer_doc()
    for t in (0.0, 0.5, 0.95, 1.0, 1.5, 1.95):
        idx = frame_at(doc, t)
        direct = compile_to_layer_stack(doc).render_frame(idx)
        got = seek(doc, seconds=t)
        assert np.array_equal(got, direct), f"t={t} idx={idx} mismatch"
        assert got.shape == (48, 64, 4)
        assert got.dtype == np.float32


def test_seek_frame_byte_identical_to_direct_render():
    doc = multi_layer_doc()
    for idx in (0, 9, 10, 19):
        direct = compile_to_layer_stack(doc).render_frame(idx)
        assert np.array_equal(seek(doc, frame=idx), direct), idx


def test_seek_clamps_out_of_range_seconds_and_frames():
    doc = multi_layer_doc()
    last = compile_to_layer_stack(doc).render_frame(19)
    first = compile_to_layer_stack(doc).render_frame(0)
    assert np.array_equal(seek(doc, seconds=100.0), last)
    assert np.array_equal(seek(doc, seconds=-3.0), first)
    assert np.array_equal(seek(doc, frame=999), last)
    assert np.array_equal(seek(doc, frame=-7), first)


def test_seek_requires_exactly_one_locator():
    doc = multi_layer_doc()
    with pytest.raises(ValueError):
        seek(doc)
    with pytest.raises(ValueError):
        seek(doc, seconds=0.5, frame=5)


def test_seek_with_shape_layer_no_media():
    doc = base_doc()
    doc = add_shape(doc, "rect", start=0.0, duration=1.0)
    idx = frame_at(doc, 0.3)
    assert np.array_equal(seek(doc, seconds=0.3),
                          compile_to_layer_stack(doc).render_frame(idx))


# ════════════════════════════════════════════════════════════════════════
# state_at — active set matches is_active gating
# ════════════════════════════════════════════════════════════════════════


def test_state_at_active_ids_match_window_coverage():
    doc = multi_layer_doc()  # red [0,1)s, green [1,2)s
    # t=0.5s -> frame 5: only red.
    s = state_at(doc, 0.5)
    assert s["frame"] == 5
    assert s["active_layer_ids"] == ["red"]
    # t=1.5s -> frame 15: only green.
    s = state_at(doc, 1.5)
    assert s["frame"] == 15
    assert s["active_layer_ids"] == ["green"]
    # The boundary frame 10 belongs to green (end_frame is exclusive).
    s = state_at(doc, 1.0)
    assert s["frame"] == 10
    assert s["active_layer_ids"] == ["green"]


def test_state_at_overlap_reports_both_in_composite_order():
    doc = base_doc(fps=10)
    doc = add_solid(doc, "back", "#FF0000", start=0.0, duration=2.0)   # 0..19
    doc = add_solid(doc, "front", "#00FF00", start=0.5, duration=1.0)  # 5..14
    s = state_at(doc, 0.8)  # frame 8: both active
    assert set(s["active_layer_ids"]) == {"back", "front"}
    # All lane==0 (default): the lane-aware sort is the identity permutation, so
    # bottom -> top order == tree order (back before front), unchanged.
    assert s["active_layer_ids"] == ["back", "front"]
    assert [r["id"] for r in s["layers"]] == ["back", "front"]
    # And that IS the compiler's composite order (z_index, id) — render included.
    stack = compile_to_layer_stack(doc)
    comp_order = [l.id for l in sorted(stack.layers, key=lambda i: (i.z_index, i.id))]
    assert s["active_layer_ids"] == comp_order
    # Render reality: green (front) is the topmost opaque solid => green pixel.
    px = seek(doc, seconds=0.8)[0, 0]
    assert px[0] == pytest.approx(0.0) and px[1] == pytest.approx(1.0)


def test_state_at_lane_order_matches_compile_composite_not_tree_order():
    """Lanes are stacked tracks: state_at must report z-order = compile's, NOT
    raw tree order. Tree order is [bottom_tree, top_tree] but a higher lane on
    the FIRST-added layer must push it ABOVE the second — and state_at's reported
    order must match BOTH compile's composite order AND the rendered pixels.
    """
    doc = base_doc(fps=10)
    # Tree order: "early" added first (red), "late" added second (green).
    doc = add_solid(doc, "early", "#FF0000", start=0.0, duration=2.0)  # red
    doc = add_solid(doc, "late", "#00FF00", start=0.0, duration=2.0)   # green
    # Put the FIRST-in-tree layer onto a HIGHER lane so it composites ON TOP,
    # making lane order (["late", "early"]) DIFFER from tree order (["early",
    # "late"]). lane 0 stays default for "late".
    doc = apply_layer_patch(doc, patch({
        "op": "set_lane", "layer_id": "early", "lane": 5,
    }))

    norm = model.normalize_doc(doc)
    tree_ids = [c["id"] for c in norm["root"]["children"]]
    assert tree_ids == ["early", "late"]  # raw tree order (the buggy answer)

    # compile's actual composite order (bottom -> top) = (z_index, id) after the
    # lane-aware sort: lane 0 "late" bottom, lane 5 "early" top.
    stack = compile_to_layer_stack(doc)
    comp_order = [l.id for l in sorted(stack.layers, key=lambda i: (i.z_index, i.id))]
    assert comp_order == ["late", "early"]
    assert comp_order != tree_ids  # the contract violation this fixes

    s = state_at(doc, 0.5)  # frame 5: both active
    # state_at must agree with compile's composite order, NOT tree order.
    assert s["active_layer_ids"] == comp_order == ["late", "early"]
    assert [r["id"] for r in s["layers"]] == comp_order
    assert s["active_layer_ids"] != tree_ids

    # Render reality: the topmost layer (last in composite order, "early"=red) is
    # the opaque solid that wins the pixel — confirming "higher lane = on top".
    px = seek(doc, seconds=0.5)[0, 0]
    assert px[0] == pytest.approx(1.0) and px[1] == pytest.approx(0.0)
    assert s["active_layer_ids"][-1] == "early"  # topmost == the winning pixel


def test_state_at_lane_order_equals_compile_for_every_frame_multilane():
    """Golden across frames: with three layers on three different lanes, the
    state_at active order == compile composite order (z_index, id) at every frame
    where they overlap. Default-lane subset stays tree order (covered elsewhere).
    """
    doc = base_doc(fps=10)
    # Tree order x, y, z; lanes scramble it: y(lane2) top, x(lane1) mid, z(lane0) bottom.
    doc = add_solid(doc, "x", "#FF0000", start=0.0, duration=2.0)
    doc = add_solid(doc, "y", "#00FF00", start=0.0, duration=2.0)
    doc = add_solid(doc, "z", "#0000FF", start=0.0, duration=2.0)
    doc = apply_layer_patch(doc, patch({"op": "set_lane", "layer_id": "x", "lane": 1}))
    doc = apply_layer_patch(doc, patch({"op": "set_lane", "layer_id": "y", "lane": 2}))
    # z keeps default lane 0.
    stack = compile_to_layer_stack(doc)
    comp_order = [l.id for l in sorted(stack.layers, key=lambda i: (i.z_index, i.id))]
    assert comp_order == ["z", "x", "y"]  # bottom -> top by lane
    fps = 10
    for f in range(stack.total_frames):
        active = [l.id for l in sorted(stack.layers, key=lambda i: (i.z_index, i.id))
                  if l.is_active(f)]
        got = state_at(doc, f / fps)["active_layer_ids"]
        assert got == active, f"frame {f}: {got} != {active}"


def test_state_at_matches_is_active_for_every_frame():
    """Golden: state_at's active set == the compiled stack's is_active() set."""
    doc = base_doc(fps=10)
    doc = add_solid(doc, "a", "#FF0000", start=0.0, duration=1.0)
    doc = add_solid(doc, "b", "#00FF00", start=0.7, duration=0.8)
    doc = add_solid(doc, "c", "#0000FF", start=1.3, duration=0.5)
    stack = compile_to_layer_stack(doc)
    fps = 10
    for f in range(stack.total_frames):
        expected = {layer.id for layer in stack.layers if layer.is_active(f)}
        got = set(state_at(doc, f / fps)["active_layer_ids"])
        assert got == expected, f"frame {f}: {got} != {expected}"


def test_state_at_empty_doc_has_no_active_layers():
    s = state_at(None, 3.0)
    assert s["active_layer_ids"] == []
    assert s["layers"] == []
    assert s["frame"] == 0


def test_state_at_skips_invisible_layers():
    doc = base_doc(fps=10)
    doc = add_solid(doc, "shown", "#FF0000", start=0.0, duration=1.0)
    doc = add_solid(doc, "hidden", "#00FF00", start=0.0, duration=1.0, visible=False)
    s = state_at(doc, 0.5)
    assert s["active_layer_ids"] == ["shown"]


def test_state_at_reports_opacity_and_transform():
    doc = base_doc(fps=10)
    doc = add_solid(doc, "s", "#FF0000", start=0.0, duration=1.0,
                    opacity=0.5, transform={"x": 12.0, "y": -3.0, "scale_x": 2.0})
    s = state_at(doc, 0.2)
    rec = s["layers"][0]
    assert rec["id"] == "s"
    assert rec["opacity"] == pytest.approx(0.5)
    assert rec["transform"]["x"] == pytest.approx(12.0)
    assert rec["transform"]["y"] == pytest.approx(-3.0)
    assert rec["transform"]["scale_x"] == pytest.approx(2.0)
    # local_frame is output-frame minus start_frame (start=0 -> equal).
    assert rec["local_frame"] == 2


# ════════════════════════════════════════════════════════════════════════
# state_at — source_frame honours time_remap (eval_time_remap)
# ════════════════════════════════════════════════════════════════════════


def remapped_doc(keyframes, *, duration=1.0, source_out=2.0, extrapolate="hold"):
    doc = base_doc(fps=10)
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "clip", "type": "video", "asset_id": "a1",
        "start": 0.0, "duration": duration, "source_in": 0.0,
        "source_out": source_out,
    }))
    doc["assets"] = [{"id": "a1", "media_kind": "video", "path": "/synthetic"}]
    doc = apply_layer_patch(doc, patch({
        "op": "set_time_remap", "layer_id": "clip",
        "keyframes": keyframes, "extrapolate": extrapolate,
    }))
    return doc


def test_state_at_source_frame_matches_eval_time_remap_2x():
    # Linear t->2t: out frame i -> src_sec 2*(i/10) -> src frame round(2*i).
    doc = remapped_doc([
        {"t": 0.0, "value": 0.0, "interp": "linear"},
        {"t": 1.0, "value": 2.0, "interp": "linear"},
    ])
    fps = 10
    clip = model.find_layer(doc, "clip")
    for i in range(10):
        s = state_at(doc, i / fps)
        rec = next(r for r in s["layers"] if r["id"] == "clip")
        out_sec = i / fps
        expected = max(0, int(round(model.eval_time_remap(clip["time_remap"], out_sec) * fps)))
        assert rec["source_frame"] == expected == 2 * i, (i, rec["source_frame"])


def test_state_at_source_frame_freeze_holds_one_frame():
    # hold curve to constant source 0.5s -> always source frame 5.
    doc = remapped_doc([
        {"t": 0.0, "value": 0.5, "interp": "hold"},
        {"t": 1.0, "value": 0.5, "interp": "hold"},
    ])
    fps = 10
    for i in range(10):
        s = state_at(doc, i / fps)
        rec = next(r for r in s["layers"] if r["id"] == "clip")
        assert rec["source_frame"] == 5, (i, rec["source_frame"])


def test_state_at_source_frame_reverse_descends():
    doc = remapped_doc([
        {"t": 0.0, "value": 0.9, "interp": "linear"},
        {"t": 1.0, "value": 0.0, "interp": "linear"},
    ])
    fps = 10
    clip = model.find_layer(doc, "clip")
    seen = []
    for i in range(10):
        s = state_at(doc, i / fps)
        rec = next(r for r in s["layers"] if r["id"] == "clip")
        expected = max(0, int(round(model.eval_time_remap(clip["time_remap"], i / fps) * fps)))
        assert rec["source_frame"] == expected
        seen.append(rec["source_frame"])
    assert seen == sorted(seen, reverse=True)  # plays backwards


def test_state_at_source_frame_default_is_local_frame_no_remap():
    # speed=1, source_in=0, no remap -> source_frame == local_frame.
    doc = multi_layer_doc()
    s = state_at(doc, 1.5)  # frame 15, green starts at frame 10 -> local 5
    rec = next(r for r in s["layers"] if r["id"] == "green")
    assert rec["local_frame"] == 5
    assert rec["source_frame"] == 5


# ════════════════════════════════════════════════════════════════════════
# robustness: nested comps don't crash state_at
# ════════════════════════════════════════════════════════════════════════


def test_state_at_does_not_crash_on_nested_composition():
    doc = base_doc(fps=10)
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "comp", "type": "composition",
        "start": 0.0, "duration": 1.0,
    }))
    # Top-level composition is reported as one active layer; no crash.
    s = state_at(doc, 0.3)
    assert "comp" in s["active_layer_ids"]
    rec = next(r for r in s["layers"] if r["id"] == "comp")
    assert rec["local_frame"] == 3
