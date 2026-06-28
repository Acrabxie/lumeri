"""``retime_segment`` — one op to speed-change a specified sub-range of a layer.

The op is pure sugar over the already-tested primitives ``split`` + ``set_speed``:
it snaps ``t0`` / ``t1`` to frame boundaries, splits the layer at both edges so the
middle ``[t0, t1]`` piece becomes its own layer, then ``set_speed`` on that middle
piece. These tests pin:

* the resulting structure (3 layers when both edges are interior);
* the middle piece's **source-frame mapping** equals a reference built directly
  from ``split`` + ``split`` + ``set_speed`` (same numbers, frame-for-frame);
* ``t0`` / ``t1`` land on exact frame boundaries (frame-accurate cuts);
* ``E_RANGE`` when the range is outside / empty / inverted, ``E_ARG`` for a
  non-positive speed;
* the result document passes ``validate_doc``;
* the catalogue carries the ``retime_segment`` entry and the op<->catalogue
  drift-guard still holds.
"""
from __future__ import annotations

import pytest

from lumenframe import model, registry, timebase
from lumenframe.catalog import CORE_OPS_CATALOG
from lumenframe.ops import LayerPatchError, apply_layer_patch, validate_doc
from lumenframe.model import normalize_doc


FPS = 24


def setup_function(_fn):
    registry.reset_for_tests()


def _base_doc(*, fps: int = FPS, start: float = 0.0, duration: float = 10.0) -> dict:
    """A one-video-layer doc whose layer spans ``[start, start+duration]`` at 1x."""
    doc = normalize_doc({})
    doc["canvas"]["fps"] = fps
    doc["root"]["children"] = [
        model._normalize_layer({
            "type": "video",
            "id": "clip1",
            "start": start,
            "duration": duration,
            "source_in": 0.0,
            "source_out": duration,
            "speed": 1.0,
        })
    ]
    doc["root"]["duration"] = start + duration
    return doc


def _source_frame(layer: dict, local_frame: int, fps: int = FPS) -> int:
    """The source frame a layer-local output frame samples (resolve.py formula).

    Mirrors ``lumenframe.resolve``'s constant-speed video mapping exactly:
    ``source_time = source_in + (local_frame/fps) * speed`` then clamped to the
    layer's ``[source_in, source_out)`` frame window.
    """
    source_in = float(layer["source_in"])
    source_out = float(layer["source_out"])
    speed = float(layer["speed"])
    source_time = source_in + (float(local_frame) / fps) * speed
    sf = int(source_time * fps)
    return min(max(sf, int(source_in * fps)), int(source_out * fps) - 1)


def _layer_at_start(doc: dict, t0: float) -> dict:
    """The root child whose start sits on ``t0`` (the isolated middle piece)."""
    for child in doc["root"]["children"]:
        if abs(float(child["start"]) - t0) <= timebase.FRAME_EPS:
            return child
    raise AssertionError(f"no layer starts at {t0}")


# ── structure + source-frame mapping vs a split+set_speed reference ─────────


def test_three_layer_structure_and_middle_plays_at_2x():
    t0, t1, speed = 2.0, 6.0, 2.0
    doc = apply_layer_patch(_base_doc(), {"version": 1, "ops": [
        {"op": "retime_segment", "layer_id": "clip1", "t0": t0, "t1": t1, "speed": speed},
    ]})
    kids = doc["root"]["children"]
    # Interior segment => exactly three pieces: head (1x), middle (speed), tail (1x).
    assert len(kids) == 3
    head, middle, tail = kids
    assert head["speed"] == 1.0 and tail["speed"] == 1.0
    assert middle["speed"] == speed
    assert head["start"] == 0.0 and middle["start"] == t0
    # The middle covers source [t0, t1] (4s) compressed to 2s of output at 2x.
    assert middle["source_in"] == t0 and middle["source_out"] == t1
    assert middle["duration"] == pytest.approx((t1 - t0) / speed)

    # Frame-for-frame, the middle plays its source at ~2x: it starts on the t0
    # source frame and spans ~speed * output_frames of source. (Per-frame deltas
    # carry int()-quantisation noise around the 2x slope, so we pin the endpoints
    # and the average slope rather than an exact constant delta — the exact
    # frame-for-frame equality with a hand-built reference is asserted below.)
    n = int(round(middle["duration"] * FPS))
    mapping = [_source_frame(middle, i) for i in range(n)]
    assert mapping[0] == int(t0 * FPS)
    src_span = mapping[-1] - mapping[0]
    assert src_span == pytest.approx(speed * (n - 1), abs=2)


def test_middle_source_mapping_equals_split_set_speed_reference():
    """The op must compose to the SAME numbers as split+split+set_speed by hand."""
    t0, t1, speed = 2.0, 6.0, 2.0

    seg = apply_layer_patch(_base_doc(), {"version": 1, "ops": [
        {"op": "retime_segment", "layer_id": "clip1", "t0": t0, "t1": t1, "speed": speed},
    ]})

    # Reference: cut the back edge first, then the front edge (same order the op
    # uses), then set_speed on the isolated middle piece.
    ref = apply_layer_patch(_base_doc(), {"version": 1, "ops": [
        {"op": "split", "layer_id": "clip1", "at_time": t1},
        {"op": "split", "layer_id": "clip1", "at_time": t0},
    ]})
    ref_mid_id = _layer_at_start(ref, t0)["id"]
    ref = apply_layer_patch(ref, {"version": 1, "ops": [
        {"op": "set_speed", "layer_id": ref_mid_id, "speed": speed},
    ]})

    seg_mid = _layer_at_start(seg, t0)
    ref_mid = _layer_at_start(ref, t0)

    # Same time-model numbers on the middle piece.
    fields = ("start", "duration", "source_in", "source_out", "speed")
    assert {k: seg_mid[k] for k in fields} == {k: ref_mid[k] for k in fields}

    # Same source-frame mapping, frame for frame.
    n = int(round(seg_mid["duration"] * FPS))
    assert n > 0
    seg_map = [_source_frame(seg_mid, i) for i in range(n)]
    ref_map = [_source_frame(ref_mid, i) for i in range(n)]
    assert seg_map == ref_map


def test_edges_land_on_frame_boundaries():
    """Off-grid t0/t1 are snapped to exact frame boundaries before cutting."""
    # 2.013s and 5.97s at 24fps snap to frames 48 (2.0s) and 143 (5.958333s).
    t0_raw, t1_raw = 2.013, 5.97
    doc = apply_layer_patch(_base_doc(), {"version": 1, "ops": [
        {"op": "retime_segment", "layer_id": "clip1", "t0": t0_raw, "t1": t1_raw, "speed": 2.0},
    ]})
    middle = next(k for k in doc["root"]["children"] if k["speed"] == 2.0)
    t0_snapped = timebase.snap_seconds(t0_raw, FPS)
    t1_snapped = timebase.snap_seconds(t1_raw, FPS)

    # The middle starts exactly on a frame boundary (snapping is idempotent).
    assert middle["start"] == pytest.approx(t0_snapped)
    assert timebase.snap_seconds(middle["start"], FPS) == pytest.approx(middle["start"])
    # Its source window is the snapped span (1x head feeds source up to t0).
    assert middle["source_in"] == pytest.approx(t0_snapped)
    assert middle["source_out"] == pytest.approx(t1_snapped)
    # And the cut points are integer frame indices.
    assert timebase.to_frame(middle["start"], FPS) == int(round(t0_snapped * FPS))
    assert timebase.to_frame(middle["source_out"], FPS) == int(round(t1_snapped * FPS))


# ── boundary segments (touch a layer edge) ─────────────────────────────────


def test_segment_touching_start_edge_makes_two_layers():
    doc = apply_layer_patch(_base_doc(start=1.0, duration=10.0), {"version": 1, "ops": [
        {"op": "retime_segment", "layer_id": "clip1", "t0": 1.0, "t1": 5.0, "speed": 2.0},
    ]})
    kids = doc["root"]["children"]
    assert len(kids) == 2  # no head split: t0 == layer start
    seg, tail = kids
    assert seg["start"] == 1.0 and seg["speed"] == 2.0
    assert seg["source_in"] == 0.0 and seg["source_out"] == pytest.approx(4.0)
    assert tail["speed"] == 1.0


def test_segment_touching_end_edge_makes_two_layers():
    doc = apply_layer_patch(_base_doc(start=1.0, duration=10.0), {"version": 1, "ops": [
        {"op": "retime_segment", "layer_id": "clip1", "t0": 5.0, "t1": 11.0, "speed": 0.5},
    ]})
    kids = doc["root"]["children"]
    assert len(kids) == 2  # no tail split: t1 == layer end
    head, seg = kids
    assert head["speed"] == 1.0
    assert seg["start"] == 5.0 and seg["speed"] == 0.5
    # source [t0..end] = [4..10] (6s) stretched to 12s of output at 0.5x.
    assert seg["source_in"] == pytest.approx(4.0) and seg["source_out"] == pytest.approx(10.0)
    assert seg["duration"] == pytest.approx(12.0)


def test_full_range_degenerates_to_plain_set_speed():
    doc = apply_layer_patch(_base_doc(start=1.0, duration=10.0), {"version": 1, "ops": [
        {"op": "retime_segment", "layer_id": "clip1", "t0": 1.0, "t1": 11.0, "speed": 2.0},
    ]})
    kids = doc["root"]["children"]
    assert len(kids) == 1  # whole layer => no cuts, just set_speed
    only = kids[0]
    assert only["id"] == "clip1" and only["speed"] == 2.0
    assert only["duration"] == pytest.approx(5.0)


# ── validation ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("t0,t1", [
    (-0.5, 5.0),   # starts before the layer
    (5.0, 20.0),   # ends past the layer
    (-1.0, 20.0),  # both outside
])
def test_range_outside_layer_raises_E_RANGE(t0, t1):
    with pytest.raises(LayerPatchError) as exc:
        apply_layer_patch(_base_doc(), {"version": 1, "ops": [
            {"op": "retime_segment", "layer_id": "clip1", "t0": t0, "t1": t1, "speed": 2.0},
        ]})
    assert exc.value.code == "E_RANGE"


@pytest.mark.parametrize("t0,t1", [(5.0, 5.0), (6.0, 4.0)])
def test_empty_or_inverted_range_raises_E_RANGE(t0, t1):
    with pytest.raises(LayerPatchError) as exc:
        apply_layer_patch(_base_doc(), {"version": 1, "ops": [
            {"op": "retime_segment", "layer_id": "clip1", "t0": t0, "t1": t1, "speed": 2.0},
        ]})
    assert exc.value.code == "E_RANGE"


@pytest.mark.parametrize("speed", [0.0, -1.0, -0.25])
def test_nonpositive_speed_raises_E_ARG(speed):
    with pytest.raises(LayerPatchError) as exc:
        apply_layer_patch(_base_doc(), {"version": 1, "ops": [
            {"op": "retime_segment", "layer_id": "clip1", "t0": 2.0, "t1": 6.0, "speed": speed},
        ]})
    assert exc.value.code == "E_ARG"


def test_missing_args_raise_E_ARG():
    for op in (
        {"op": "retime_segment", "t0": 2.0, "t1": 6.0, "speed": 2.0},               # no layer_id
        {"op": "retime_segment", "layer_id": "clip1", "t1": 6.0, "speed": 2.0},     # no t0
        {"op": "retime_segment", "layer_id": "clip1", "t0": 2.0, "speed": 2.0},     # no t1
        {"op": "retime_segment", "layer_id": "clip1", "t0": 2.0, "t1": 6.0},        # no speed
    ):
        with pytest.raises(LayerPatchError) as exc:
            apply_layer_patch(_base_doc(), {"version": 1, "ops": [op]})
        assert exc.value.code == "E_ARG"


def test_unknown_layer_raises_E_NOT_FOUND():
    with pytest.raises(LayerPatchError) as exc:
        apply_layer_patch(_base_doc(), {"version": 1, "ops": [
            {"op": "retime_segment", "layer_id": "ghost", "t0": 2.0, "t1": 6.0, "speed": 2.0},
        ]})
    assert exc.value.code == "E_NOT_FOUND"


def test_result_doc_passes_validate_doc():
    doc = apply_layer_patch(_base_doc(), {"version": 1, "ops": [
        {"op": "retime_segment", "layer_id": "clip1", "t0": 2.0, "t1": 6.0, "speed": 2.0},
    ]})
    # apply_layer_patch already validates; assert it stays valid explicitly too,
    # and that the ids are unique (split must have minted fresh ones).
    validate_doc(doc)
    ids = [k["id"] for k in doc["root"]["children"]]
    assert len(ids) == len(set(ids)) == 3


# ── catalogue entry + op<->catalogue drift guard ───────────────────────────


def test_catalog_has_retime_segment_entry():
    entry = next((e for e in CORE_OPS_CATALOG if e["op"] == "retime_segment"), None)
    assert entry is not None, "retime_segment missing from CORE_OPS_CATALOG"
    assert entry["group"] == "time"
    example = entry["example"]
    assert example["op"] == "retime_segment"
    # The documented example must actually apply cleanly.
    result = apply_layer_patch(_base_doc(), {"version": 1, "ops": [example]})
    validate_doc(result)


def test_op_catalog_drift_guard_still_passes():
    """Every registered core op has a catalogue entry and vice-versa (incl. ours)."""
    catalog_ops = {e["op"] for e in CORE_OPS_CATALOG}
    core_ops = {n for n in registry.list_ops() if registry.op_source(n) == "core"}
    assert "retime_segment" in catalog_ops
    assert "retime_segment" in core_ops
    assert catalog_ops == core_ops, (
        f"catalog/registry drift — only in catalog: {catalog_ops - core_ops}; "
        f"only registered: {core_ops - catalog_ops}"
    )
