"""Tests for lumenframe.timebase -- the single source of truth for
seconds <-> frame conversion.

Covers:
* round-trip to_frame/to_seconds at fps in {23.976, 24, 30, 60}
* to_frame(snap_seconds(t, fps), fps) is idempotent / stable
* floor vs ceil vs round differ as expected at .5 frame boundaries
* default rounding is a drop-in for the legacy int(round(seconds * fps))
"""

from __future__ import annotations

import math

import pytest

from lumenframe.timebase import FRAME_EPS, snap_seconds, to_frame, to_seconds

FPS_VALUES = [23.976, 24, 30, 60]


def test_frame_eps_value():
    assert FRAME_EPS == 1e-9


@pytest.mark.parametrize("fps", FPS_VALUES)
@pytest.mark.parametrize("frame", [0, 1, 2, 5, 23, 24, 47, 100, 1000])
def test_round_trip_frame_seconds_frame(fps, frame):
    """frame -> seconds -> frame recovers the original integer frame."""
    seconds = to_seconds(frame, fps)
    assert to_frame(seconds, fps) == frame


@pytest.mark.parametrize("fps", FPS_VALUES)
@pytest.mark.parametrize("frame", [0, 1, 2, 5, 23, 24, 47, 100, 1000])
def test_to_seconds_matches_division(fps, frame):
    assert to_seconds(frame, fps) == pytest.approx(frame / fps, abs=FRAME_EPS)


@pytest.mark.parametrize("fps", FPS_VALUES)
def test_default_rounding_is_legacy_int_round(fps):
    """Default rounding must be bit-for-bit identical to the legacy pattern
    int(round(seconds * fps)) used throughout compile.py."""
    for i in range(0, 200):
        seconds = i / 7.0  # arbitrary non-frame-aligned times
        assert to_frame(seconds, fps) == int(round(seconds * fps))
        assert to_frame(seconds, fps, "round") == int(round(seconds * fps))


@pytest.mark.parametrize("fps", FPS_VALUES)
def test_snap_idempotent_in_seconds(fps):
    """Snapping an already-snapped time returns the same seconds value."""
    for i in range(0, 200):
        t = i / 7.0
        once = snap_seconds(t, fps)
        twice = snap_seconds(once, fps)
        assert once == twice


@pytest.mark.parametrize("fps", FPS_VALUES)
def test_to_frame_of_snap_is_stable(fps):
    """to_frame(snap_seconds(t)) == to_frame(t): snapping does not move the
    quantized frame, and re-quantizing the snapped time is stable."""
    for i in range(0, 200):
        t = i / 7.0
        f0 = to_frame(t, fps)
        snapped = snap_seconds(t, fps)
        assert to_frame(snapped, fps) == f0
        # And snapping again keeps the same frame.
        assert to_frame(snap_seconds(snapped, fps), fps) == f0


@pytest.mark.parametrize("fps", FPS_VALUES)
def test_snap_lands_on_frame_boundary(fps):
    """A snapped time is, to within FRAME_EPS, an exact multiple of 1/fps."""
    for i in range(0, 200):
        t = i / 7.0
        snapped = snap_seconds(t, fps)
        frames = snapped * fps
        assert abs(frames - round(frames)) <= FRAME_EPS * fps + 1e-12


@pytest.mark.parametrize("fps", FPS_VALUES)
@pytest.mark.parametrize("frame", [0, 1, 5, 24, 100])
def test_floor_round_ceil_ordering(fps, frame):
    """floor <= round <= ceil for any time."""
    # pick a time strictly inside a frame interval
    t = to_seconds(frame, fps) + 0.3 / fps
    fl = to_frame(t, fps, "floor")
    rd = to_frame(t, fps, "round")
    ce = to_frame(t, fps, "ceil")
    assert fl <= rd <= ce
    # 0.3 of a frame in: floor stays, ceil advances by one
    assert fl == frame
    assert ce == frame + 1


@pytest.mark.parametrize("fps", FPS_VALUES)
def test_floor_ceil_differ_at_half_boundary(fps):
    """At a .5 frame boundary floor and ceil must differ by exactly one."""
    # 1.5 frames into the timeline -> exact == X.5
    t = 1.5 / fps
    fl = to_frame(t, fps, "floor")
    ce = to_frame(t, fps, "ceil")
    assert ce - fl == 1
    assert fl == 1
    assert ce == 2


def test_half_boundary_concrete_24fps():
    """Concrete .5-boundary behavior at 24 fps.

    Default 'round' uses Python's int(round(...)) (banker's rounding), so the
    three policies are demonstrably distinct across these cases.
    """
    fps = 24
    # exact == 0.5  -> round=0 (banker's), floor=0, ceil=1
    t = 0.5 / fps
    assert to_frame(t, fps, "round") == 0
    assert to_frame(t, fps, "floor") == 0
    assert to_frame(t, fps, "ceil") == 1
    # exact == 1.5 -> round=2, floor=1, ceil=2  (floor differs from round/ceil)
    t = 1.5 / fps
    assert to_frame(t, fps, "round") == 2
    assert to_frame(t, fps, "floor") == 1
    assert to_frame(t, fps, "ceil") == 2
    # exact == 2.5 -> round=2 (banker's), floor=2, ceil=3 (ceil differs)
    t = 2.5 / fps
    assert to_frame(t, fps, "round") == 2
    assert to_frame(t, fps, "floor") == 2
    assert to_frame(t, fps, "ceil") == 3


def test_floor_ceil_do_not_overshoot_on_exact_frame():
    """A time exactly on a frame boundary must not be pushed off it by the
    FRAME_EPS cushion in floor/ceil."""
    fps = 24
    for frame in [0, 1, 7, 24, 240]:
        t = to_seconds(frame, fps)
        assert to_frame(t, fps, "floor") == frame
        assert to_frame(t, fps, "ceil") == frame
        assert to_frame(t, fps, "round") == frame


def test_invalid_rounding_raises():
    with pytest.raises(ValueError):
        to_frame(1.0, 24, "nearest")


def test_round_trip_non_integer_fps_23_976():
    """Explicit round-trip spot checks at the NTSC 23.976 fps rate."""
    fps = 23.976
    for frame in [0, 1, 12, 24, 48, 239]:
        seconds = to_seconds(frame, fps)
        assert to_frame(seconds, fps) == frame
