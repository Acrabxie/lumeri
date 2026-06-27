"""Frame-based temporal editing core — time-remap + frame-precise trim/split/kf.

Two halves, both pinned to concrete frame/pixel numbers:

A. **time-remap / speed-ramp** (``set_time_remap`` op + ``compile`` ``time_map_fn``)
   A *synthetic source* is used where source frame ``N`` paints a flat grey of
   value ``N/255``. That makes the rendered centre pixel a direct read-out of
   *which source frame the compiler sampled* at each output frame — so a freeze
   shows a constant frame number, a 2x ramp shows ``src == 2*out``, and a reverse
   curve shows a descending frame number. No tolerance games: the byte the
   compiler picked is the byte we assert.

B. **frame-precise edits** (``trim`` / ``split`` / ``set_keyframe`` accept frames)
   The document stays seconds-canonical, so a frame-addressed edit must produce a
   *byte-identical* document to the equivalent seconds edit (``at_frame=K`` ==
   ``at_time=K/fps``), and a frame edge must land on the matching source time.
"""
from __future__ import annotations

import copy

import numpy as np
import pytest

from lumenframe import apply_layer_patch, empty_doc, model
from lumenframe.compile import compile_to_layer_stack
from lumenframe.ops import LayerPatchError, validate_patch


FPS = 10.0


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def base_doc(w=16, h=12, fps=FPS):
    return empty_doc(width=w, height=h, fps=fps)


def center_px(frame):
    return frame[frame.shape[0] // 2, frame.shape[1] // 2]


def gray_byte(frame) -> int:
    """The 0..255 grey value at the centre pixel = the sampled source frame N."""
    return int(round(float(center_px(frame)[0]) * 255.0))


def synthetic_source_resolver(layer, ctx):
    """content_fn(source_local_frame) -> flat grey of value (frame/255).

    The compiler attaches the time_remap ``time_map_fn`` ahead of this, so the
    argument handed here is already the *source-local* frame. Frame N paints
    ``N/255`` across an opaque canvas, making the rendered pixel a read-out of N.
    """
    if str(layer.get("type")) != "video":
        return None

    def content_fn(source_local_frame: int):
        frame = np.zeros((ctx.height, ctx.width, 4), dtype=np.float32)
        frame[..., :3] = float(int(source_local_frame)) / 255.0
        frame[..., 3] = 1.0
        return frame

    return content_fn


def _video_doc(duration: float, *, source_out: float | None = None):
    doc = base_doc()
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "clip", "type": "video", "asset_id": "a1",
        "start": 0.0, "duration": duration, "source_in": 0.0,
        "source_out": source_out if source_out is not None else duration,
    }))
    doc["assets"] = [{"id": "a1", "media_kind": "video", "path": "/synthetic"}]
    return doc


def _sampled_source_frames(doc, *, n_out: int) -> list[int]:
    stack = compile_to_layer_stack(doc, resolver=synthetic_source_resolver)
    return [gray_byte(stack.render_frame(i)) for i in range(n_out)]


# ════════════════════════════════════════════════════════════════════════
# A. time-remap / speed-ramp
# ════════════════════════════════════════════════════════════════════════


def test_remap_freeze_holds_one_source_frame():
    """A ``hold`` curve to a constant source time freezes on a single frame."""
    doc = _video_doc(1.0)  # 10 output frames at fps=10
    # Output 0..1s all sample source 0.5s -> source frame 5, regardless of output.
    doc = apply_layer_patch(doc, patch({
        "op": "set_time_remap", "layer_id": "clip",
        "keyframes": [
            {"t": 0.0, "value": 0.5, "interp": "hold"},
            {"t": 1.0, "value": 0.5, "interp": "hold"},
        ],
        "extrapolate": "hold",
    }))
    sampled = _sampled_source_frames(doc, n_out=10)
    assert sampled == [5] * 10, sampled


def test_remap_2x_speed_source_is_twice_output():
    """A linear curve t->2t ramps the source at double speed: src == 2*out."""
    doc = _video_doc(1.0, source_out=2.0)  # 10 output frames cover 2s of source
    doc = apply_layer_patch(doc, patch({
        "op": "set_time_remap", "layer_id": "clip",
        "keyframes": [
            {"t": 0.0, "value": 0.0, "interp": "linear"},
            {"t": 1.0, "value": 2.0, "interp": "linear"},
        ],
    }))
    sampled = _sampled_source_frames(doc, n_out=10)
    # out frame i -> out_sec i/10 -> src_sec 2*(i/10) -> src frame round(2*i)=2i.
    assert sampled == [2 * i for i in range(10)], sampled


def test_remap_reverse_descends_source_frames():
    """A descending curve plays the source backwards: source frame counts down."""
    doc = _video_doc(1.0)  # 10 output frames at fps=10
    doc = apply_layer_patch(doc, patch({
        "op": "set_time_remap", "layer_id": "clip",
        "keyframes": [
            {"t": 0.0, "value": 0.9, "interp": "linear"},
            {"t": 1.0, "value": 0.0, "interp": "linear"},
        ],
    }))
    sampled = _sampled_source_frames(doc, n_out=10)
    # out i -> src_sec 0.9 - 0.9*(i/10) -> src frame round((9 - 0.9*i)).
    expected = [int(round((0.9 - 0.9 * (i / 10.0)) * FPS)) for i in range(10)]
    assert sampled == expected, (sampled, expected)
    assert sampled == sorted(sampled, reverse=True)  # strictly descending order


def test_remap_clears_constant_speed_and_overrides_it():
    """A remap clears any prior non-unit speed (documented mutual exclusion)."""
    doc = _video_doc(1.0, source_out=2.0)
    doc = apply_layer_patch(doc, patch({"op": "set_speed", "layer_id": "clip", "speed": 2.0}))
    assert model.find_layer(doc, "clip")["speed"] == pytest.approx(2.0)
    doc = apply_layer_patch(doc, patch({
        "op": "set_time_remap", "layer_id": "clip",
        "keyframes": [
            {"t": 0.0, "value": 0.0, "interp": "linear"},
            {"t": 1.0, "value": 1.0, "interp": "linear"},
        ],
    }))
    clip = model.find_layer(doc, "clip")
    assert clip["speed"] == pytest.approx(1.0)  # speed was cleared
    assert clip["time_remap"]["keyframes"][0]["value"] == pytest.approx(0.0)
    # The remap (not the old 2x speed) drives sampling: t->t means src == out.
    sampled = _sampled_source_frames(doc, n_out=10)
    assert sampled == list(range(10)), sampled


def test_remap_extrapolate_hold_clamps_outside_span():
    """Output beyond the last keyframe holds the final source frame."""
    doc = _video_doc(1.0)  # 10 output frames; curve only spans 0..0.5s
    doc = apply_layer_patch(doc, patch({
        "op": "set_time_remap", "layer_id": "clip",
        "keyframes": [
            {"t": 0.0, "value": 0.0, "interp": "linear"},
            {"t": 0.5, "value": 0.5, "interp": "linear"},
        ],
        "extrapolate": "hold",
    }))
    sampled = _sampled_source_frames(doc, n_out=10)
    # 0..0.5s ramps 0->5; 0.5..1s clamps to source 0.5s = frame 5.
    assert sampled[:6] == [0, 1, 2, 3, 4, 5], sampled
    assert sampled[6:] == [5, 5, 5, 5], sampled


def test_remap_normalizes_and_round_trips_on_doc():
    """The remap is stored top-level (not buried in props) and re-normalises stably."""
    doc = _video_doc(1.0)
    doc = apply_layer_patch(doc, patch({
        "op": "set_time_remap", "layer_id": "clip",
        "keyframes": [
            {"t": 1.0, "value": 1.0, "interp": "linear"},
            {"t": 0.0, "value": 0.0, "interp": "hold"},  # out of order on input
        ],
    }))
    clip = model.find_layer(doc, "clip")
    assert "time_remap" in clip and "time_remap" not in clip.get("props", {})
    ts = [k["t"] for k in clip["time_remap"]["keyframes"]]
    assert ts == sorted(ts)  # sorted by output time
    # Re-normalising an already-normalised doc is a fixed point.
    assert model.normalize_doc(doc) == model.normalize_doc(model.normalize_doc(doc))


def test_remap_curve_evaluator_unit():
    """eval_time_remap maps output seconds -> source seconds per interp/extrapolate."""
    remap = model._normalize_time_remap({
        "keyframes": [
            {"t": 0.0, "value": 0.0, "interp": "linear"},
            {"t": 2.0, "value": 1.0, "interp": "linear"},
        ],
        "extrapolate": "hold",
    })
    assert model.eval_time_remap(remap, 0.0) == pytest.approx(0.0)
    assert model.eval_time_remap(remap, 1.0) == pytest.approx(0.5)  # halfway
    assert model.eval_time_remap(remap, 2.0) == pytest.approx(1.0)
    assert model.eval_time_remap(remap, 5.0) == pytest.approx(1.0)  # hold past end
    assert model.eval_time_remap(remap, -3.0) == pytest.approx(0.0)  # hold before start

    hold = model._normalize_time_remap({
        "keyframes": [{"t": 0.0, "value": 0.5, "interp": "hold"},
                      {"t": 2.0, "value": 0.9, "interp": "linear"}],
    })
    assert model.eval_time_remap(hold, 1.0) == pytest.approx(0.5)  # frozen on left kf


# ── time-remap op validation ─────────────────────────────────────────────


def test_set_time_remap_requires_nonempty_keyframes():
    doc = _video_doc(1.0)
    res = validate_patch(doc, patch({"op": "set_time_remap", "layer_id": "clip", "keyframes": []}))
    assert not res["ok"]
    assert {e["code"] for e in res["errors"]} == {"E_ARG"}


def test_set_time_remap_rejects_keyframe_without_value():
    doc = _video_doc(1.0)
    res = validate_patch(doc, patch({
        "op": "set_time_remap", "layer_id": "clip",
        "keyframes": [{"t": 0.0}],  # missing value
    }))
    assert not res["ok"]
    assert "E_ARG" in {e["code"] for e in res["errors"]}


def test_set_time_remap_rejects_unknown_interp():
    doc = _video_doc(1.0)
    res = validate_patch(doc, patch({
        "op": "set_time_remap", "layer_id": "clip",
        "keyframes": [{"t": 0.0, "value": 0.0, "interp": "bezier"}],
    }))
    assert not res["ok"]
    assert "E_ARG" in {e["code"] for e in res["errors"]}


def test_set_time_remap_rejects_ambiguous_curve():
    """Two keyframes at the same output t with different source values are E_RANGE."""
    doc = _video_doc(1.0)
    res = validate_patch(doc, patch({
        "op": "set_time_remap", "layer_id": "clip",
        "keyframes": [{"t": 0.5, "value": 0.0}, {"t": 0.5, "value": 1.0}],
    }))
    assert not res["ok"]
    assert "E_RANGE" in {e["code"] for e in res["errors"]}


# ════════════════════════════════════════════════════════════════════════
# B. frame-precise trim / split / keyframe (seconds-canonical doc)
# ════════════════════════════════════════════════════════════════════════


def test_split_at_frame_equals_at_time_byte_identical():
    """split at_frame=K produces a byte-identical doc to at_time=K/fps."""
    K = 3
    base = _video_doc(1.0, source_out=1.0)

    by_frame = apply_layer_patch(copy.deepcopy(base),
                                 patch({"op": "split", "layer_id": "clip", "at_frame": K}))
    by_time = apply_layer_patch(copy.deepcopy(base),
                                patch({"op": "split", "layer_id": "clip", "at_time": K / FPS}))

    # Selection carries fresh ids (uuid) for the right half, so compare structure
    # minus the volatile ids; the *time* fields must be byte-identical.
    def strip_ids(d):
        d = copy.deepcopy(d)
        for n in model.walk(d["root"]):
            n["id"] = "ID"
            n["name"] = "N"
        d["selection"] = ["ID", "ID"]
        d["id"] = "DOC"
        return d

    assert strip_ids(by_frame) == strip_ids(by_time)
    # And concretely: the cut lands at K/fps seconds on the timeline.
    left = by_frame["root"]["children"][0]
    assert left["duration"] == pytest.approx(K / FPS)
    assert left["source_out"] == pytest.approx(K / FPS)


def test_trim_frame_edges_map_to_source_right():
    """trim with frame_in/frame_out lands on the matching source seconds."""
    doc = _video_doc(1.0, source_out=1.0)
    # Trim the in edge to frame 2 (=0.2s): start advances, source_in advances by 0.2.
    doc = apply_layer_patch(doc, patch({
        "op": "trim", "layer_id": "clip", "edge": "in", "frame_in": 2,
    }))
    clip = model.find_layer(doc, "clip")
    assert clip["start"] == pytest.approx(0.2)
    assert clip["source_in"] == pytest.approx(0.2)
    # Trim the out edge to frame 7 (=0.7s): new end at 0.7, duration 0.5.
    doc = apply_layer_patch(doc, patch({
        "op": "trim", "layer_id": "clip", "edge": "out", "frame_out": 7,
    }))
    clip = model.find_layer(doc, "clip")
    assert clip["duration"] == pytest.approx(0.5)  # 0.7 - 0.2
    assert clip["source_out"] == pytest.approx(0.7)  # source_in 0.2 + 0.5 * speed(1)


def test_trim_frame_in_equals_seconds_to():
    """frame_in=K is byte-identical to to=K/fps for the same edge."""
    base = _video_doc(1.0, source_out=1.0)
    a = apply_layer_patch(copy.deepcopy(base),
                          patch({"op": "trim", "layer_id": "clip", "edge": "in", "frame_in": 4}))
    b = apply_layer_patch(copy.deepcopy(base),
                          patch({"op": "trim", "layer_id": "clip", "edge": "in", "to": 4 / FPS}))
    assert a == b


def test_set_keyframe_frame_equals_time():
    """set_keyframe frame=K writes the same keyframe as t=K/fps (seconds-canonical)."""
    base = _video_doc(1.0)
    by_frame = apply_layer_patch(copy.deepcopy(base), patch({
        "op": "set_keyframe", "layer_id": "clip", "property": "opacity",
        "frame": 5, "value": 0.5,
    }))
    by_time = apply_layer_patch(copy.deepcopy(base), patch({
        "op": "set_keyframe", "layer_id": "clip", "property": "opacity",
        "t": 5 / FPS, "value": 0.5,
    }))
    assert by_frame == by_time
    kf = model.find_layer(by_frame, "clip")["keyframes"]["opacity"][0]
    assert kf["t"] == pytest.approx(0.5)  # 5/10, stored in seconds


def test_remove_keyframe_accepts_frame():
    doc = _video_doc(1.0)
    doc = apply_layer_patch(doc, patch({
        "op": "set_keyframe", "layer_id": "clip", "property": "opacity",
        "frame": 5, "value": 0.5,
    }))
    doc = apply_layer_patch(doc, patch({
        "op": "remove_keyframe", "layer_id": "clip", "property": "opacity", "frame": 5,
    }))
    assert "opacity" not in model.find_layer(doc, "clip").get("keyframes", {})


# ── mixing seconds + frame for the same edge is rejected ──────────────────


def test_split_mixing_at_time_and_at_frame_is_e_arg():
    doc = _video_doc(1.0)
    res = validate_patch(doc, patch({
        "op": "split", "layer_id": "clip", "at_time": 0.3, "at_frame": 3,
    }))
    assert not res["ok"]
    assert "E_ARG" in {e["code"] for e in res["errors"]}


def test_trim_mixing_to_and_frame_is_e_arg():
    doc = _video_doc(1.0)
    res = validate_patch(doc, patch({
        "op": "trim", "layer_id": "clip", "edge": "in", "to": 0.2, "frame_in": 2,
    }))
    assert not res["ok"]
    assert "E_ARG" in {e["code"] for e in res["errors"]}


def test_set_keyframe_mixing_t_and_frame_is_e_arg():
    doc = _video_doc(1.0)
    res = validate_patch(doc, patch({
        "op": "set_keyframe", "layer_id": "clip", "property": "opacity",
        "t": 0.5, "frame": 5, "value": 1.0,
    }))
    assert not res["ok"]
    assert "E_ARG" in {e["code"] for e in res["errors"]}
