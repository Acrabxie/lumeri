"""Two ergonomic time ops + their Gemini-usability catalogue entries.

This slice adds, ADD-only via ``@register_op`` + a matching catalogue entry:

* ``set_lane{layer_id, lane}`` — put an existing layer on a timeline lane/track
  (the int ``compile`` reads via ``_lane_ordered_children`` to stack tracks);
* ``set_range{layer_id, frame_in, frame_out}`` — frame-native one-shot placement
  that sets ``start``/``duration`` from a frame window through the canvas
  timebase, snapped to frame boundaries.

These tests pin behaviour, validation, the lane->compile ordering, and the
catalogue contract (drift-guard + structurally-valid example envelopes), mirror-
ing ``tests/test_lumenframe_catalog_examples.py`` expectations.
"""
from __future__ import annotations

import pytest

from lumenframe import apply_layer_patch, empty_doc, find_layer
from lumenframe import model, registry
from lumenframe import timebase
from lumenframe.catalog import CORE_OPS_CATALOG, error_catalog
from lumenframe.compile import _lane_ordered_children
from lumenframe.ops import LayerPatchError, normalize_doc, validate_patch


# ── helpers (mirror tests/test_lanes.py conventions) ───────────────────────


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def base_doc(w=64, h=48, fps=10):
    return empty_doc(width=w, height=h, fps=fps)


def add_solid(doc, lid, *, start=0.0, duration=1.0, **fields):
    return apply_layer_patch(doc, patch({
        "op": "add_layer", "id": lid, "type": "solid", "color": "#ffffff",
        "start": start, "duration": duration, **fields,
    }))


def lane_of(doc, lid):
    return find_layer(doc, lid)["lane"]


# ── set_lane: behaviour ────────────────────────────────────────────────────


def test_set_lane_changes_lane():
    doc = base_doc()
    doc = add_solid(doc, "a")
    assert lane_of(doc, "a") == 0  # default lane
    out = apply_layer_patch(doc, patch({"op": "set_lane", "layer_id": "a", "lane": 3}))
    assert lane_of(out, "a") == 3
    # Source doc untouched (apply is atomic / copy-on-write).
    assert lane_of(doc, "a") == 0


def test_set_lane_accepts_integral_float_and_negative():
    doc = base_doc()
    doc = add_solid(doc, "a")
    out = apply_layer_patch(doc, patch({"op": "set_lane", "layer_id": "a", "lane": 2.0}))
    assert lane_of(out, "a") == 2
    assert isinstance(lane_of(out, "a"), int)
    out = apply_layer_patch(doc, patch({"op": "set_lane", "layer_id": "a", "lane": -1}))
    assert lane_of(out, "a") == -1


def test_set_lane_compile_orders_by_lane():
    """After set_lane the comp's children compile in (lane, tree-index) order.

    ``compile._populate_stack`` orders a comp's children with
    ``_lane_ordered_children`` (a stable sort on lane) before assigning z, so a
    higher lane composites above a lower one. We promote a tree-earlier layer to a
    higher lane and assert that exact reordering.
    """
    doc = base_doc()
    doc = add_solid(doc, "a")  # tree index 0
    doc = add_solid(doc, "b")  # tree index 1
    doc = add_solid(doc, "c")  # tree index 2

    # Default (all lane 0): identity tree order.
    children = doc["root"]["children"]
    assert [c["id"] for c in _lane_ordered_children(children)] == ["a", "b", "c"]

    # Promote the tree-earliest layer 'a' to lane 5 -> it sorts to the top.
    out = apply_layer_patch(doc, patch({"op": "set_lane", "layer_id": "a", "lane": 5}))
    ordered = _lane_ordered_children(out["root"]["children"])
    assert [c["id"] for c in ordered] == ["b", "c", "a"]

    # Sink 'c' below lane 0 -> it sorts to the bottom; 'a' stays on top.
    out = apply_layer_patch(out, patch({"op": "set_lane", "layer_id": "c", "lane": -2}))
    ordered = _lane_ordered_children(out["root"]["children"])
    assert [c["id"] for c in ordered] == ["c", "b", "a"]


# ── set_lane: validation ───────────────────────────────────────────────────


def test_set_lane_missing_layer_id_is_E_ARG():
    doc = base_doc()
    with pytest.raises(LayerPatchError) as ei:
        apply_layer_patch(doc, patch({"op": "set_lane", "lane": 1}))
    assert ei.value.code == "E_ARG"


def test_set_lane_missing_lane_is_E_ARG():
    doc = add_solid(base_doc(), "a")
    with pytest.raises(LayerPatchError) as ei:
        apply_layer_patch(doc, patch({"op": "set_lane", "layer_id": "a"}))
    assert ei.value.code == "E_ARG"


@pytest.mark.parametrize("bad", ["1", 1.5, True, False, None, [1]])
def test_set_lane_non_int_lane_is_E_ARG(bad):
    doc = add_solid(base_doc(), "a")
    with pytest.raises(LayerPatchError) as ei:
        apply_layer_patch(doc, patch({"op": "set_lane", "layer_id": "a", "lane": bad}))
    assert ei.value.code == "E_ARG"


def test_set_lane_unknown_layer_is_E_NOT_FOUND():
    doc = base_doc()
    with pytest.raises(LayerPatchError) as ei:
        apply_layer_patch(doc, patch({"op": "set_lane", "layer_id": "nope", "lane": 1}))
    assert ei.value.code == "E_NOT_FOUND"


# ── set_range: behaviour ───────────────────────────────────────────────────


def test_set_range_sets_exact_frame_derived_seconds():
    fps = 10
    doc = base_doc(fps=fps)
    doc = add_solid(doc, "a", start=0.0, duration=1.0)
    out = apply_layer_patch(doc, patch(
        {"op": "set_range", "layer_id": "a", "frame_in": 30, "frame_out": 90}
    ))
    layer = find_layer(out, "a")
    # start = to_seconds(30, 10) = 3.0 ; duration = to_seconds(90-30, 10) = 6.0
    assert layer["start"] == pytest.approx(timebase.to_seconds(30, fps))
    assert layer["duration"] == pytest.approx(timebase.to_seconds(60, fps))
    assert layer["start"] == pytest.approx(3.0)
    assert layer["duration"] == pytest.approx(6.0)


def test_set_range_snaps_to_frame_boundaries():
    """A fractional frame index resolves to a snapped, frame-accurate second."""
    fps = 24
    doc = base_doc(fps=fps)
    doc = add_solid(doc, "a")
    # frame_in 12 -> 0.5s exactly; frame_out 36 -> span 24 frames -> 1.0s.
    out = apply_layer_patch(doc, patch(
        {"op": "set_range", "layer_id": "a", "frame_in": 12, "frame_out": 36}
    ))
    layer = find_layer(out, "a")
    # Snapping is idempotent: snapping the result again must not move it.
    assert layer["start"] == pytest.approx(timebase.snap_seconds(layer["start"], fps))
    assert layer["duration"] == pytest.approx(timebase.snap_seconds(layer["duration"], fps))
    assert layer["start"] == pytest.approx(0.5)
    assert layer["duration"] == pytest.approx(1.0)
    # And each lands exactly on a frame index.
    assert timebase.to_frame(layer["start"], fps) == 12
    assert timebase.to_frame(layer["duration"], fps) == 24


def test_set_range_leaves_source_and_speed_untouched():
    doc = base_doc(fps=10)
    doc = add_solid(doc, "a", start=0.0, duration=2.0, source_in=1.0, source_out=3.0)
    before = find_layer(doc, "a")
    src_in, src_out, speed = before["source_in"], before["source_out"], before["speed"]
    out = apply_layer_patch(doc, patch(
        {"op": "set_range", "layer_id": "a", "frame_in": 5, "frame_out": 25}
    ))
    layer = find_layer(out, "a")
    assert layer["source_in"] == src_in
    assert layer["source_out"] == src_out
    assert layer["speed"] == speed
    assert layer["start"] == pytest.approx(0.5)
    assert layer["duration"] == pytest.approx(2.0)


# ── set_range: validation ──────────────────────────────────────────────────


def test_set_range_frame_out_not_greater_is_E_ARG():
    doc = add_solid(base_doc(), "a")
    for fi, fo in ((30, 30), (90, 30)):
        with pytest.raises(LayerPatchError) as ei:
            apply_layer_patch(doc, patch(
                {"op": "set_range", "layer_id": "a", "frame_in": fi, "frame_out": fo}
            ))
        assert ei.value.code == "E_ARG"


@pytest.mark.parametrize("op", [
    {"op": "set_range", "frame_in": 0, "frame_out": 10},          # no layer_id
    {"op": "set_range", "layer_id": "a", "frame_out": 10},        # no frame_in
    {"op": "set_range", "layer_id": "a", "frame_in": 0},          # no frame_out
])
def test_set_range_missing_arg_is_E_ARG(op):
    doc = add_solid(base_doc(), "a")
    with pytest.raises(LayerPatchError) as ei:
        apply_layer_patch(doc, patch(op))
    assert ei.value.code == "E_ARG"


def test_set_range_unknown_layer_is_E_NOT_FOUND():
    doc = base_doc()
    with pytest.raises(LayerPatchError) as ei:
        apply_layer_patch(doc, patch(
            {"op": "set_range", "layer_id": "nope", "frame_in": 0, "frame_out": 10}
        ))
    assert ei.value.code == "E_NOT_FOUND"


# ── catalogue: drift-guard + new entries + structurally-valid examples ─────


def setup_function(_fn):
    registry.reset_for_tests()


def test_new_ops_are_registered_core_ops():
    core = {name for name in registry.list_ops() if registry.op_source(name) == "core"}
    assert {"set_lane", "set_range"} <= core


def test_catalog_has_entries_for_new_ops():
    by_op = {e["op"]: e for e in CORE_OPS_CATALOG}
    for name in ("set_lane", "set_range"):
        assert name in by_op, f"catalog missing entry for {name}"
        entry = by_op[name]
        assert entry.get("group") == "time"
        assert entry.get("summary", "").strip()


def test_drift_guard_passes_with_new_ops():
    """Op registry and catalogue stay in lock-step (mirrors the drift-guard)."""
    catalog_ops = {e["op"] for e in CORE_OPS_CATALOG}
    core_ops = {n for n in registry.list_ops() if registry.op_source(n) == "core"}
    assert catalog_ops == core_ops, (
        f"catalog/registry drift — only in catalog: {catalog_ops - core_ops}; "
        f"only registered: {core_ops - catalog_ops}"
    )


def test_new_entries_have_concrete_example_and_known_error_codes():
    """Mirror test_lumenframe_catalog_examples expectations for the new entries."""
    real = set(registry.list_ops())
    codes = set(error_catalog())
    by_op = {e["op"]: e for e in CORE_OPS_CATALOG}
    for name in ("set_lane", "set_range", "retime_segment", "merge_compositions"):
        entry = by_op[name]
        example = entry.get("example")
        assert isinstance(example, dict) and example, f"{name}: example must be a non-empty dict"
        assert example.get("op") == name, f"{name}: example op must match the entry"
        assert example["op"] in real, f"{name}: example op is not registered"
        errors = entry.get("errors")
        assert isinstance(errors, list) and errors, f"{name}: errors must be a non-empty list"
        for line in errors:
            assert isinstance(line, str) and line.strip()
            code = line.split(" ", 1)[0]
            assert code in codes, f"{name}: error code {code!r} not in error_catalog()"


def test_new_examples_are_structurally_valid_against_a_populated_doc():
    """Each new example applies with no structural (shape/arg) error.

    Mirrors test_lumenframe_catalog_examples: seed the referenced ids so the only
    thing validate_patch could surface is a not-found, never a malformed envelope
    or missing arg. We assert no structural code surfaces for the new entries.
    """
    structural = {"E_ARG", "E_OP", "E_PATCH", "E_OP_UNKNOWN"}
    doc = normalize_doc({})

    def mk(lid, ltype, **kw):
        return model._normalize_layer({"type": ltype, "id": lid, **kw})

    doc["root"]["children"] = [
        mk("clip1", "video", start=0.0, duration=10.0, source_in=0.0, source_out=10.0),
    ]

    by_op = {e["op"]: e for e in CORE_OPS_CATALOG}
    for name in ("set_lane", "set_range"):
        example = by_op[name]["example"]
        result = validate_patch(doc, {"version": 1, "ops": [example]})
        offending = {err["code"] for err in result["errors"]} & structural
        assert not offending, (
            f"{name}: example raised structural error(s) {offending}: "
            f"{[e['message'] for e in result['errors']]}"
        )
