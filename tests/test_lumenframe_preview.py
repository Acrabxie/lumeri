"""Sparse frame preview backend — preview_frames / preview_frames_png."""
from __future__ import annotations

import numpy as np
import pytest

from lumenframe import apply_layer_patch, empty_doc
from lumenframe.compile import compile_to_layer_stack
from lumenframe.preview import preview_frames, preview_frames_png


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def base_doc(w=64, h=48, fps=10):
    return empty_doc(width=w, height=h, fps=fps)


def add_solid(doc, lid, color, *, start=0.0, duration=1.0, **fields):
    return apply_layer_patch(doc, patch({
        "op": "add_layer", "id": lid, "type": "solid", "color": color,
        "start": start, "duration": duration, **fields,
    }))


def build_doc():
    """A small multi-layer, multi-window doc so frames actually differ.

    fps=10, duration ~2s -> 20 frames. ``red`` covers the first second,
    ``green`` (on top, added later) covers the second second. So early frames
    are red, late frames are green -> previews at [0, mid, last] are visibly
    distinct, which makes the pixel-equality check meaningful.
    """
    doc = base_doc(w=64, h=48, fps=10)
    doc = add_solid(doc, "red", "#FF0000", start=0.0, duration=1.0)    # frames 0..9
    doc = add_solid(doc, "green", "#00FF00", start=1.0, duration=1.0)  # frames 10..19
    return doc


def center_px(frame):
    return frame[frame.shape[0] // 2, frame.shape[1] // 2]


# ── core equivalence: preview == direct render ────────────────────────────


def test_preview_matches_direct_render_pixel_exact():
    doc = build_doc()
    stack = compile_to_layer_stack(doc)
    total = stack.total_frames
    assert total == 20

    mid = total // 2          # 10
    last = total - 1          # 19
    requested = [0, mid, last]

    results = preview_frames(doc, requested)

    # Only the requested frames come back, in request order, with the right idx.
    assert [idx for idx, _ in results] == requested

    for idx, arr in results:
        direct = compile_to_layer_stack(doc).render_frame(idx)
        assert np.array_equal(arr, direct), f"frame {idx} mismatch vs direct render"
        # Dimensions == canvas (h, w, 4) and float32 RGBA.
        assert arr.shape == (stack.height, stack.width, 4) == (48, 64, 4)
        assert arr.dtype == np.float32


def test_preview_content_is_what_we_expect():
    """Concrete proof of the actual pixel values at each previewed frame."""
    doc = build_doc()
    results = preview_frames(doc, [0, 10, 19])
    px = {idx: center_px(arr) for idx, arr in results}

    # frame 0: only red active -> opaque red.
    assert px[0][0] == pytest.approx(1.0)
    assert px[0][1] == pytest.approx(0.0)
    assert px[0][3] == pytest.approx(1.0)
    # frame 10: red gone (ends at 9), green active -> opaque green.
    assert px[10][0] == pytest.approx(0.0)
    assert px[10][1] == pytest.approx(1.0)
    assert px[10][3] == pytest.approx(1.0)
    # frame 19 (last): green still active -> opaque green.
    assert px[19][1] == pytest.approx(1.0)
    assert px[19][3] == pytest.approx(1.0)


# ── only requested frames are returned ────────────────────────────────────


def test_only_requested_frames_returned():
    doc = build_doc()
    results = preview_frames(doc, [3, 7])
    assert len(results) == 2
    assert [idx for idx, _ in results] == [3, 7]


def test_request_order_and_duplicates_preserved():
    doc = build_doc()
    results = preview_frames(doc, [5, 0, 5])
    assert [idx for idx, _ in results] == [5, 0, 5]
    # Same index renders byte-identical content both times.
    assert np.array_equal(results[0][1], results[2][1])


def test_empty_request_returns_empty_list():
    doc = build_doc()
    assert preview_frames(doc, []) == []


# ── clamping of out-of-range indices ──────────────────────────────────────


def test_out_of_range_high_clamps_to_last():
    doc = build_doc()
    stack = compile_to_layer_stack(doc)
    last = stack.total_frames - 1  # 19

    (idx, arr), = preview_frames(doc, [9999])
    assert idx == last
    direct = compile_to_layer_stack(doc).render_frame(last)
    assert np.array_equal(arr, direct)


def test_negative_index_clamps_to_zero():
    doc = build_doc()
    (idx, arr), = preview_frames(doc, [-5])
    assert idx == 0
    direct = compile_to_layer_stack(doc).render_frame(0)
    assert np.array_equal(arr, direct)


def test_clamped_indices_never_raise_indexerror():
    doc = build_doc()
    stack = compile_to_layer_stack(doc)
    total = stack.total_frames
    # Mix of below-range, in-range, and above-range; none should raise.
    results = preview_frames(doc, [-100, 0, total // 2, total - 1, total, total + 50])
    idxs = [idx for idx, _ in results]
    assert idxs == [0, 0, total // 2, total - 1, total - 1, total - 1]
    for idx in idxs:
        assert 0 <= idx < total


# ── single compile reused (sparse, not full render) ───────────────────────


def test_compiles_once_and_renders_only_requested(monkeypatch):
    import lumenframe.preview as preview_mod

    doc = build_doc()

    compile_calls = {"n": 0}
    real_compile = preview_mod.compile_to_layer_stack

    def counting_compile(*args, **kwargs):
        compile_calls["n"] += 1
        stack = real_compile(*args, **kwargs)
        rendered: list[int] = []
        real_render = stack.render_frame

        def tracking_render(frame_index):
            rendered.append(frame_index)
            return real_render(frame_index)

        stack.render_frame = tracking_render  # type: ignore[method-assign]
        counting_compile.rendered = rendered  # type: ignore[attr-defined]
        return stack

    monkeypatch.setattr(preview_mod, "compile_to_layer_stack", counting_compile)

    requested = [0, 5, 19]
    preview_frames(doc, requested)

    assert compile_calls["n"] == 1, "doc must be compiled exactly once"
    assert counting_compile.rendered == requested, (
        "render_frame must be called only for requested indices, in order"
    )


# ── PNG variant (PIL available) ───────────────────────────────────────────


def test_preview_frames_png_returns_png_bytes():
    doc = build_doc()
    results = preview_frames_png(doc, [0, 19])
    assert [idx for idx, _ in results] == [0, 19]
    for idx, data in results:
        assert isinstance(data, (bytes, bytearray))
        # PNG magic header.
        assert data[:8] == b"\x89PNG\r\n\x1a\n", f"frame {idx} not a PNG"


def test_preview_frames_png_decodes_to_canvas_dims():
    from PIL import Image
    import io

    doc = build_doc()
    (idx, data), = preview_frames_png(doc, [0])
    img = Image.open(io.BytesIO(data))
    assert img.mode == "RGBA"
    assert img.size == (64, 48)  # (width, height)
    # Center pixel of frame 0 is opaque red (255, 0, 0, 255).
    r, g, b, a = img.getpixel((64 // 2, 48 // 2))
    assert (r, g, a) == (255, 0, 255)
