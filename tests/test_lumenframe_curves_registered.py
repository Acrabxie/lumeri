"""The ``curves`` colour tool is a registered, invokable effect.

The curves *kernel* (``lumenframe.effects.curves``) already existed but was not
wired into the renderer's dispatch table. These tests prove it is now a
first-class effect: invokable via ``add_effect`` type ``"curves"``, that an
identity curve is a no-op, that an S-curve raises contrast, and that the
EFFECTS <-> catalogue drift guard still holds with ``curves`` present in both.
"""
from __future__ import annotations

import numpy as np
import pytest

from lumenframe import apply_layer_patch, empty_doc
from lumenframe.compile import EFFECTS, compile_to_layer_stack
from lumenframe.catalog import effect_types


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def _grey_doc(level: float, *, w=8, h=8, fps=10):
    """A solid mid-grey layer so a curve has tone to act on."""
    hexv = format(int(round(level * 255)), "02x")
    color = f"#{hexv}{hexv}{hexv}"
    doc = empty_doc(width=w, height=h, fps=fps)
    return apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "g", "type": "solid", "color": color,
        "start": 0.0, "duration": 1.0,
    }))


def center_rgb(frame):
    px = frame[frame.shape[0] // 2, frame.shape[1] // 2]
    return float(px[0]), float(px[1]), float(px[2])


# ── registration / drift guard ────────────────────────────────────────────


def test_curves_registered_in_dispatch_and_catalog():
    assert "curves" in EFFECTS
    assert "curves" in effect_types()


def test_no_drift_with_curves_present():
    """The drift guard (set(EFFECTS) == effect_types()) stays green."""
    assert set(EFFECTS) == effect_types()


# ── behaviour through the real compile path (add_effect type 'curves') ──────


def test_curves_identity_is_a_noop():
    """add_effect type 'curves' with identity points leaves pixels unchanged."""
    base = _grey_doc(0.5)
    base_px = center_rgb(compile_to_layer_stack(base).render_frame(0))

    doc = apply_layer_patch(base, patch({
        "op": "add_effect", "layer_id": "g",
        "effect": {"type": "curves", "params": {"channel": "rgb",
                                                "points": [[0.0, 0.0], [1.0, 1.0]]}},
    }))
    out_px = center_rgb(compile_to_layer_stack(doc).render_frame(0))
    for b, o in zip(base_px, out_px):
        assert o == pytest.approx(b, abs=1e-4)


def test_curves_scurve_raises_contrast():
    """An S-curve pushes a dark tone darker and a light tone lighter."""
    s_points = [[0.0, 0.0], [0.25, 0.12], [0.5, 0.5], [0.75, 0.88], [1.0, 1.0]]

    # Dark sample (0.3) gets pushed down; light sample (0.7) gets pushed up.
    dark_in = 0.3
    light_in = 0.7
    dark_doc = apply_layer_patch(_grey_doc(dark_in), patch({
        "op": "add_effect", "layer_id": "g",
        "effect": {"type": "curves", "params": {"channel": "rgb", "points": s_points}},
    }))
    light_doc = apply_layer_patch(_grey_doc(light_in), patch({
        "op": "add_effect", "layer_id": "g",
        "effect": {"type": "curves", "params": {"channel": "rgb", "points": s_points}},
    }))
    dark_out = center_rgb(compile_to_layer_stack(dark_doc).render_frame(0))[0]
    light_out = center_rgb(compile_to_layer_stack(light_doc).render_frame(0))[0]

    assert dark_out < dark_in, f"dark tone should drop, got {dark_out} >= {dark_in}"
    assert light_out > light_in, f"light tone should rise, got {light_out} <= {light_in}"

    # Contrast = the spread between light and dark widens after the curve.
    spread_in = light_in - dark_in
    spread_out = light_out - dark_out
    assert spread_out > spread_in, (
        f"S-curve must raise contrast: spread_in={spread_in:.4f} "
        f"spread_out={spread_out:.4f}"
    )
