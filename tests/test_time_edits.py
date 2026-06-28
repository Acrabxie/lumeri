"""NLE time-dimension ops: ``reverse`` and ``ripple_delete``.

Both ops compose existing, already-tested machinery and add no new model fields:

* ``reverse`` plays a layer (or the sub-segment ``[t0, t1]``) BACKWARDS by
  building a ``time_remap`` curve — the output_seconds -> source_seconds curve
  compile turns into a nearest-frame ``time_map_fn``. Tests pin the actual
  RENDER-LEVEL behaviour: a synthetic source whose frame N renders the value N
  is reversed, and at output frame ``k`` the rendered source frame is exactly
  ``last - k`` (verified both through ``Layer.frame_content`` pixels and through
  the compiled ``time_map_fn``). Reversing twice restores the forward mapping;
  a sub-segment reverse only touches ``[t0, t1]``.

* ``ripple_delete`` removes a layer AND shifts every later same-lane sibling
  earlier by the deleted duration (DaVinci-style gap close), contrasted against
  the plain ``delete_layer`` which leaves the gap. Other lanes are untouched and
  ``doc_duration`` shrinks.

The catalogue carries both entries and the op<->catalogue drift-guard still
holds.
"""
from __future__ import annotations

import numpy as np
import pytest

from gemia.video.layers import Layer
from lumenframe import model, registry, timebase
from lumenframe.catalog import CORE_OPS_CATALOG
from lumenframe.compile import _time_remap_fn
from lumenframe.ops import LayerPatchError, apply_layer_patch, validate_doc
from lumenframe.model import normalize_doc


FPS = 24
# Frame N -> pixel value N * _SCALE (distinct, exactly-representable float32).
_SCALE = 1.0 / 255.0


def setup_function(_fn):
    registry.reset_for_tests()


# ── doc fixtures ────────────────────────────────────────────────────────────


def _base_doc(*, fps: int = FPS, start: float = 0.0, duration: float = 10.0) -> dict:
    """A one-video-layer doc spanning ``[start, start+duration]`` at 1x, src 0..dur."""
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


def _forward_source_frame(layer: dict, local_frame: int, fps: int = FPS) -> int:
    """Source frame a forward (constant-speed) layer-local output frame samples.

    Mirrors the resolve.py video mapping: ``src = source_in + (k/fps)*speed``,
    quantised and clamped to the ``[source_in, source_out)`` frame window.
    """
    si = float(layer["source_in"])
    so = float(layer["source_out"])
    sp = float(layer["speed"])
    sf = int((si + (float(local_frame) / fps) * sp) * fps)
    return min(max(sf, int(si * fps)), int(so * fps) - 1)


# ── synthetic gradient-over-time source for RENDER-LEVEL assertions ─────────


def _value_content_fn(frame_index: int) -> np.ndarray:
    """Source frame N -> solid 4x4 RGBA whose every channel == N * _SCALE."""
    return np.full((4, 4, 4), float(frame_index) * _SCALE, dtype=np.float32)


def _rendered_source_frame(layer: dict, output_local_frame: int) -> int:
    """Render ``output_local_frame`` through a Layer wired with the doc's time_map.

    Builds a ``gemia.video.layers.Layer`` whose ``content_fn`` is the synthetic
    gradient-over-time source and whose ``time_map_fn`` is the one compile would
    attach for this (possibly reversed) layer, then reads back which source frame
    the pixels encode. This is the true render path, not a re-derived formula.
    """
    runtime = Layer(
        id="probe",
        name="probe",
        start_frame=0,
        content_fn=_value_content_fn,
        time_map_fn=_time_remap_fn(layer, FPS),
    )
    out = runtime.frame_content(output_local_frame)
    # Solid frame: any pixel encodes N * _SCALE -> recover N.
    return int(round(float(out[0, 0, 0]) / _SCALE))


# ════════════════════════════════════════════════════════════════════════
# reverse — whole layer: render-level source-frame mapping
# ════════════════════════════════════════════════════════════════════════


def test_reverse_whole_layer_render_level_maps_k_to_last_minus_k():
    """At output frame k the RENDERED source frame == last - k (gradient source)."""
    doc = apply_layer_patch(_base_doc(), {"version": 1, "ops": [
        {"op": "reverse", "layer_id": "clip1"},
    ]})
    rev_layer = doc["root"]["children"][0]
    # A time_remap drives the reverse; constant speed was cleared to 1.0.
    assert rev_layer.get("time_remap") and rev_layer["time_remap"]["keyframes"]
    assert rev_layer["speed"] == 1.0
    # Duration is preserved (reverse never changes length).
    assert rev_layer["duration"] == 10.0

    n_frames = int(round(rev_layer["duration"] * FPS))
    last = n_frames - 1
    forward_layer = _base_doc()["root"]["children"][0]

    forward = [_forward_source_frame(forward_layer, k) for k in range(n_frames)]
    reversed_rendered = [_rendered_source_frame(rev_layer, k) for k in range(n_frames)]

    # Frame 0 of the reversed clip shows the forward clip's LAST source frame.
    assert reversed_rendered[0] == forward[last]
    assert reversed_rendered[last] == forward[0]
    # Every output frame: reversed source frame == forward source frame at (last-k).
    for k in range(n_frames):
        assert reversed_rendered[k] == forward[last - k], (
            f"frame {k}: reversed src {reversed_rendered[k]} != forward[last-k] {forward[last - k]}"
        )
    # The reversed sequence is the forward sequence read back-to-front.
    assert reversed_rendered == list(reversed(forward))


def test_reverse_whole_layer_time_map_fn_mapping_matches_render():
    """The compiled time_map_fn agrees frame-for-frame with the rendered result."""
    doc = apply_layer_patch(_base_doc(), {"version": 1, "ops": [
        {"op": "reverse", "layer_id": "clip1"},
    ]})
    rev_layer = doc["root"]["children"][0]
    tmf = _time_remap_fn(rev_layer, FPS)
    n_frames = int(round(rev_layer["duration"] * FPS))
    for k in range(n_frames):
        assert tmf(k) == _rendered_source_frame(rev_layer, k)


def test_reverse_changes_pixels_versus_forward():
    """Guard: the reverse genuinely moves pixels relative to the forward clip."""
    rev_doc = apply_layer_patch(_base_doc(), {"version": 1, "ops": [
        {"op": "reverse", "layer_id": "clip1"},
    ]})
    rev_layer = rev_doc["root"]["children"][0]
    fwd_layer = _base_doc()["root"]["children"][0]  # no remap -> identity time_map
    # Output frame 5: forward shows src 5, reverse shows a late src frame.
    assert _rendered_source_frame(fwd_layer, 5) == 5
    assert _rendered_source_frame(rev_layer, 5) != 5


# ════════════════════════════════════════════════════════════════════════
# reverse — round-trip (involution)
# ════════════════════════════════════════════════════════════════════════


def test_reverse_twice_restores_forward_mapping():
    """reverse(reverse(layer)) == the original forward source-frame mapping."""
    once = apply_layer_patch(_base_doc(), {"version": 1, "ops": [
        {"op": "reverse", "layer_id": "clip1"},
    ]})
    twice = apply_layer_patch(once, {"version": 1, "ops": [
        {"op": "reverse", "layer_id": "clip1"},
    ]})
    twice_layer = twice["root"]["children"][0]
    forward_layer = _base_doc()["root"]["children"][0]

    n_frames = int(round(twice_layer["duration"] * FPS))
    for k in range(n_frames):
        assert _rendered_source_frame(twice_layer, k) == _forward_source_frame(forward_layer, k), (
            f"frame {k}: reverse-twice diverged from forward"
        )
    # Duration is still the original.
    assert twice_layer["duration"] == 10.0


def test_reverse_twice_render_level_round_trips_pixels():
    """End-to-end: reversing twice reproduces the forward clip's pixels exactly."""
    twice = apply_layer_patch(
        apply_layer_patch(_base_doc(), {"version": 1, "ops": [
            {"op": "reverse", "layer_id": "clip1"},
        ]}),
        {"version": 1, "ops": [{"op": "reverse", "layer_id": "clip1"}]},
    )
    twice_layer = twice["root"]["children"][0]
    fwd_layer = _base_doc()["root"]["children"][0]
    n_frames = int(round(twice_layer["duration"] * FPS))
    for k in range(n_frames):
        a = Layer(id="a", name="a", start_frame=0, content_fn=_value_content_fn,
                  time_map_fn=_time_remap_fn(twice_layer, FPS)).frame_content(k)
        b = Layer(id="b", name="b", start_frame=0, content_fn=_value_content_fn,
                  time_map_fn=_time_remap_fn(fwd_layer, FPS)).frame_content(k)
        assert np.array_equal(a, b), f"frame {k}: pixels diverged after reverse-twice"


# ════════════════════════════════════════════════════════════════════════
# reverse — sub-segment only affects [t0, t1]
# ════════════════════════════════════════════════════════════════════════


def test_reverse_segment_only_reverses_the_middle():
    t0, t1 = 2.0, 6.0
    doc = apply_layer_patch(_base_doc(), {"version": 1, "ops": [
        {"op": "reverse", "layer_id": "clip1", "t0": t0, "t1": t1},
    ]})
    kids = doc["root"]["children"]
    # Interior segment => three pieces: head, reversed middle, tail.
    assert len(kids) == 3
    head, middle, tail = kids
    assert head["start"] == 0.0 and head["duration"] == 2.0
    assert middle["start"] == t0 and middle["duration"] == (t1 - t0)
    assert tail["start"] == t1 and tail["duration"] == 4.0

    # Only the middle carries a reversing time_remap; head & tail are untouched.
    assert not head.get("time_remap")
    assert not tail.get("time_remap")
    assert middle.get("time_remap") and middle["time_remap"]["keyframes"]

    # Timeline length is preserved (reverse never ripples).
    assert model.doc_duration(doc) == 10.0


def test_reverse_segment_middle_plays_backwards_render_level():
    t0, t1 = 2.0, 6.0
    doc = apply_layer_patch(_base_doc(), {"version": 1, "ops": [
        {"op": "reverse", "layer_id": "clip1", "t0": t0, "t1": t1},
    ]})
    middle = doc["root"]["children"][1]
    seg_frames = int(round(middle["duration"] * FPS))
    rendered = [_rendered_source_frame(middle, k) for k in range(seg_frames)]
    # Strictly decreasing source frames over the segment == playing backwards.
    assert all(rendered[i] > rendered[i + 1] for i in range(seg_frames - 1)), rendered


def test_reverse_segment_via_frames_matches_seconds():
    by_sec = apply_layer_patch(_base_doc(), {"version": 1, "ops": [
        {"op": "reverse", "layer_id": "clip1", "t0": 2.0, "t1": 6.0},
    ]})
    by_frame = apply_layer_patch(_base_doc(), {"version": 1, "ops": [
        {"op": "reverse", "layer_id": "clip1", "frame0": 48, "frame1": 144},
    ]})
    sec_mids = [c["start"] for c in by_sec["root"]["children"]]
    frame_mids = [c["start"] for c in by_frame["root"]["children"]]
    assert sec_mids == frame_mids == [0.0, 2.0, 6.0]


# ── reverse validation ──────────────────────────────────────────────────────


def test_reverse_missing_layer_is_not_found():
    with pytest.raises(LayerPatchError) as ei:
        apply_layer_patch(_base_doc(), {"version": 1, "ops": [
            {"op": "reverse", "layer_id": "ghost"},
        ]})
    assert ei.value.code == "E_NOT_FOUND"


def test_reverse_missing_layer_id_is_arg():
    with pytest.raises(LayerPatchError) as ei:
        apply_layer_patch(_base_doc(), {"version": 1, "ops": [
            {"op": "reverse"},
        ]})
    assert ei.value.code == "E_ARG"


def test_reverse_only_one_edge_is_arg():
    with pytest.raises(LayerPatchError) as ei:
        apply_layer_patch(_base_doc(), {"version": 1, "ops": [
            {"op": "reverse", "layer_id": "clip1", "t0": 2.0},
        ]})
    assert ei.value.code == "E_ARG"


def test_reverse_inverted_range_is_range():
    with pytest.raises(LayerPatchError) as ei:
        apply_layer_patch(_base_doc(), {"version": 1, "ops": [
            {"op": "reverse", "layer_id": "clip1", "t0": 6.0, "t1": 2.0},
        ]})
    assert ei.value.code == "E_RANGE"


def test_reverse_range_outside_layer_is_range():
    with pytest.raises(LayerPatchError) as ei:
        apply_layer_patch(_base_doc(duration=5.0), {"version": 1, "ops": [
            {"op": "reverse", "layer_id": "clip1", "t0": 1.0, "t1": 9.0},
        ]})
    assert ei.value.code == "E_RANGE"


def test_reverse_result_passes_validate_doc():
    for ops in (
        [{"op": "reverse", "layer_id": "clip1"}],
        [{"op": "reverse", "layer_id": "clip1", "t0": 2.0, "t1": 6.0}],
    ):
        doc = apply_layer_patch(_base_doc(), {"version": 1, "ops": ops})
        validate_doc(doc)  # raises on any structural violation


# ════════════════════════════════════════════════════════════════════════
# ripple_delete — close the gap by shifting later same-lane siblings
# ════════════════════════════════════════════════════════════════════════


def _abutting_doc() -> dict:
    """Lane-0: a[0,2] b[2,5] c[5,7]; plus lane-1 sibling x[1,5]."""
    doc = normalize_doc({})
    doc["canvas"]["fps"] = FPS

    def mk(lid, start, dur, lane=0):
        return model._normalize_layer({
            "type": "video", "id": lid, "start": start, "duration": dur,
            "source_in": 0.0, "source_out": dur, "speed": 1.0, "lane": lane,
        })

    doc["root"]["children"] = [
        mk("a", 0.0, 2.0),
        mk("b", 2.0, 3.0),
        mk("c", 5.0, 2.0),
        mk("x", 1.0, 4.0, lane=1),
    ]
    doc["root"]["duration"] = model.doc_duration(doc)
    return doc


def _starts(doc: dict) -> dict[str, float]:
    return {c["id"]: c["start"] for c in doc["root"]["children"]}


def test_ripple_delete_shifts_later_same_lane_siblings_earlier():
    doc = apply_layer_patch(_abutting_doc(), {"version": 1, "ops": [
        {"op": "ripple_delete", "layer_id": "b"},
    ]})
    starts = _starts(doc)
    assert "b" not in starts
    assert starts["a"] == 0.0           # before the cut: unchanged
    assert starts["c"] == 2.0           # later same-lane: shifted earlier by b's 3.0s
    assert starts["x"] == 1.0           # other lane: untouched


def test_ripple_delete_shrinks_doc_duration():
    before = model.doc_duration(_abutting_doc())
    doc = apply_layer_patch(_abutting_doc(), {"version": 1, "ops": [
        {"op": "ripple_delete", "layer_id": "b"},
    ]})
    after = model.doc_duration(doc)
    assert after < before
    # c now ends at 2.0+2.0 = 4.0; x (lane1) ends at 1.0+4.0 = 5.0 -> drives extent.
    assert after == 5.0


def test_ripple_delete_other_lane_unaffected():
    """A later sibling on a DIFFERENT lane keeps its start (lanes are independent)."""
    doc = apply_layer_patch(_abutting_doc(), {"version": 1, "ops": [
        {"op": "ripple_delete", "layer_id": "b"},
    ]})
    assert _starts(doc)["x"] == 1.0


def test_ripple_delete_passes_validate_doc():
    doc = apply_layer_patch(_abutting_doc(), {"version": 1, "ops": [
        {"op": "ripple_delete", "layer_id": "b"},
    ]})
    validate_doc(doc)


def test_ripple_delete_contrasts_plain_delete_layer():
    """Plain delete leaves the gap; ripple_delete closes it."""
    plain = apply_layer_patch(_abutting_doc(), {"version": 1, "ops": [
        {"op": "delete_layer", "layer_id": "b"},
    ]})
    rippled = apply_layer_patch(_abutting_doc(), {"version": 1, "ops": [
        {"op": "ripple_delete", "layer_id": "b"},
    ]})
    # Same layers survive in both.
    assert set(_starts(plain)) == set(_starts(rippled)) == {"a", "c", "x"}
    # delete_layer leaves c where it was (gap); ripple_delete pulls it in.
    assert _starts(plain)["c"] == 5.0
    assert _starts(rippled)["c"] == 2.0
    assert model.doc_duration(rippled) < model.doc_duration(plain)


def test_ripple_delete_missing_layer_is_not_found():
    with pytest.raises(LayerPatchError) as ei:
        apply_layer_patch(_abutting_doc(), {"version": 1, "ops": [
            {"op": "ripple_delete", "layer_id": "ghost"},
        ]})
    assert ei.value.code == "E_NOT_FOUND"


def test_ripple_delete_missing_layer_id_is_arg():
    with pytest.raises(LayerPatchError) as ei:
        apply_layer_patch(_abutting_doc(), {"version": 1, "ops": [
            {"op": "ripple_delete"},
        ]})
    assert ei.value.code == "E_ARG"


# ════════════════════════════════════════════════════════════════════════
# catalogue + drift-guard
# ════════════════════════════════════════════════════════════════════════


def test_catalog_has_both_entries():
    by_op = {e["op"]: e for e in CORE_OPS_CATALOG}
    for op in ("reverse", "ripple_delete"):
        assert op in by_op, f"catalog missing {op}"
        entry = by_op[op]
        assert entry.get("summary")
        assert isinstance(entry.get("example"), dict) and entry["example"].get("op") == op
        assert isinstance(entry.get("errors"), list) and entry["errors"]


def test_catalog_registry_drift_guard_still_holds():
    catalog_ops = {e["op"] for e in CORE_OPS_CATALOG}
    core_ops = {n for n in registry.list_ops() if registry.op_source(n) == "core"}
    assert catalog_ops == core_ops, (
        f"drift — only in catalog: {catalog_ops - core_ops}; "
        f"only registered: {core_ops - catalog_ops}"
    )
    assert "reverse" in core_ops and "ripple_delete" in core_ops
