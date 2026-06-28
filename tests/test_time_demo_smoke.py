"""Fast smoke test for ``examples/time_demo.py``.

This does NOT render the full ~20s video (that is the script's ``__main__``).
It only proves, cheaply, that each segment-builder produces a doc which:

* ``compile_to_layer_stack`` accepts, and
* whose ``total_frames`` matches the segment's intended duration, and
* actually composes (we render 1-2 frames per segment at SMALL dimensions to
  prove the layer stack draws without error).

The demo module hard-codes 1280x720; we monkeypatch the canvas constants down to
a tiny size so a frame render is near-instant, then reload the registry so the
builders pick up the small canvas. No network, no keys, no external media.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DEMO_PATH = REPO / "examples" / "time_demo.py"


def _load_demo(width: int, height: int):
    """Import examples/time_demo.py fresh with a small canvas patched in.

    The builders read ``W`` / ``H`` / ``FPS`` at call time (module globals), so
    patching the module attributes before invoking a builder is enough to shrink
    the rendered canvas. We import under a unique name so repeated calls are
    independent.
    """
    spec = importlib.util.spec_from_file_location("time_demo_under_test", DEMO_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.W = int(width)
    module.H = int(height)
    return module


@pytest.fixture(scope="module")
def demo():
    return _load_demo(width=160, height=90)


def test_demo_module_imports_and_has_registry(demo):
    """The module imports and exposes the four labelled segments."""
    assert hasattr(demo, "SEGMENTS")
    names = [entry[0] for entry in demo.SEGMENTS]
    assert names == ["seek", "retime", "lanes", "merge"]
    # output target is declared and lives under the time-demo artifact dir.
    assert demo.OUT_MP4.name == "lumeri_time_features.mp4"
    assert demo.OUT_MP4.parent.name == "time-demo"


@pytest.mark.parametrize(
    "name,builder_attr,intended_secs",
    [
        ("seek", "build_seek_doc", None),          # comp is LONGER than shown
        ("retime", "build_retime_doc", 4.0),       # 1.5 + 1.0(2x of 2.0) + 1.5
        ("lanes", "build_lanes_doc", 5.0),
        ("merge", "build_merge_doc", 6.0),         # comp A(3) -> comp B(3)
    ],
)
def test_segment_builder_compiles_and_total_frames(demo, name, builder_attr, intended_secs):
    """Each builder returns a doc compile_to_layer_stack accepts with the right length."""
    from lumenframe.compile import compile_to_layer_stack
    from lumenframe.resolve import default_resolver

    builder = getattr(demo, builder_attr)
    doc = builder()
    assert isinstance(doc, dict) and "root" in doc and "canvas" in doc

    stack = compile_to_layer_stack(doc, resolver=default_resolver)
    assert stack.width == demo.W and stack.height == demo.H
    assert float(stack.fps) == float(demo.FPS)
    assert stack.total_frames >= 1

    if intended_secs is not None:
        # The compiled comp length matches the intended on-screen duration.
        assert stack.total_frames == round(intended_secs * demo.FPS), (
            f"{name}: total_frames {stack.total_frames} != "
            f"{round(intended_secs * demo.FPS)}"
        )


def test_seek_range_slices_a_longer_comp(demo):
    """The SEEK segment renders ONLY the [t_in, t_out) slice of a longer comp."""
    from lumenframe.compile import compile_to_layer_stack
    from lumenframe.resolve import default_resolver

    doc = demo.build_seek_doc()
    stack = compile_to_layer_stack(doc, resolver=default_resolver)
    full_frames = stack.total_frames

    # The source comp is the full RANGE_SRC_SECS; the shown slice is shorter.
    assert full_frames == round(demo.RANGE_SRC_SECS * demo.FPS)
    expected_slice = round((demo.RANGE_OUT - demo.RANGE_IN) * demo.FPS)
    assert expected_slice < full_frames  # proves it's truly a sub-range

    frames = demo.render_range(
        doc, demo.RANGE_IN, demo.RANGE_OUT, resolver=default_resolver
    )
    assert len(frames) == expected_slice
    # frames are real canvas-sized RGBA arrays.
    first = frames[0]
    assert first.shape == (demo.H, demo.W, 4)


def test_each_segment_renders_a_frame(demo):
    """Render 1-2 frames per segment to prove the layer stack actually composes.

    This is the cheap stand-in for the full render: a couple of small frames per
    segment, never the whole video.
    """
    import numpy as np

    from lumenframe.compile import compile_to_layer_stack
    from lumenframe.resolve import default_resolver

    for entry in demo.SEGMENTS:
        name, builder = entry[0], entry[1]
        doc = builder()
        stack = compile_to_layer_stack(doc, resolver=default_resolver)
        # first frame + a frame partway through (covers keyframed motion).
        for idx in {0, min(stack.total_frames - 1, stack.total_frames // 2)}:
            frame = np.asarray(stack.render_frame(idx))
            assert frame.shape == (demo.H, demo.W, 4)
            assert frame.dtype == np.float32
            # something was actually drawn (non-transparent pixels exist).
            assert float(frame[..., 3].max()) > 0.0, f"{name} frame {idx} is empty"


def test_retime_segment_creates_a_2x_middle_piece(demo):
    """The RETIME builder produces a speed=2.0 sub-piece (the retimed middle)."""
    doc = demo.build_retime_doc()
    speeds = [
        float(c.get("speed", 1.0))
        for c in doc["root"]["children"]
        if str(c.get("id", "")).startswith(("shape", "sweep"))
    ]
    assert any(abs(sp - 2.0) < 1e-6 for sp in speeds), (
        f"expected a 2x sub-piece, got speeds {speeds}"
    )


def test_lanes_assigns_distinct_lanes(demo):
    """The LANES builder puts track layers on distinct, non-zero lanes."""
    doc = demo.build_lanes_doc()
    lanes = {
        c["id"]: int(c.get("lane", 0))
        for c in doc["root"]["children"]
        if str(c.get("id", "")).startswith("trk") and not str(c["id"]).endswith("lbl")
    }
    assert set(lanes.values()) >= {1, 2, 3}, f"expected lanes 1/2/3, got {lanes}"


def test_merge_collapses_sources_into_one_timeline(demo):
    """The MERGE builder leaves a single merged comp at the root (sources spliced out)."""
    from lumenframe import model

    doc = demo.build_merge_doc()
    root_children = doc["root"]["children"]
    # The source comps (compA/compB/compC) must be gone from the tree.
    all_ids = {str(n.get("id")) for n in model.walk(doc["root"])}
    assert "compA" not in all_ids and "compB" not in all_ids and "compC" not in all_ids
    # Exactly one merge target ("main") remains at the root holding the content.
    main = model.find_layer(doc, "main")
    assert main is not None
    # append A(3s) then B(3s) -> two sequential pieces + the overlay piece.
    starts = sorted(round(model._as_float(c.get("start")), 3) for c in main["children"])
    assert 0.0 in starts and 3.0 in starts, f"expected A@0 and B@3, got {starts}"
