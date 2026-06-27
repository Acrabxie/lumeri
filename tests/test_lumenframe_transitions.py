"""Transition *rendering* at compile time.

``add_transition`` records ``props.transitions[edge] = {kind, duration}`` on a
layer but the doc itself says nothing about how to draw it. The compiler
synthesises a transient per-frame effect (without mutating the doc):

* ``fade`` / ``dissolve`` ramp the layer alpha 0->1 (in) / 1->0 (out);
* ``wipe_l/r/u/d`` reveal a growing band (covered columns/rows increase);
* ``slide`` translates the content in from the edge.

These tests sample ``render_frame`` alpha to prove the ramp and the progressive
reveal, and confirm a layer without a transition is unaffected (golden).
"""
from __future__ import annotations

import numpy as np

from lumenframe import apply_layer_patch, empty_doc
from lumenframe.compile import compile_to_layer_stack
from lumenframe.catalog import transition_kinds


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def _solid_doc(*, w=16, h=16, fps=10, duration=1.0):
    doc = empty_doc(width=w, height=h, fps=fps)
    return apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "r", "type": "solid", "color": "#FF0000",
        "start": 0.0, "duration": duration,
    }))


def _with_transition(doc, kind, duration, at):
    return apply_layer_patch(doc, patch({
        "op": "add_transition", "layer_id": "r", "kind": kind,
        "duration": duration, "at": at,
    }))


def alpha_mean(stack, frame):
    return float(np.mean(stack.render_frame(frame)[..., 3]))


def covered_columns(stack, frame):
    """Number of canvas columns that contain any opaque pixel."""
    a = stack.render_frame(frame)[..., 3]
    return int(np.sum(a.max(axis=0) > 0.5))


def covered_rows(stack, frame):
    a = stack.render_frame(frame)[..., 3]
    return int(np.sum(a.max(axis=1) > 0.5))


# ── catalogue parity ───────────────────────────────────────────────────────


def test_transition_kinds_cover_the_renderer():
    kinds = transition_kinds()
    for k in ("fade", "dissolve", "wipe_l", "wipe_r", "wipe_u", "wipe_d", "slide"):
        assert k in kinds


# ── fade ───────────────────────────────────────────────────────────────────


def test_fade_in_alpha_ramps_from_zero_to_full():
    """fade-in over K frames: alpha ~0 at frame 0, ~full at the last edge frame."""
    fps = 10
    dur = 0.5
    doc = _with_transition(_solid_doc(fps=fps), "fade", dur, "in")
    stack = compile_to_layer_stack(doc)
    k = int(round(dur * fps))  # edge length in frames

    a0 = alpha_mean(stack, 0)
    a_last = alpha_mean(stack, k)  # the last edge frame reaches full reveal

    assert a0 < 0.05, f"fade-in frame 0 alpha should be ~0, got {a0}"
    assert a_last > 0.95, f"fade-in last edge frame alpha should be ~full, got {a_last}"

    # Monotonic non-decreasing ramp across the edge.
    ramp = [alpha_mean(stack, f) for f in range(k + 1)]
    assert all(ramp[i] <= ramp[i + 1] + 1e-6 for i in range(len(ramp) - 1)), ramp


def test_dissolve_in_behaves_like_fade_for_single_layer():
    fps = 10
    dur = 0.5
    doc = _with_transition(_solid_doc(fps=fps), "dissolve", dur, "in")
    stack = compile_to_layer_stack(doc)
    k = int(round(dur * fps))
    assert alpha_mean(stack, 0) < 0.05
    assert alpha_mean(stack, k) > 0.95


def test_fade_out_alpha_ramps_to_zero():
    fps = 10
    dur = 0.5
    doc = _with_transition(_solid_doc(fps=fps), "fade", dur, "out")
    stack = compile_to_layer_stack(doc)
    last = stack.total_frames - 1
    assert alpha_mean(stack, 0) > 0.95, "before the out edge the layer is fully shown"
    assert alpha_mean(stack, last) < 0.05, "the final frame fades to ~0"


# ── wipe ───────────────────────────────────────────────────────────────────


def test_wipe_l_reveals_progressively():
    """wipe_l: covered-column count strictly increases across the in edge."""
    fps = 10
    dur = 0.5
    w = 20
    doc = _with_transition(_solid_doc(w=w, fps=fps), "wipe_l", dur, "in")
    stack = compile_to_layer_stack(doc)
    k = int(round(dur * fps))
    cov = [covered_columns(stack, f) for f in range(k + 1)]

    assert cov[0] == 0, f"wipe_l frame 0 should reveal nothing, got {cov[0]}"
    assert cov[-1] == w, f"wipe_l last edge frame should reveal all {w} cols, got {cov[-1]}"
    assert all(cov[i] <= cov[i + 1] for i in range(len(cov) - 1)), cov
    assert cov[0] < cov[-1], f"coverage must grow: {cov}"


def test_wipe_r_reveals_progressively():
    fps = 10
    dur = 0.5
    w = 20
    doc = _with_transition(_solid_doc(w=w, fps=fps), "wipe_r", dur, "in")
    stack = compile_to_layer_stack(doc)
    k = int(round(dur * fps))
    cov = [covered_columns(stack, f) for f in range(k + 1)]
    assert cov[0] == 0 and cov[-1] == w
    assert all(cov[i] <= cov[i + 1] for i in range(len(cov) - 1)), cov


def test_wipe_d_reveals_rows_progressively():
    fps = 10
    dur = 0.5
    h = 20
    doc = _with_transition(_solid_doc(h=h, fps=fps), "wipe_d", dur, "in")
    stack = compile_to_layer_stack(doc)
    k = int(round(dur * fps))
    cov = [covered_rows(stack, f) for f in range(k + 1)]
    assert cov[0] == 0 and cov[-1] == h
    assert all(cov[i] <= cov[i + 1] for i in range(len(cov) - 1)), cov


# ── slide ──────────────────────────────────────────────────────────────────


def test_slide_in_translates_content():
    """slide-in: content is shifted off-canvas at frame 0, full at the edge end."""
    fps = 10
    dur = 0.5
    w = 20
    doc = _with_transition(_solid_doc(w=w, fps=fps), "slide", dur, "in")
    stack = compile_to_layer_stack(doc)
    k = int(round(dur * fps))
    assert covered_columns(stack, 0) == 0, "slide-in starts fully off-canvas"
    assert covered_columns(stack, k) == w, "slide-in ends fully on-canvas"


# ── golden: no transition is untouched ─────────────────────────────────────


def test_layer_without_transition_is_unchanged():
    doc = _solid_doc(fps=10)
    stack = compile_to_layer_stack(doc)
    for f in (0, 5, 9):
        assert alpha_mean(stack, f) > 0.99, "no transition => full alpha everywhere"
