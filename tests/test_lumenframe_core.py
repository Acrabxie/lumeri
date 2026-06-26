"""Core lumenframe model + LayerPatch op-vocabulary tests."""
from __future__ import annotations

import copy
import json

import pytest

from lumenframe import (
    apply_layer_patch,
    empty_doc,
    find_layer,
    find_parent,
    new_layer,
    normalize_doc,
)
from lumenframe.model import doc_duration, locate
from lumenframe.ops import LayerPatchError, validate_doc


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def make_doc():
    """Root comp with three media layers v1<v2<v3 (bottom→top)."""
    doc = empty_doc(width=1920, height=1080, fps=30)
    return apply_layer_patch(doc, patch(
        {"op": "add_layer", "id": "v1", "type": "video", "start": 0, "duration": 4},
        {"op": "add_layer", "id": "v2", "type": "image", "start": 1, "duration": 3},
        {"op": "add_layer", "id": "v3", "type": "text", "start": 2, "duration": 2, "text": "hello"},
    ))


# ── model basics ─────────────────────────────────────────────────────────


def test_empty_doc_is_valid_and_json_serialisable():
    doc = empty_doc(title="T", width=1280, height=720, fps=24)
    validate_doc(doc)
    assert doc["canvas"] == {"width": 1280, "height": 720, "fps": 24.0, "background": "#000000"}
    assert doc["root"]["type"] == "composition"
    # round-trips through JSON unchanged after normalisation
    again = normalize_doc(json.loads(json.dumps(doc)))
    assert again["root"]["id"] == doc["root"]["id"]


def test_normalize_folds_unknown_keys_into_props():
    layer = normalize_doc({"root": {"type": "composition", "children": [
        {"id": "t", "type": "text", "text": "hi", "font": "Inter"},
    ]}})["root"]["children"][0]
    assert layer["props"]["text"] == "hi"
    assert layer["props"]["font"] == "Inter"


def test_add_layer_defaults_parent_to_root_and_selects():
    doc = make_doc()
    assert [c["id"] for c in doc["root"]["children"]] == ["v1", "v2", "v3"]
    assert doc["selection"] == ["v3"]
    assert doc["root"]["children"][2]["props"]["text"] == "hello"
    assert doc_duration(doc) == 4.0


# ── layer management ─────────────────────────────────────────────────────


def test_duplicate_layer_gets_fresh_ids_and_sits_after_original():
    doc = make_doc()
    out = apply_layer_patch(doc, patch({"op": "duplicate_layer", "layer_id": "v2"}))
    ids = [c["id"] for c in out["root"]["children"]]
    assert ids[0] == "v1" and ids[1] == "v2" and ids[3] == "v3"
    dup_id = ids[2]
    assert dup_id not in {"v1", "v2", "v3"}
    assert out["selection"] == [dup_id]
    assert find_layer(out, dup_id)["start"] == 1.0


def test_delete_layer_clears_dangling_mattes():
    doc = make_doc()
    doc = apply_layer_patch(doc, patch(
        {"op": "set_mask", "layer_id": "v3", "mask": {"kind": "luma_matte", "source_layer_id": "v2"}},
    ))
    out = apply_layer_patch(doc, patch({"op": "delete_layer", "layer_id": "v2"}))
    assert find_layer(out, "v2") is None
    assert find_layer(out, "v3")["mask"] is None  # matte cleaned up, doc stays valid


def test_cannot_delete_root():
    doc = make_doc()
    with pytest.raises(LayerPatchError) as e:
        apply_layer_patch(doc, patch({"op": "delete_layer", "layer_id": "root"}))
    assert e.value.code == "E_ROOT"


def test_select_modes():
    doc = make_doc()
    doc = apply_layer_patch(doc, patch({"op": "select", "layer_ids": ["v1", "v2"]}))
    assert doc["selection"] == ["v1", "v2"]
    doc = apply_layer_patch(doc, patch({"op": "select", "layer_id": "v2", "mode": "toggle"}))
    assert doc["selection"] == ["v1"]
    doc = apply_layer_patch(doc, patch({"op": "select", "layer_id": "v3", "mode": "add"}))
    assert doc["selection"] == ["v1", "v3"]
    doc = apply_layer_patch(doc, patch({"op": "select", "mode": "clear"}))
    assert doc["selection"] == []


# ── z-order / hierarchy ──────────────────────────────────────────────────


def test_reorder_layer_to_top_and_back():
    doc = make_doc()
    out = apply_layer_patch(doc, patch({"op": "reorder_layer", "layer_id": "v1", "to": "top"}))
    assert [c["id"] for c in out["root"]["children"]] == ["v2", "v3", "v1"]
    out = apply_layer_patch(out, patch({"op": "reorder_layer", "layer_id": "v1", "to": "backward"}))
    assert [c["id"] for c in out["root"]["children"]] == ["v2", "v1", "v3"]


def test_group_then_ungroup_round_trips_timing():
    doc = make_doc()
    grouped = apply_layer_patch(doc, patch(
        {"op": "group_layers", "layer_ids": ["v2", "v3"], "name": "G"},
    ))
    comp_id = grouped["selection"][0]
    comp = find_layer(grouped, comp_id)
    assert comp["type"] == "composition" and comp["name"] == "G"
    assert comp["start"] == 1.0 and comp["duration"] == 3.0  # spans v2(1..4) & v3(2..4)
    # children re-based to group-local time
    inner = {c["id"]: c for c in comp["children"]}
    assert inner["v2"]["start"] == 0.0 and inner["v3"]["start"] == 1.0
    # v1 untouched, group took v2's old slot
    assert [c["id"] for c in grouped["root"]["children"]] == ["v1", comp_id]

    back = apply_layer_patch(grouped, patch({"op": "ungroup_layer", "layer_id": comp_id}))
    restored = {c["id"]: c for c in back["root"]["children"]}
    assert restored["v2"]["start"] == 1.0 and restored["v3"]["start"] == 2.0


def test_merge_layers_marks_merged():
    doc = make_doc()
    out = apply_layer_patch(doc, patch({"op": "merge_layers", "layer_ids": ["v1", "v2"]}))
    comp = find_layer(out, out["selection"][0])
    assert comp["merged"] is True
    assert {c["id"] for c in comp["children"]} == {"v1", "v2"}


def test_move_layer_reparents_and_guards_cycles():
    doc = make_doc()
    doc = apply_layer_patch(doc, patch({"op": "add_layer", "id": "box", "type": "composition"}))
    moved = apply_layer_patch(doc, patch({"op": "move_layer", "layer_id": "v1", "parent_id": "box"}))
    assert find_parent(moved, "v1")["id"] == "box"
    with pytest.raises(LayerPatchError) as e:
        apply_layer_patch(moved, patch({"op": "move_layer", "layer_id": "box", "parent_id": "v1"}))
    assert e.value.code in {"E_CYCLE", "E_CONTAINER"}


def test_move_layer_same_parent_reindex():
    doc = make_doc()
    out = apply_layer_patch(doc, patch({"op": "move_layer", "layer_id": "v1", "index": 2}))
    assert [c["id"] for c in out["root"]["children"]] == ["v2", "v3", "v1"]


# ── time ─────────────────────────────────────────────────────────────────


def test_set_time_and_split():
    doc = make_doc()
    doc = apply_layer_patch(doc, patch({"op": "set_time", "layer_id": "v1", "start": 0, "duration": 6}))
    doc = apply_layer_patch(doc, patch(
        {"op": "set_effect_params" , "layer_id": "v1", "effect_id": "x", "params": {}}
    )) if False else doc
    out = apply_layer_patch(doc, patch({"op": "split", "layer_id": "v1", "at_time": 2.5}))
    left = find_layer(out, "v1")
    parent, idx = locate(out, "v1")
    right = parent["children"][idx + 1]
    assert left["duration"] == 2.5 and right["start"] == 2.5 and right["duration"] == 3.5
    assert right["source_in"] == left["source_out"]
    assert out["selection"] == ["v1", right["id"]]


def test_split_outside_range_rejected():
    doc = make_doc()
    with pytest.raises(LayerPatchError) as e:
        apply_layer_patch(doc, patch({"op": "split", "layer_id": "v1", "at_time": 99}))
    assert e.value.code == "E_RANGE"


def test_trim_edges_track_source():
    doc = make_doc()
    doc = apply_layer_patch(doc, patch({"op": "set_time", "layer_id": "v1", "start": 0, "duration": 4}))
    doc = apply_layer_patch(doc, patch({"op": "trim", "layer_id": "v1", "edge": "in", "to": 1}))
    v1 = find_layer(doc, "v1")
    assert v1["start"] == 1.0 and v1["duration"] == 3.0 and v1["source_in"] == 1.0
    doc = apply_layer_patch(doc, patch({"op": "trim", "layer_id": "v1", "edge": "out", "to": 2.5}))
    v1 = find_layer(doc, "v1")
    assert v1["duration"] == 1.5 and v1["source_out"] == v1["source_in"] + 1.5


def test_set_speed_keeps_source_range():
    doc = make_doc()
    doc = apply_layer_patch(doc, patch(
        {"op": "set_time", "layer_id": "v1", "duration": 4},
        {"op": "trim", "layer_id": "v1", "edge": "out", "to": 4},  # source_out -> 4
    ))
    out = apply_layer_patch(doc, patch({"op": "set_speed", "layer_id": "v1", "speed": 2.0}))
    v1 = find_layer(out, "v1")
    assert v1["speed"] == 2.0 and v1["duration"] == 2.0  # 4s source at 2x = 2s


# ── intra-layer ──────────────────────────────────────────────────────────


def test_set_transform_and_opacity_clamp():
    doc = make_doc()
    out = apply_layer_patch(doc, patch(
        {"op": "set_transform", "layer_id": "v2", "x": 100, "scale": 1.5, "rotation": 45},
        {"op": "set_opacity", "layer_id": "v2", "opacity": 2.0},
    ))
    v2 = find_layer(out, "v2")
    assert v2["transform"]["x"] == 100 and v2["transform"]["scale_x"] == 1.5 and v2["transform"]["scale_y"] == 1.5
    assert v2["transform"]["rotation"] == 45
    assert v2["opacity"] == 1.0  # clamped


def test_color_grade_upserts_single_effect():
    doc = make_doc()
    doc = apply_layer_patch(doc, patch({"op": "color_grade", "layer_id": "v1", "saturation": 1.2}))
    doc = apply_layer_patch(doc, patch({"op": "color_grade", "layer_id": "v1", "contrast": 0.9}))
    grades = [e for e in find_layer(doc, "v1")["effects"] if e["type"] == "color_grade"]
    assert len(grades) == 1
    assert grades[0]["params"] == {"saturation": 1.2, "contrast": 0.9}


def test_effect_lifecycle():
    doc = make_doc()
    doc = apply_layer_patch(doc, patch({"op": "add_effect", "layer_id": "v1", "effect": {"id": "blur1", "type": "gaussian_blur", "params": {"radius": 3}}}))
    doc = apply_layer_patch(doc, patch({"op": "set_effect_params", "layer_id": "v1", "effect_id": "blur1", "params": {"radius": 8}}))
    assert find_layer(doc, "v1")["effects"][0]["params"]["radius"] == 8
    doc = apply_layer_patch(doc, patch({"op": "remove_effect", "layer_id": "v1", "effect_id": "blur1"}))
    assert find_layer(doc, "v1")["effects"] == []


def test_keyframes_set_and_remove():
    doc = make_doc()
    doc = apply_layer_patch(doc, patch(
        {"op": "set_keyframe", "layer_id": "v2", "property": "opacity", "t": 1.0, "value": 0.0},
        {"op": "set_keyframe", "layer_id": "v2", "property": "opacity", "t": 0.0, "value": 1.0},
    ))
    track = find_layer(doc, "v2")["keyframes"]["opacity"]
    assert [k["t"] for k in track] == [0.0, 1.0]  # sorted
    doc = apply_layer_patch(doc, patch({"op": "remove_keyframe", "layer_id": "v2", "property": "opacity", "t": 0.0}))
    assert [k["t"] for k in find_layer(doc, "v2")["keyframes"]["opacity"]] == [1.0]


# ── inter-layer ──────────────────────────────────────────────────────────


def test_set_mask_validates_track_matte_source():
    doc = make_doc()
    with pytest.raises(LayerPatchError) as e:
        apply_layer_patch(doc, patch({"op": "set_mask", "layer_id": "v3", "mask": {"kind": "alpha_matte", "source_layer_id": "ghost"}}))
    assert e.value.code == "E_NOT_FOUND"
    ok = apply_layer_patch(doc, patch({"op": "set_mask", "layer_id": "v3", "mask": {"kind": "alpha_matte", "source_layer_id": "v2"}}))
    assert find_layer(ok, "v3")["mask"]["source_layer_id"] == "v2"


def test_add_adjustment_layer():
    doc = make_doc()
    out = apply_layer_patch(doc, patch(
        {"op": "add_adjustment_layer", "id": "adj", "effects": [{"type": "color_grade", "params": {"contrast": 1.1}}]},
    ))
    adj = find_layer(out, "adj")
    assert adj["type"] == "adjustment" and adj["effects"][0]["type"] == "color_grade"


def test_add_transition():
    doc = make_doc()
    out = apply_layer_patch(doc, patch({"op": "add_transition", "layer_id": "v2", "kind": "dissolve", "duration": 0.5, "at": "both"}))
    trans = find_layer(out, "v2")["props"]["transitions"]
    assert trans["in"]["kind"] == "dissolve" and trans["out"]["duration"] == 0.5


# ── atomicity & validation ───────────────────────────────────────────────


def test_patch_is_atomic_on_failure():
    doc = make_doc()
    before = copy.deepcopy(doc)
    with pytest.raises(LayerPatchError):
        apply_layer_patch(doc, patch(
            {"op": "rename_layer", "layer_id": "v1", "name": "renamed"},
            {"op": "delete_layer", "layer_id": "does-not-exist"},  # blows up here
        ))
    assert doc == before  # caller's doc untouched; partial rename rolled back


def test_unknown_op_rejected():
    doc = make_doc()
    with pytest.raises(LayerPatchError) as e:
        apply_layer_patch(doc, patch({"op": "frobnicate", "layer_id": "v1"}))
    assert e.value.code == "E_OP_UNKNOWN"


def test_validation_catches_duplicate_ids_and_bad_container():
    doc = empty_doc()
    doc["root"]["children"] = [
        new_layer("video", id="dup"),
        new_layer("video", id="dup"),
    ]
    with pytest.raises(LayerPatchError) as e:
        validate_doc(normalize_doc(doc))
    assert e.value.code == "E_DUP_ID"

    bad = empty_doc()
    bad["root"]["children"] = [new_layer("text", id="t", children=[new_layer("video")])]
    with pytest.raises(LayerPatchError) as e2:
        validate_doc(normalize_doc(bad))
    assert e2.value.code == "E_CONTAINER"
