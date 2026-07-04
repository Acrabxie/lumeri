"""Feature 2 — lanes as real stacked tracks in ``lumenframe.compile``.

``lane`` is stored on every layer but, historically, ``compile.py`` ignored it.
This suite pins the new behaviour:

* ``_populate_stack`` orders a comp's children by ``(lane, tree-index)`` — a
  STABLE sort on ``lane`` — so a HIGHER lane composites ABOVE a lower lane while
  WITHIN a lane the existing bottom->top tree order is preserved.
* CRITICAL regression: when every layer has ``lane == 0`` (the default) the sort
  is the identity permutation, so z-order and every rendered byte stay EXACTLY
  what they are today. We prove this by golden-comparing the real compile output
  against a reference that does NOT touch ordering (lane sort monkeypatched to a
  no-op identity).

All docs are synthetic; no network / keys / external assets.
"""
from __future__ import annotations

import numpy as np
import pytest

from lumenframe import apply_layer_patch, empty_doc
from lumenframe import compile as lf_compile
from lumenframe.compile import _lane_ordered_children, compile_to_layer_stack


# ── helpers (mirror tests/test_lumenframe_compile.py conventions) ──────────


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def base_doc(w=64, h=48, fps=10):
    return empty_doc(width=w, height=h, fps=fps)


def add_solid(doc, lid, color, *, start=0.0, duration=1.0, **fields):
    return apply_layer_patch(doc, patch({
        "op": "add_layer", "id": lid, "type": "solid", "color": color,
        "start": start, "duration": duration, **fields,
    }))


def center_px(frame):
    return frame[frame.shape[0] // 2, frame.shape[1] // 2]


def z_by_id(stack) -> dict[str, int]:
    """Map runtime layer id -> assigned z_index for the compiled stack."""
    return {layer.id: layer.z_index for layer in stack.layers}


# ── unit: the ordering primitive ──────────────────────────────────────────


def test_lane_order_identity_when_all_zero():
    """All lane==0 -> identity permutation (same object order, in place)."""
    children = [{"id": "a", "lane": 0}, {"id": "b", "lane": 0}, {"id": "c", "lane": 0}]
    out = _lane_ordered_children(children)
    assert [c["id"] for c in out] == ["a", "b", "c"]
    # Identity: same objects in the same order.
    assert all(o is c for o, c in zip(out, children))


def test_lane_order_missing_lane_treated_as_zero():
    """A layer without a 'lane' key is treated as lane 0 (stable, no reorder)."""
    children = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    assert [c["id"] for c in _lane_ordered_children(children)] == ["a", "b", "c"]


def test_lane_order_higher_lane_moves_to_top():
    """A higher lane sorts AFTER (= composites above) a lower lane."""
    children = [{"id": "a", "lane": 1}, {"id": "b", "lane": 0}]
    # a is earlier in the tree but has the higher lane -> a ends up last (top).
    assert [c["id"] for c in _lane_ordered_children(children)] == ["b", "a"]


def test_lane_order_stable_within_lane():
    """Equal lanes keep their original tree order (stable sort)."""
    children = [
        {"id": "a", "lane": 0},
        {"id": "b", "lane": 1},
        {"id": "c", "lane": 0},
        {"id": "d", "lane": 1},
    ]
    # lane 0: a, c (tree order) ; lane 1: b, d (tree order) ; then lane 0 < lane 1
    assert [c["id"] for c in _lane_ordered_children(children)] == ["a", "c", "b", "d"]


def test_lane_order_negative_lane_sinks_below():
    """A negative lane composites below lane 0."""
    children = [{"id": "a", "lane": 0}, {"id": "b", "lane": -1}]
    assert [c["id"] for c in _lane_ordered_children(children)] == ["b", "a"]


# ── (a) REGRESSION: default lane (all 0) is byte-identical ─────────────────


def test_default_lane_z_equals_tree_index():
    """With all lane==0, each layer's compiled z_index equals its tree index."""
    doc = base_doc()
    doc = add_solid(doc, "l0", "#FF0000")
    doc = add_solid(doc, "l1", "#00FF00")
    doc = add_solid(doc, "l2", "#0000FF")
    stack = compile_to_layer_stack(doc)
    z = z_by_id(stack)
    # Tree order is the add order: l0 (z0), l1 (z1), l2 (z2).
    assert z == {"l0": 0, "l1": 1, "l2": 2}


def _compile_with_ordering_disabled(doc, monkeypatch):
    """Compile while _lane_ordered_children is forced to a pure no-op identity.

    This reproduces the *pre-feature* behaviour (children consumed in raw tree
    order), giving a golden reference to byte-compare the real compile against.
    """
    monkeypatch.setattr(lf_compile, "_lane_ordered_children", lambda children: children)
    return compile_to_layer_stack(doc)


def test_default_lane_frames_byte_identical_to_unordered_reference(monkeypatch):
    """All lane==0: every rendered frame is byte-identical to the no-ordering ref.

    Build one doc, compile it the real way (lane ordering active) and again with
    ordering disabled, then assert the rendered frames are bit-for-bit equal.
    """
    def build():
        d = base_doc()
        d = add_solid(d, "bg", "#202020")
        d = add_solid(d, "mid", "#00FF00", start=0.2, duration=0.6)
        d = add_solid(d, "top", "#0000FF", opacity=0.5)
        return d

    real_stack = compile_to_layer_stack(build())

    ref_stack = _compile_with_ordering_disabled(build(), monkeypatch)

    assert real_stack.total_frames == ref_stack.total_frames
    for f in range(real_stack.total_frames):
        a = np.asarray(real_stack.render_frame(f))
        b = np.asarray(ref_stack.render_frame(f))
        assert a.shape == b.shape
        assert np.array_equal(a, b), f"frame {f} differs from unordered reference"


def test_default_lane_nested_comp_byte_identical(monkeypatch):
    """Nested composition with all lane==0 also stays byte-identical."""
    def build():
        d = base_doc()
        d = apply_layer_patch(d, patch({
            "op": "add_layer", "id": "comp", "type": "composition",
            "start": 0.0, "duration": 1.0,
        }))
        d = apply_layer_patch(d, patch({
            "op": "add_layer", "id": "inner0", "type": "solid", "color": "#FF0000",
            "parent_id": "comp", "start": 0.0, "duration": 1.0,
        }))
        d = apply_layer_patch(d, patch({
            "op": "add_layer", "id": "inner1", "type": "solid", "color": "#00FF00",
            "parent_id": "comp", "start": 0.0, "duration": 1.0,
        }))
        return d

    real_stack = compile_to_layer_stack(build())
    ref_stack = _compile_with_ordering_disabled(build(), monkeypatch)
    for f in range(real_stack.total_frames):
        a = np.asarray(real_stack.render_frame(f))
        b = np.asarray(ref_stack.render_frame(f))
        assert np.array_equal(a, b), f"nested frame {f} differs from reference"


# ── (b) lanes SET: higher lane renders ABOVE a lower lane ──────────────────


def test_higher_lane_renders_above_lower_lane():
    """Layer A (lane=1) placed EARLIER in tree than B (lane=0) renders ABOVE B.

    Both fully opaque and fill the canvas; the centre pixel must be A's colour
    because A composites on top once lanes are honoured (without lanes, B — added
    later — would win).
    """
    doc = base_doc()
    # A added first (lower tree index) but on the higher lane.
    doc = add_solid(doc, "A", "#FF0000", lane=1)
    # B added second (higher tree index) on the default lane 0.
    doc = add_solid(doc, "B", "#0000FF", lane=0)

    stack = compile_to_layer_stack(doc)
    z = z_by_id(stack)
    # A's lane (1) > B's lane (0) => A gets the higher z (drawn on top).
    assert z["A"] > z["B"]

    px = center_px(stack.render_frame(0))
    # Red (A) wins the centre, not blue (B).
    assert px[0] == pytest.approx(1.0)
    assert px[2] == pytest.approx(0.0)
    assert px[3] == pytest.approx(1.0)


def test_lane_zero_keeps_tree_order_for_top():
    """Sanity: with both on lane 0, the later-added layer still wins (unchanged)."""
    doc = base_doc()
    doc = add_solid(doc, "A", "#FF0000", lane=0)
    doc = add_solid(doc, "B", "#0000FF", lane=0)
    px = center_px(compile_to_layer_stack(doc).render_frame(0))
    # B (added later, same lane) is on top -> blue centre.
    assert px[2] == pytest.approx(1.0)
    assert px[0] == pytest.approx(0.0)


def test_within_lane_order_preserved_across_lanes():
    """Two-per-lane: ordering is (lane, tree-index) end to end.

    lane 0: bg0 (red), bg1 (green) ; lane 2: hi0 (blue), hi1 (white).
    Final z order (bottom->top) must be bg0 < bg1 < hi0 < hi1.
    """
    doc = base_doc()
    doc = add_solid(doc, "bg0", "#FF0000", lane=0)
    doc = add_solid(doc, "hi0", "#0000FF", lane=2)   # earlier tree idx, higher lane
    doc = add_solid(doc, "bg1", "#00FF00", lane=0)
    doc = add_solid(doc, "hi1", "#FFFFFF", lane=2)
    stack = compile_to_layer_stack(doc)
    z = z_by_id(stack)
    assert z["bg0"] < z["bg1"] < z["hi0"] < z["hi1"]
    # Top of stack (hi1, white) wins the centre.
    px = center_px(stack.render_frame(0))
    assert px[0] == pytest.approx(1.0)
    assert px[1] == pytest.approx(1.0)
    assert px[2] == pytest.approx(1.0)


# ── (c) determinism ────────────────────────────────────────────────────────


def test_lane_ordering_is_deterministic():
    """Compiling the same lane-set doc twice yields identical z-order + frames."""
    def build():
        d = base_doc()
        d = add_solid(d, "A", "#FF0000", lane=3)
        d = add_solid(d, "B", "#00FF00", lane=1)
        d = add_solid(d, "C", "#0000FF", lane=2)
        return d

    s1 = compile_to_layer_stack(build())
    s2 = compile_to_layer_stack(build())
    assert z_by_id(s1) == z_by_id(s2)
    # Lane order: B(1) < C(2) < A(3).
    z = z_by_id(s1)
    assert z["B"] < z["C"] < z["A"]
    for f in range(s1.total_frames):
        assert np.array_equal(
            np.asarray(s1.render_frame(f)), np.asarray(s2.render_frame(f))
        ), f"frame {f} not deterministic across compiles"


def test_lanes_in_nested_composition():
    """Lane ordering applies recursively inside a nested composition."""
    doc = base_doc()
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "comp", "type": "composition",
        "start": 0.0, "duration": 1.0,
    }))
    # Inside comp: low lane added later, high lane added earlier.
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "in_hi", "type": "solid", "color": "#FF0000",
        "parent_id": "comp", "start": 0.0, "duration": 1.0, "lane": 5,
    }))
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "in_lo", "type": "solid", "color": "#0000FF",
        "parent_id": "comp", "start": 0.0, "duration": 1.0, "lane": 0,
    }))
    stack = compile_to_layer_stack(doc)
    # The comp's nested stack composites in_hi (lane 5) above in_lo (lane 0),
    # so the comp — and the canvas centre — shows red.
    px = center_px(stack.render_frame(0))
    assert px[0] == pytest.approx(1.0)
    assert px[2] == pytest.approx(0.0)
