"""Feature 3 — ``merge_compositions``: lift two timelines/compositions into one.

The op brings the *children* of each source composition up into a target comp
(re-timing them onto the target's local timeline like ungroup), re-ids every
lifted subtree in one shared id-space (so nothing collides and cross-subtree
matte/parent references follow), and removes the emptied source containers.

What is pinned here:

* **append** — source children start shifted by the target's current content
  extent (max child end) + offset; exact start values asserted, ids fresh, the
  whole doc passes ``validate_doc`` with no duplicate ids.
* **overlay** — source children keep their local start (+ offset, default 0);
  a *golden render equivalence* proves the overlay-merged doc renders byte-for-
  byte identically to a hand-built doc holding the same children in the same comp
  (``np.array_equal`` over several frames).
* **mattes** — a track-matte that pointed inside a moved subtree is re-pointed to
  the lifted layer's new id (intra-subtree *and* cross-subtree).
* **doc_duration** — correct after both modes.
* **E_ARG** — into_id missing / not a composition, source missing / not a
  composition, source == into_id, root as source, unknown mode, into_id inside a
  source, empty source_ids.
"""
from __future__ import annotations

import numpy as np
import pytest

from lumenframe import model, registry
from lumenframe.ops import apply_layer_patch, validate_doc, LayerPatchError


def setup_function(_fn):
    registry.reset_for_tests()


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def _solid(lid, color="#ff0000", *, start=0.0, duration=1.0, **fields):
    layer = model.new_layer("solid", id=lid, start=start, duration=duration, **fields)
    layer["props"]["color"] = color
    return layer


def _comp(lid, *children, name=None, **fields):
    comp = model.new_layer("composition", id=lid, name=name or lid, **fields)
    comp["children"] = list(children)
    return comp


def _doc(*root_children, width=64, height=48, fps=10):
    doc = model.empty_doc(width=width, height=height, fps=fps)
    doc["root"]["children"] = list(root_children)
    return doc


def _starts(comp):
    return [round(model._as_float(c.get("start")), 6) for c in comp.get("children") or []]


def _all_ids(doc):
    return [str(n.get("id")) for n in model.walk(doc["root"])]


# ── append: starts shifted by the target's content extent ────────────────


def test_append_shifts_source_children_past_target_extent():
    # Target comp A holds content out to extent 3.0; source comp B's children sit
    # at local 0.0 and 0.5. Append should place them at 3.0 and 3.5.
    A = _comp("A", _solid("a1", start=0.0, duration=3.0),
              _solid("a2", start=1.0, duration=2.0))  # extent = max(3.0, 3.0) = 3.0
    B = _comp("B", _solid("b1", start=0.0, duration=2.0),
              _solid("b2", start=0.5, duration=1.0))
    doc = _doc(A, B)

    out = apply_layer_patch(doc, patch(
        {"op": "merge_compositions", "source_ids": ["B"], "into_id": "A", "mode": "append"}))

    merged = model.find_layer(out, "A")
    assert _starts(merged) == [0.0, 1.0, 3.0, 3.5]
    # Source container is gone (emptied + removed by default).
    assert model.find_layer(out, "B") is None
    validate_doc(out)


def test_append_with_offset_adds_gap_after_extent():
    A = _comp("A", _solid("a1", start=0.0, duration=4.0))  # extent 4.0
    B = _comp("B", _solid("b1", start=0.0, duration=1.0),
              _solid("b2", start=2.0, duration=1.0))
    doc = _doc(A, B)

    out = apply_layer_patch(doc, patch(
        {"op": "merge_compositions", "source_ids": ["B"], "into_id": "A",
         "mode": "append", "offset": 0.5}))

    merged = model.find_layer(out, "A")
    # baseline = extent(4.0) + offset(0.5) = 4.5, then + local starts.
    assert _starts(merged) == [0.0, 4.5, 6.5]


def test_append_into_root_sets_doc_duration():
    # into_id = root composition: the merge extends the whole timeline.
    src = _comp("B", _solid("b1", start=0.0, duration=2.0),
                _solid("b2", start=0.5, duration=1.5))  # local extent 2.0
    doc = _doc(_solid("r1", start=0.0, duration=3.0), src)  # root extent 3.0
    root_id = str(doc["root"]["id"])

    out = apply_layer_patch(doc, patch(
        {"op": "merge_compositions", "source_ids": ["B"], "into_id": root_id, "mode": "append"}))

    assert _starts(out["root"]) == [0.0, 3.0, 3.5]
    assert model.doc_duration(out) == 5.0  # 3.0 (r1) + 2.0 (b extent appended)


def test_multiple_sources_append_stack_sequentially():
    A = _comp("A", _solid("a1", start=0.0, duration=2.0))  # extent 2.0
    B = _comp("B", _solid("b1", start=0.0, duration=1.0))  # local extent 1.0
    C = _comp("C", _solid("c1", start=0.0, duration=3.0))  # local extent 3.0
    doc = _doc(A, B, C)

    out = apply_layer_patch(doc, patch(
        {"op": "merge_compositions", "source_ids": ["B", "C"], "into_id": "A", "mode": "append"}))

    merged = model.find_layer(out, "A")
    # a1 @0, then B baseline = 2.0 -> b1 @2.0 (B extent now 3.0),
    # then C baseline = 3.0 -> c1 @3.0.
    assert _starts(merged) == [0.0, 2.0, 3.0]
    assert model.find_layer(out, "B") is None
    assert model.find_layer(out, "C") is None
    validate_doc(out)


# ── append/overlay must EXTEND the target's explicit duration ─────────────
# Regression for the silent-drop bug: a nested comp compiles its length from its
# EXPLICIT ``duration`` (compile._composition_content sub_total = round(dur*fps),
# clamping every local_frame to sub_total-1). Appending children past the original
# extent without growing ``duration`` made them NEVER render.


def test_append_extends_nested_target_duration_to_cover_lifted_content():
    # A is a *nested* comp authored exactly to its content (extent == duration == 3.0).
    A = _comp("A", _solid("a1", start=0.0, duration=3.0), duration=3.0)
    B = _comp("B", _solid("b1", start=0.0, duration=2.0))  # local extent 2.0
    doc = _doc(A, B)
    assert model.find_layer(doc, "A")["duration"] == 3.0  # pre-condition

    out = apply_layer_patch(doc, patch(
        {"op": "merge_compositions", "source_ids": ["B"], "into_id": "A", "mode": "append"}))

    merged = model.find_layer(out, "A")
    assert _starts(merged) == [0.0, 3.0]          # b1 appended at the old extent
    # The fix: duration grows from 3.0 to the new content extent 5.0 so the
    # appended child is inside the comp's compiled length (was the bug: stayed 3.0).
    assert merged["duration"] == 5.0
    assert merged["duration"] == model._composition_extent(merged)
    validate_doc(out)


def test_append_never_shrinks_a_comp_authored_longer_than_its_content():
    # A is authored to 6.0 though its content only reaches 3.0. Append must take the
    # MAX(authored, new extent), never shrink the authored tail.
    A = _comp("A", _solid("a1", start=0.0, duration=3.0), duration=6.0)
    B = _comp("B", _solid("b1", start=0.0, duration=1.0))  # appended at extent 3.0 -> 3..4
    doc = _doc(A, B)

    out = apply_layer_patch(doc, patch(
        {"op": "merge_compositions", "source_ids": ["B"], "into_id": "A", "mode": "append"}))

    merged = model.find_layer(out, "A")
    assert _starts(merged) == [0.0, 3.0]
    # new content extent is 4.0 but authored 6.0 wins -> duration stays 6.0.
    assert merged["duration"] == 6.0


def test_overlay_extends_target_when_lifted_child_overruns_duration():
    # Overlay a longer source child into a shorter authored target: the duration
    # must grow so the overrunning overlaid content renders.
    A = _comp("A", _solid("a1", start=0.0, duration=1.0), duration=1.0)
    B = _comp("B", _solid("b1", start=0.0, duration=2.0))  # overlaid, overruns to 2.0
    doc = _doc(A, B)

    out = apply_layer_patch(doc, patch(
        {"op": "merge_compositions", "source_ids": ["B"], "into_id": "A", "mode": "overlay"}))

    merged = model.find_layer(out, "A")
    assert _starts(merged) == [0.0, 0.0]          # overlay keeps local starts
    assert merged["duration"] == 2.0              # grown to cover the overrun
    validate_doc(out)


def test_append_render_shows_appended_content_past_old_extent():
    """Render-level regression: a frame in the APPENDED region of a nested comp
    shows the appended (green) content, not a clamped blank/old frame.

    Without the duration-extend fix the appended child sits past the comp's
    compiled length (sub_total clamps local_frame to sub_total-1), so the blue
    base below would show through instead of green.
    """
    from lumenframe.compile import compile_to_layer_stack

    W, H, FPS = 8, 8, 10

    # Blue base spans the whole 2s timeline on lane 0; the nested comp NC (lane 1,
    # authored to its 1.0s content) sits above it holding a red 0..1 child.
    base = _solid("base", "#0000ff", start=0.0, duration=2.0, lane=0)
    NC = _comp("NC", _solid("red", "#ff0000", start=0.0, duration=1.0),
               start=0.0, duration=1.0, lane=1)
    B = _comp("B", _solid("grn", "#00ff00", start=0.0, duration=1.0))  # -> appended 1..2
    doc = _doc(base, NC, B, width=W, height=H, fps=FPS)

    out = apply_layer_patch(doc, patch(
        {"op": "merge_compositions", "source_ids": ["B"], "into_id": "NC", "mode": "append"}))

    merged = model.find_layer(out, "NC")
    assert merged["duration"] == 2.0  # extended past old extent of 1.0
    stack = compile_to_layer_stack(out, strict=False)

    # frame 0 (t=0): NC's red over blue base.
    f0 = stack.render_frame(0)[H // 2, W // 2]
    assert f0[0] == pytest.approx(1.0) and f0[2] == pytest.approx(0.0), "expected red at t=0"

    # frame 15 (t=1.5s): the APPENDED green region. With the bug this was the blue
    # base showing through (green clamped away); now it must be green.
    f15 = stack.render_frame(15)[H // 2, W // 2]
    assert f15[1] == pytest.approx(1.0), "appended green did not render past old extent"
    assert f15[0] == pytest.approx(0.0) and f15[2] == pytest.approx(0.0), \
        "blue base bled through — appended content was clamped/dropped"


def test_overlay_render_shows_overrunning_content_past_old_extent():
    """Render-level regression for OVERLAY: a longer overlaid child renders past
    the target's original (shorter) authored duration."""
    from lumenframe.compile import compile_to_layer_stack

    W, H, FPS = 8, 8, 10

    base = _solid("base", "#0000ff", start=0.0, duration=2.0, lane=0)
    NC = _comp("NC", _solid("red", "#ff0000", start=0.0, duration=1.0),
               start=0.0, duration=1.0, lane=1)
    B = _comp("B", _solid("grn", "#00ff00", start=0.0, duration=2.0))  # overruns to 2.0
    doc = _doc(base, NC, B, width=W, height=H, fps=FPS)

    out = apply_layer_patch(doc, patch(
        {"op": "merge_compositions", "source_ids": ["B"], "into_id": "NC", "mode": "overlay"}))

    assert model.find_layer(out, "NC")["duration"] == 2.0
    stack = compile_to_layer_stack(out, strict=False)
    f15 = stack.render_frame(15)[H // 2, W // 2]  # t=1.5s, past the old 1.0 duration
    assert f15[1] == pytest.approx(1.0), "overrunning overlaid content did not render"
    assert f15[2] == pytest.approx(0.0), "blue base bled through — overlay overrun was clamped"


# ── ids are minted fresh, no collisions ──────────────────────────────────


def test_lifted_ids_are_fresh_no_collision():
    A = _comp("A", _solid("a1", start=0.0, duration=2.0))
    B = _comp("B", _solid("b1", start=0.0, duration=1.0),
              _comp("inner", _solid("deep", start=0.0, duration=1.0)))
    doc = _doc(A, B)

    out = apply_layer_patch(doc, patch(
        {"op": "merge_compositions", "source_ids": ["B"], "into_id": "A", "mode": "overlay"}))

    ids = _all_ids(out)
    assert len(ids) == len(set(ids))  # no duplicates anywhere
    # None of the original source ids survive (they were re-ided).
    for old in ("b1", "inner", "deep"):
        assert model.find_layer(out, old) is None
    # Existing target ids are untouched.
    assert model.find_layer(out, "a1") is not None
    validate_doc(out)


def test_nested_subtree_is_lifted_whole():
    A = _comp("A", _solid("a1", start=0.0, duration=2.0))
    inner = _comp("inner", _solid("deep", start=0.0, duration=1.0))
    B = _comp("B", inner)
    doc = _doc(A, B)

    out = apply_layer_patch(doc, patch(
        {"op": "merge_compositions", "source_ids": ["B"], "into_id": "A", "mode": "overlay"}))

    merged = model.find_layer(out, "A")
    # A now holds a1 plus the lifted inner comp (which still holds its deep child).
    assert len(merged["children"]) == 2
    lifted_inner = [c for c in merged["children"] if c.get("type") == "composition"]
    assert len(lifted_inner) == 1
    assert len(lifted_inner[0]["children"]) == 1


# ── mattes are re-pointed to the new ids ─────────────────────────────────


def test_intra_subtree_matte_is_repointed():
    # Inside source B, b_fg uses b_matte as an alpha matte. After lifting, the
    # reference must follow b_matte's *new* id (not dangle at "b_matte").
    A = _comp("A", _solid("a1", start=0.0, duration=2.0))
    b_matte = _solid("b_matte", start=0.0, duration=2.0)
    b_fg = _solid("b_fg", start=0.0, duration=2.0)
    b_fg["mask"] = {"kind": "alpha_matte", "source_layer_id": "b_matte"}
    B = _comp("B", b_matte, b_fg)
    doc = _doc(A, B)

    out = apply_layer_patch(doc, patch(
        {"op": "merge_compositions", "source_ids": ["B"], "into_id": "A", "mode": "overlay"}))

    merged = model.find_layer(out, "A")
    fg = next(c for c in merged["children"]
              if isinstance(c.get("mask"), dict) and c["mask"].get("kind") == "alpha_matte")
    new_src = fg["mask"]["source_layer_id"]
    # The reference points at a real, present sibling — its new id, not "b_matte".
    assert new_src != "b_matte"
    assert model.find_layer(out, new_src) is not None
    assert any(str(c.get("id")) == new_src for c in merged["children"])
    validate_doc(out)  # validate_doc would raise E_MASK on a dangling matte


def test_cross_subtree_matte_is_repointed():
    # Two separate source comps merged together: a matte in C points at a layer in
    # B. Both are lifted in ONE merge call, so the cross-comp reference must be
    # re-pointed to B's new id (a per-subtree remap would leave it dangling).
    A = _comp("A", _solid("a1", start=0.0, duration=2.0))
    B = _comp("B", _solid("shared_matte", start=0.0, duration=2.0))
    c_fg = _solid("c_fg", start=0.0, duration=2.0)
    c_fg["mask"] = {"kind": "luma_matte", "source_layer_id": "shared_matte"}
    C = _comp("C", c_fg)
    doc = _doc(A, B, C)

    out = apply_layer_patch(doc, patch(
        {"op": "merge_compositions", "source_ids": ["B", "C"], "into_id": "A", "mode": "overlay"}))

    merged = model.find_layer(out, "A")
    fg = next(c for c in merged["children"]
              if isinstance(c.get("mask"), dict) and c["mask"].get("kind") == "luma_matte")
    new_src = fg["mask"]["source_layer_id"]
    assert new_src != "shared_matte"
    assert model.find_layer(out, new_src) is not None
    validate_doc(out)


# ── overlay: starts unchanged + golden render equivalence ────────────────


def test_overlay_keeps_local_starts():
    A = _comp("A", _solid("a1", start=0.0, duration=3.0))
    B = _comp("B", _solid("b1", start=0.0, duration=2.0),
              _solid("b2", start=1.5, duration=1.0))
    doc = _doc(A, B)

    out = apply_layer_patch(doc, patch(
        {"op": "merge_compositions", "source_ids": ["B"], "into_id": "A", "mode": "overlay"}))

    merged = model.find_layer(out, "A")
    assert _starts(merged) == [0.0, 0.0, 1.5]  # a1 unchanged, b1/b2 at local times


def test_overlay_offset_shifts_all_source_children():
    A = _comp("A", _solid("a1", start=0.0, duration=3.0))
    B = _comp("B", _solid("b1", start=0.0, duration=2.0),
              _solid("b2", start=1.0, duration=1.0))
    doc = _doc(A, B)

    out = apply_layer_patch(doc, patch(
        {"op": "merge_compositions", "source_ids": ["B"], "into_id": "A",
         "mode": "overlay", "offset": 0.5}))

    merged = model.find_layer(out, "A")
    assert _starts(merged) == [0.0, 0.5, 1.5]


def test_overlay_into_root_doc_duration():
    src = _comp("B", _solid("b1", start=0.0, duration=2.0))
    doc = _doc(_solid("r1", start=0.0, duration=3.0), src)
    root_id = str(doc["root"]["id"])

    out = apply_layer_patch(doc, patch(
        {"op": "merge_compositions", "source_ids": ["B"], "into_id": root_id, "mode": "overlay"}))

    # overlay: r1 ends at 3.0, b1 ends at 2.0 -> max = 3.0
    assert model.doc_duration(out) == 3.0


def test_overlay_render_equals_handbuilt_doc():
    """Golden: an overlay-merge into root renders identically to a doc with the
    same children placed directly in root (same colors, same local times)."""
    from lumenframe.compile import compile_to_layer_stack

    W, H, FPS = 32, 24, 10

    # Source comp B holds a red full-canvas solid (frames 0..1) and a green
    # full-canvas solid starting at 0.5s that composites over it.
    def build_source_children():
        return [
            _solid("b1", "#ff0000", start=0.0, duration=1.0),
            _solid("b2", "#00ff00", start=0.5, duration=0.5),
        ]

    B = _comp("B", *build_source_children())
    merge_doc = _doc(B, width=W, height=H, fps=FPS)
    root_id = str(merge_doc["root"]["id"])
    merged = apply_layer_patch(merge_doc, patch(
        {"op": "merge_compositions", "source_ids": ["B"], "into_id": root_id, "mode": "overlay"}))

    # Hand-built reference: the SAME children sitting directly in root.
    hand_doc = _doc(*build_source_children(), width=W, height=H, fps=FPS)
    hand = apply_layer_patch(hand_doc, patch())  # normalise via the same path

    merged_stack = compile_to_layer_stack(merged, strict=False)
    hand_stack = compile_to_layer_stack(hand, strict=False)

    # Cover before/at/after the green overlay onset.
    for frame_idx in (0, 4, 5, 9):
        fm = merged_stack.render_frame(frame_idx)
        fh = hand_stack.render_frame(frame_idx)
        assert np.array_equal(fm, fh), f"frame {frame_idx} differs after overlay merge"

    # And the green really does composite over the red at the overlay onset.
    mid = merged_stack.render_frame(6)
    cpx = mid[H // 2, W // 2]
    assert cpx[1] == pytest.approx(1.0) and cpx[0] == pytest.approx(0.0)


# ── keep_sources flag ────────────────────────────────────────────────────


def test_keep_sources_leaves_empty_container():
    A = _comp("A", _solid("a1", start=0.0, duration=2.0))
    B = _comp("B", _solid("b1", start=0.0, duration=1.0))
    doc = _doc(A, B)

    out = apply_layer_patch(doc, patch(
        {"op": "merge_compositions", "source_ids": ["B"], "into_id": "A",
         "mode": "overlay", "keep_sources": True}))

    leftover = model.find_layer(out, "B")
    assert leftover is not None
    assert leftover.get("children") == []  # emptied but kept
    validate_doc(out)


# ── E_ARG cases ──────────────────────────────────────────────────────────


def test_into_id_arg_absent_is_e_arg():
    doc = _doc(_comp("B", _solid("b1")))
    with pytest.raises(LayerPatchError) as ei:
        apply_layer_patch(doc, patch(
            {"op": "merge_compositions", "source_ids": ["B"]}))  # no into_id key at all
    assert ei.value.code == "E_ARG"


def test_into_id_not_found_is_e_not_found():
    doc = _doc(_comp("B", _solid("b1")))
    with pytest.raises(LayerPatchError) as ei:
        apply_layer_patch(doc, patch(
            {"op": "merge_compositions", "source_ids": ["B"], "into_id": "ghost"}))
    assert ei.value.code == "E_NOT_FOUND"


def test_into_id_not_a_composition_is_e_arg():
    A = _solid("A", start=0.0, duration=2.0)  # not a composition
    doc = _doc(A, _comp("B", _solid("b1")))
    with pytest.raises(LayerPatchError) as ei:
        apply_layer_patch(doc, patch(
            {"op": "merge_compositions", "source_ids": ["B"], "into_id": "A"}))
    assert ei.value.code == "E_ARG"


def test_source_not_found_is_e_not_found():
    doc = _doc(_comp("A", _solid("a1")))
    with pytest.raises(LayerPatchError) as ei:
        apply_layer_patch(doc, patch(
            {"op": "merge_compositions", "source_ids": ["ghost"], "into_id": "A"}))
    assert ei.value.code == "E_NOT_FOUND"


def test_source_not_a_composition_is_e_arg():
    A = _comp("A", _solid("a1"))
    B = _solid("B", start=0.0, duration=2.0)  # not a composition
    doc = _doc(A, B)
    with pytest.raises(LayerPatchError) as ei:
        apply_layer_patch(doc, patch(
            {"op": "merge_compositions", "source_ids": ["B"], "into_id": "A"}))
    assert ei.value.code == "E_ARG"


def test_empty_source_ids_is_e_arg():
    doc = _doc(_comp("A", _solid("a1")))
    with pytest.raises(LayerPatchError) as ei:
        apply_layer_patch(doc, patch(
            {"op": "merge_compositions", "source_ids": [], "into_id": "A"}))
    assert ei.value.code == "E_ARG"


def test_source_equals_into_id_is_e_arg():
    doc = _doc(_comp("A", _solid("a1")))
    with pytest.raises(LayerPatchError) as ei:
        apply_layer_patch(doc, patch(
            {"op": "merge_compositions", "source_ids": ["A"], "into_id": "A"}))
    assert ei.value.code == "E_ARG"


def test_unknown_mode_is_e_arg():
    A = _comp("A", _solid("a1"))
    B = _comp("B", _solid("b1"))
    doc = _doc(A, B)
    with pytest.raises(LayerPatchError) as ei:
        apply_layer_patch(doc, patch(
            {"op": "merge_compositions", "source_ids": ["B"], "into_id": "A", "mode": "sideways"}))
    assert ei.value.code == "E_ARG"


def test_into_id_inside_source_is_e_arg():
    # into_id nested inside a source -> lifting would move the target itself.
    inner = _comp("inner", _solid("x", start=0.0, duration=1.0))
    B = _comp("B", inner)
    doc = _doc(B)
    with pytest.raises(LayerPatchError) as ei:
        apply_layer_patch(doc, patch(
            {"op": "merge_compositions", "source_ids": ["B"], "into_id": "inner"}))
    assert ei.value.code == "E_ARG"


def test_root_as_source_is_e_arg():
    doc = _doc(_comp("A", _solid("a1")))
    root_id = str(doc["root"]["id"])
    with pytest.raises(LayerPatchError) as ei:
        apply_layer_patch(doc, patch(
            {"op": "merge_compositions", "source_ids": [root_id], "into_id": "A"}))
    assert ei.value.code == "E_ARG"


# ── catalog drift-guard ──────────────────────────────────────────────────


def test_catalog_has_merge_compositions_entry_and_drift_guard_passes():
    from lumenframe.catalog import CORE_OPS_CATALOG

    entry = next((e for e in CORE_OPS_CATALOG if e["op"] == "merge_compositions"), None)
    assert entry is not None
    assert entry["group"] == "layer"
    assert "into_id*" in entry["args"] and "source_ids*" in entry["args"]

    # The same invariant the dedicated drift test enforces: every core op has a
    # catalog entry and vice versa.
    catalog_ops = {e["op"] for e in CORE_OPS_CATALOG}
    core_ops = {n for n in registry.list_ops() if registry.op_source(n) == "core"}
    assert "merge_compositions" in core_ops
    assert catalog_ops == core_ops
