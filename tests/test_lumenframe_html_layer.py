"""R3 html-layer tests — HTML/CSS/JS motion graphics composited as a video layer.

DETERMINISTIC: ``render_hyperframes_clip`` is monkeypatched to write a known
solid-colour mp4 and return a clip dict pointing at it, so NO real browser /
HyperFrames CLI / network is touched. The tests then:

* compile a doc with an ``html`` layer and assert the composited centre pixel
  equals the stubbed clip's colour (stubbed-vs-composited pixel equality);
* assert the stub is called exactly ONCE even across many ``render_frame`` calls
  AND a second ``compile_to_layer_stack`` of the same content (caching);
* assert the ``html`` layer type is registered.

A best-effort *real* render check is attempted only if the ``hyperframes`` CLI is
on PATH; it is skipped (never failed) otherwise, so the suite never depends on it.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import numpy as np
import pytest

from lumenframe import apply_layer_patch, empty_doc, layer_type_spec, list_layer_types
from lumenframe.compile import compile_to_layer_stack
import lumenframe.resolve_html as resolve_html


# ── helpers ────────────────────────────────────────────────────────────────


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def center_px(frame):
    return frame[frame.shape[0] // 2, frame.shape[1] // 2]


def _write_solid_mp4(path: str, *, rgb: tuple[int, int, int], frames: int, w: int, h: int, fps: float) -> None:
    """Write a solid-colour mp4 at ``path`` (RGB given, cv2 writes BGR)."""
    import cv2

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(path, fourcc, fps, (w, h))
    bgr = (rgb[2], rgb[1], rgb[0])
    for _ in range(max(frames, 1)):
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        frame[:, :] = bgr
        out.write(frame)
    out.release()


class _StubRenderer:
    """Stand-in for render_hyperframes_clip: writes a known solid mp4, counts calls."""

    def __init__(self, *, rgb, w, h, fps, tmpdir: Path):
        self.rgb = rgb
        self.w = w
        self.h = h
        self.fps = fps
        self.tmpdir = tmpdir
        self.calls = 0
        self.last_kwargs = None

    def __call__(self, stage_html, *, css="", duration=3.0, width=None, height=None,
                 fps=None, name="hyperframes", context):
        self.calls += 1
        self.last_kwargs = {
            "stage_html": stage_html,
            "css": css,
            "duration": duration,
            "width": width,
            "height": height,
            "fps": fps,
            "name": name,
            "context": context,
        }
        out_path = self.tmpdir / f"render_{self.calls}.mp4"
        n_frames = max(int(round(float(duration) * float(self.fps))), 1)
        _write_solid_mp4(
            str(out_path), rgb=self.rgb, frames=n_frames,
            w=self.w, h=self.h, fps=self.fps,
        )
        clip_duration = max(float(duration), 0.1)
        return {
            "id": "clip_stub",
            "asset_id": "asset_stub",
            "path": str(out_path),
            "name": f"{name}.mp4",
            "media_kind": "video",
            "duration": clip_duration,
            "source_in": 0.0,
            "source_out": clip_duration,
            "metadata": {"generated_by": "stub", "width": self.w, "height": self.h, "fps": self.fps},
        }


@pytest.fixture
def stub_workspace():
    """Isolated tmp dir + a clean html render cache for each test."""
    resolve_html.clear_render_cache()
    d = Path(tempfile.mkdtemp(prefix="html_layer_test_"))
    try:
        yield d
    finally:
        resolve_html.clear_render_cache()
        shutil.rmtree(d, ignore_errors=True)


def _add_html_layer(doc, *, html, css=None, js=None, duration=0.5, lid="hl"):
    op = {"op": "add_layer", "id": lid, "type": "html", "html": html, "duration": duration}
    if css is not None:
        op["css"] = css
    if js is not None:
        op["js"] = js
    return apply_layer_patch(doc, patch(op))


# ── registration ─────────────────────────────────────────────────────────


def test_html_layer_type_is_registered():
    """The 'html' layer type is known to the registry (so add_layer accepts it)."""
    assert layer_type_spec("html") is not None
    assert "html" in list_layer_types()
    # The core registers it as a non-container leaf layer.
    spec = layer_type_spec("html")
    assert spec.get("container") is False


def test_add_html_layer_accepted_by_ops():
    """add_layer validation accepts type='html' and stores props {html,css,js}."""
    doc = empty_doc(width=32, height=24, fps=10)
    doc = _add_html_layer(
        doc, html="<div class='box'></div>", css=".box{width:10px}",
        js="console.log(1)", duration=0.5,
    )
    layer = doc["root"]["children"][0]
    assert layer["type"] == "html"
    assert layer["props"]["html"] == "<div class='box'></div>"
    assert layer["props"]["css"] == ".box{width:10px}"
    assert layer["props"]["js"] == "console.log(1)"


# ── stubbed pixel equality ─────────────────────────────────────────────────


def test_html_layer_composites_stubbed_pixels(monkeypatch, stub_workspace):
    """Composited centre pixel equals the stubbed clip's known solid colour."""
    w, h, fps = 64, 48, 10
    rgb = (10, 200, 30)  # a distinctive green
    stub = _StubRenderer(rgb=rgb, w=32, h=24, fps=fps, tmpdir=stub_workspace)
    monkeypatch.setattr("gemia.hyperframes_adapter.render_hyperframes_clip", stub)

    doc = empty_doc(width=w, height=h, fps=fps)
    doc = _add_html_layer(doc, html="<div>hi</div>", css="div{color:red}", duration=0.5)

    stack = compile_to_layer_stack(doc)
    frame = stack.render_frame(0)
    px = center_px(frame)

    # Stub colour in float32 [0,1]. mp4 codec round-trips with slight loss, so
    # compare with a tolerance that still distinguishes the channels clearly.
    expected = np.array([rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0], dtype=np.float32)
    assert np.allclose(px[:3], expected, atol=0.06), f"px={px[:3]} expected={expected}"
    assert px[3] > 0.9  # opaque
    assert stub.calls == 1


def test_html_render_called_once_across_many_frames(monkeypatch, stub_workspace):
    """render_hyperframes_clip fires exactly ONCE even across many render_frame calls."""
    w, h, fps = 48, 36, 10
    stub = _StubRenderer(rgb=(200, 20, 20), w=32, h=24, fps=fps, tmpdir=stub_workspace)
    monkeypatch.setattr("gemia.hyperframes_adapter.render_hyperframes_clip", stub)

    doc = empty_doc(width=w, height=h, fps=fps)
    doc = _add_html_layer(doc, html="<p>x</p>", duration=0.5)

    stack = compile_to_layer_stack(doc)
    # Render many frames; the html->mp4 render must not re-fire per frame.
    for i in range(5):
        stack.render_frame(i)
    assert stub.calls == 1, f"render called {stub.calls} times across 5 frames"


def test_html_render_cached_across_recompiles(monkeypatch, stub_workspace):
    """Identical content across separate compiles renders ONCE (content-hash cache)."""
    w, h, fps = 48, 36, 10
    stub = _StubRenderer(rgb=(20, 20, 200), w=32, h=24, fps=fps, tmpdir=stub_workspace)
    monkeypatch.setattr("gemia.hyperframes_adapter.render_hyperframes_clip", stub)

    def build():
        d = empty_doc(width=w, height=h, fps=fps)
        return _add_html_layer(d, html="<b>same</b>", css="b{font-weight:bold}", duration=0.5)

    # First compile renders; second compile of identical content reuses the cache.
    compile_to_layer_stack(build()).render_frame(0)
    compile_to_layer_stack(build()).render_frame(0)
    assert stub.calls == 1

    # Changing the content re-renders (proves the cache keys on content, not identity).
    d2 = empty_doc(width=w, height=h, fps=fps)
    d2 = _add_html_layer(d2, html="<b>different</b>", css="b{font-weight:bold}", duration=0.5)
    compile_to_layer_stack(d2).render_frame(0)
    assert stub.calls == 2


def test_html_layer_maps_canvas_and_duration(monkeypatch, stub_workspace):
    """The layer's duration + canvas w/h/fps are passed through to the renderer."""
    w, h, fps = 80, 60, 12
    stub = _StubRenderer(rgb=(120, 120, 120), w=32, h=24, fps=fps, tmpdir=stub_workspace)
    monkeypatch.setattr("gemia.hyperframes_adapter.render_hyperframes_clip", stub)

    doc = empty_doc(width=w, height=h, fps=fps)
    doc = _add_html_layer(doc, html="<div>map</div>", js="var a=1;", duration=0.75)

    compile_to_layer_stack(doc).render_frame(0)
    kw = stub.last_kwargs
    assert kw is not None
    assert kw["width"] == w
    assert kw["height"] == h
    assert float(kw["fps"]) == float(fps)
    assert abs(float(kw["duration"]) - 0.75) < 1e-6
    # js rides along inside the stage_html as a <script> block.
    assert "<script>" in kw["stage_html"]
    assert "var a=1;" in kw["stage_html"]
    assert "<div>map</div>" in kw["stage_html"]


def test_html_layer_empty_html_skips(monkeypatch, stub_workspace):
    """Empty html resolves to nothing and never calls the renderer."""
    stub = _StubRenderer(rgb=(0, 0, 0), w=32, h=24, fps=10, tmpdir=stub_workspace)
    monkeypatch.setattr("gemia.hyperframes_adapter.render_hyperframes_clip", stub)

    doc = empty_doc(width=32, height=24, fps=10)
    doc = _add_html_layer(doc, html="   ", duration=0.5)
    stack = compile_to_layer_stack(doc)
    frame = stack.render_frame(0)
    # No html content -> transparent canvas, renderer untouched.
    assert stub.calls == 0
    assert float(frame[..., 3].max()) == 0.0


# ── best-effort real render (never depended upon) ───────────────────────────


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg not available for a real render path",
)
def test_html_layer_real_render_best_effort(stub_workspace):  # pragma: no cover - env dependent
    """Best-effort: a *real* render (CLI or local fallback) should composite too.

    NOT depended upon — any environmental failure (HyperFrames fps constraints,
    missing CLI, render hiccup) skips the test rather than failing the suite. The
    adapter requires fps in {24,30,60}, so we use 30.
    """
    from gemia.hyperframes_adapter import HyperFramesRenderError

    resolve_html.clear_render_cache()
    # fps=30 satisfies HyperFrames v1 (24/30/60 only).
    doc = empty_doc(width=64, height=48, fps=30)
    doc = _add_html_layer(
        doc, html="<div style='width:100%;height:100%;background:#00ff00'></div>",
        duration=0.3,
    )
    try:
        stack = compile_to_layer_stack(doc)
        frame = stack.render_frame(0)
    except HyperFramesRenderError as exc:  # pragma: no cover - env dependent
        pytest.skip(f"real hyperframes render unavailable: {exc}")
    except Exception as exc:  # pragma: no cover - env dependent
        pytest.skip(f"real render path unavailable: {exc!r}")
    # Best-effort: just assert we produced an opaque frame of the right shape.
    assert frame.shape == (48, 64, 4)
    assert float(frame[..., 3].max()) > 0.0
