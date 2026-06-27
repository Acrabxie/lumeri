"""Tests for the additive per-layer time-remap seam on Layer.

A synthetic content_fn renders frame N as a solid RGBA frame whose value is N
(scaled into [0, 1]), so any time remap is directly observable in the output
pixels.
"""
from __future__ import annotations

import numpy as np

from gemia.video.layers import Layer

# Scale local frame index -> pixel value in [0, 1]; chosen so distinct frame
# indices map to distinct, exactly representable float32 values.
_SCALE = 1.0 / 255.0


def _value_content_fn(frame_index: int) -> np.ndarray:
    """Frame N -> solid 4x4 RGBA frame whose every channel equals N * _SCALE."""
    value = float(frame_index) * _SCALE
    return np.full((4, 4, 4), value, dtype=np.float32)


def _make_layer(time_map_fn=None) -> Layer:
    return Layer(
        id="probe",
        name="probe",
        start_frame=0,
        content_fn=_value_content_fn,
        time_map_fn=time_map_fn,
    )


class TestLayerTimeMapSeam:
    def test_default_none_matches_baseline(self) -> None:
        baseline = _make_layer(time_map_fn=None)
        # A Layer constructed without specifying time_map_fn at all must also
        # default to None and behave identically.
        implicit = Layer(id="probe", name="probe", content_fn=_value_content_fn)

        assert baseline.time_map_fn is None
        assert implicit.time_map_fn is None

        for frame_index in range(8):
            base_out = baseline.frame_content(frame_index)
            impl_out = implicit.frame_content(frame_index)
            expected_value = float(frame_index) * _SCALE
            assert np.allclose(base_out, expected_value, atol=0.0), (
                f"frame {frame_index}: expected solid {expected_value}, "
                f"got min={base_out.min()} max={base_out.max()}"
            )
            assert np.array_equal(base_out, impl_out)

    def test_identity_matches_baseline(self) -> None:
        baseline = _make_layer(time_map_fn=None)
        identity = _make_layer(time_map_fn=lambda f: f)

        for frame_index in range(8):
            base_out = baseline.frame_content(frame_index)
            ident_out = identity.frame_content(frame_index)
            assert np.array_equal(base_out, ident_out), (
                f"frame {frame_index}: identity map diverged from baseline"
            )

    def test_freeze_map_produces_frame_zero_everywhere(self) -> None:
        frozen = _make_layer(time_map_fn=lambda _f: 0)

        frame_zero_value = 0.0 * _SCALE  # == 0.0
        for frame_index in range(8):
            out = frozen.frame_content(frame_index)
            # Every output frame must equal source frame 0 (all-zero solid).
            assert np.array_equal(out, np.full((4, 4, 4), frame_zero_value, dtype=np.float32)), (
                f"frame {frame_index}: expected frozen frame-0 value "
                f"{frame_zero_value}, got min={out.min()} max={out.max()}"
            )

    def test_freeze_map_is_observably_different_from_baseline(self) -> None:
        # Guard: prove the freeze map actually changes the pixels relative to
        # the identity/baseline path (so the seam is wired in, not a no-op).
        baseline = _make_layer(time_map_fn=None)
        frozen = _make_layer(time_map_fn=lambda _f: 0)

        # Frame 5 baseline is value 5/255; frozen is value 0.
        base5 = baseline.frame_content(5)
        froz5 = frozen.frame_content(5)
        assert np.isclose(float(base5[0, 0, 0]), 5.0 * _SCALE)
        assert float(froz5[0, 0, 0]) == 0.0
        assert not np.array_equal(base5, froz5)
