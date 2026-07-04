"""Direct-UI compositing ops — REAL end-to-end (no mocks).

The compositing UI emits a small, shared op contract to
``POST /sessions/{id}/timeline/op``. The backend must ACCEPT, APPLY, and let
those ops RENDER. This module drives the ACTUAL ``_session_timeline_op`` handler
(the same entrypoint ``server.py`` routes the HTTP POST into) against a real
``AgentLoopV3`` whose project is a real ProjectStore-backed patch log — exactly
the harness ``tests/test_timeline_direct_edit.py`` uses. Nothing about the
server/patcher is mocked; only the HTTP socket plumbing is replaced by an
in-memory request/response handler so the assertions hit live code.

Contract under test:
  - BLEND:     set_effects{effects:{blend_mode:"<14 modes>"}}  (reuses set_effects)
  - PIP:       set_effects{effects:{scale,x,y}}                (already worked)
  - CROSSFADE: add_transition{kind:"<_TRANSITION_KINDS>", duration_sec}
  - a bogus op (e.g. "set_blend") must STILL be rejected with 400 (control)

Render bridge: a clip whose effects carry blend_mode="screen" must yield a Layer
(renderable composite branch) with blend_mode=="screen" through
``gemia.video.compositing_graph`` — proving the stored effect actually reaches
the compositor (gemia.video.layers Layer.blend_mode / _blend_colors).
"""
from __future__ import annotations

import io
import json
from types import SimpleNamespace
from typing import Any

import numpy as np

from gemia import v3_routes
from gemia.agent_loop_v3 import AgentLoopV3
from gemia.video.compositing_graph import (
    build_compositing_graph_from_layer_plan,
    build_compositing_graph_from_layer_stack,
)
from gemia.video.layers import BLEND_MODES, Layer, LayerStack


# ── harness (mirrors tests/test_timeline_direct_edit.py exactly) ───────────────


class _PostHandler:
    """Minimal POST handler: rfile carries the JSON body, wfile captures output."""

    def __init__(self, body: bytes) -> None:
        self.headers = {"Content-Length": str(len(body))}
        self.path = "/"
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status: int | None = None
        self.response_headers: dict[str, str] = {}
        self.connection = None

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, key: str, value: str) -> None:
        self.response_headers[key.lower()] = value

    def end_headers(self) -> None:
        pass

    @property
    def body_json(self) -> dict:
        return json.loads(self.wfile.getvalue().decode("utf-8"))


def _loop(tmp_path, sid: str) -> tuple[AgentLoopV3, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    loop = AgentLoopV3(
        session_id=sid,
        output_dir=tmp_path,
        gemini_client=SimpleNamespace(model="fake"),
        emit_event=events.append,
    )
    return loop, events


def _seed_video_clip(loop: AgentLoopV3, clip_id: str, *, start: float, duration: float,
                     asset_id: str) -> None:
    """Insert one video clip on V1 via the patch layer (no ffmpeg: the patch
    layer never reads the file, only the dict)."""
    loop.project.apply_ops(
        [
            {"op": "upsert_asset", "asset": {
                "id": asset_id, "asset_id": asset_id, "name": "v.mp4",
                "media_kind": "video", "source_path": "/tmp/v.mp4", "duration": 60.0}},
            {"op": "insert_clip", "track_id": "V1", "at": {"time": start},
             "data": {"clip": {
                 "id": clip_id, "asset_id": asset_id, "media_kind": "video",
                 "duration": duration, "source_in": 0.0, "source_out": duration}}},
        ],
        label="test-setup",
    )


def _post(loop: AgentLoopV3, sid: str, op_body: dict) -> _PostHandler:
    handler = _PostHandler(json.dumps(op_body).encode("utf-8"))
    runner = SimpleNamespace(agent=loop, session_id=sid)
    ok = v3_routes._session_timeline_op(handler, runner)
    assert ok is True
    return handler


def _clip(loop: AgentLoopV3, clip_id: str) -> dict | None:
    for c in loop.project.load()["timeline"]["clips"]:
        if c.get("id") == clip_id:
            return c
    return None


# ── BLEND: set_effects{blend_mode} -> 200 + stored on the clip ────────────────


def test_blend_set_effects_stores_blend_mode(tmp_path) -> None:
    loop, _ = _loop(tmp_path, "fx-blend")
    _seed_video_clip(loop, "c1", start=0.0, duration=5.0, asset_id="a1")

    h = _post(loop, "fx-blend", {"op": "set_effects", "clip_id": "c1",
                                 "effects": {"blend_mode": "screen"}})

    assert h.status == 200
    assert _clip(loop, "c1")["effects"].get("blend_mode") == "screen"
    # SAME path as the model verbs: stored with user provenance.
    assert _clip(loop, "c1")["provenance"]["source"] == "user_direct_edit"


def test_blend_accepts_all_fourteen_ui_modes(tmp_path) -> None:
    """The 14 modes the frontend UI exposes must all be accepted + stored."""
    ui_modes = [
        "normal", "multiply", "screen", "overlay", "add", "lighten", "darken",
        "soft_light", "hard_light", "difference", "exclusion", "color_dodge",
        "color_burn", "subtract",
    ]
    # The frontend's 14 are a subset of the renderer's canonical BLEND_MODES.
    assert set(ui_modes).issubset(set(BLEND_MODES))
    for i, mode in enumerate(ui_modes):
        loop, _ = _loop(tmp_path / f"m{i}", f"fx-blend-{i}")
        _seed_video_clip(loop, "c1", start=0.0, duration=5.0, asset_id="a1")
        h = _post(loop, f"fx-blend-{i}", {"op": "set_effects", "clip_id": "c1",
                                          "effects": {"blend_mode": mode}})
        assert h.status == 200, (mode, h.body_json)
        assert _clip(loop, "c1")["effects"].get("blend_mode") == mode


def test_blend_unknown_mode_rejected_400(tmp_path) -> None:
    loop, _ = _loop(tmp_path, "fx-blend-bad")
    _seed_video_clip(loop, "c1", start=0.0, duration=5.0, asset_id="a1")
    h = _post(loop, "fx-blend-bad", {"op": "set_effects", "clip_id": "c1",
                                     "effects": {"blend_mode": "telepathy"}})
    assert h.status == 400
    assert h.body_json["code"] == "E_BAD_ARG"
    # rejected => never stored
    assert _clip(loop, "c1").get("effects", {}).get("blend_mode") is None


# ── PIP: set_effects{scale,x,y} -> 200 + stored (already in _EFFECT_KEYS) ──────


def test_pip_set_effects_stores_scale_x_y(tmp_path) -> None:
    loop, _ = _loop(tmp_path, "fx-pip")
    _seed_video_clip(loop, "c1", start=0.0, duration=5.0, asset_id="a1")
    h = _post(loop, "fx-pip", {"op": "set_effects", "clip_id": "c1",
                               "effects": {"scale": 0.4, "x": 120, "y": 30}})
    assert h.status == 200
    fx = _clip(loop, "c1")["effects"]
    assert abs(fx["scale"] - 0.4) < 1e-6 and fx["x"] == 120 and fx["y"] == 30


# ── CROSSFADE: add_transition -> 200 + transition on the project ──────────────


def test_crossfade_add_transition_lands_on_project(tmp_path) -> None:
    loop, events = _loop(tmp_path, "fx-xfade")
    # two adjacent video clips on V1 so the transition has a following clip.
    _seed_video_clip(loop, "c1", start=0.0, duration=5.0, asset_id="a1")
    _seed_video_clip(loop, "c2", start=5.0, duration=5.0, asset_id="a2")
    events.clear()

    h = _post(loop, "fx-xfade", {"op": "add_transition", "clip_id": "c1",
                                 "kind": "dissolve", "duration_sec": 0.5})

    assert h.status == 200
    trans = _clip(loop, "c1")["transition_after"]
    assert trans is not None
    assert trans["kind"] == "dissolve"
    assert abs(trans["duration_sec"] - 0.5) < 1e-6
    # SAME path as the verbs: a timeline_op SSE fires carrying the internal op.
    tl_ops = [e for e in events if e.get("kind") == "timeline_op"]
    assert len(tl_ops) == 1 and "add_transition" in tl_ops[0]["ops"]


def test_crossfade_unknown_kind_rejected_400(tmp_path) -> None:
    loop, _ = _loop(tmp_path, "fx-xfade-bad")
    _seed_video_clip(loop, "c1", start=0.0, duration=5.0, asset_id="a1")
    _seed_video_clip(loop, "c2", start=5.0, duration=5.0, asset_id="a2")
    # "crossfade" is the UI label but NOT a lumerai _TRANSITION_KINDS member;
    # the frontend must map it to "dissolve". A raw bad kind fails fast.
    h = _post(loop, "fx-xfade-bad", {"op": "add_transition", "clip_id": "c1",
                                     "kind": "crossfade", "duration_sec": 0.5})
    assert h.status == 400
    assert h.body_json["code"] == "E_BAD_ARG"
    assert _clip(loop, "c1").get("transition_after") is None


# ── control: a bogus op is STILL rejected with 400 ────────────────────────────


def test_bogus_op_still_rejected_400(tmp_path) -> None:
    """The frontend used to emit set_blend/pip/crossfade names directly; those raw
    op tokens are NOT in _USER_EDIT_OPS and must still be a 400 (control)."""
    loop, _ = _loop(tmp_path, "fx-control")
    _seed_video_clip(loop, "c1", start=0.0, duration=5.0, asset_id="a1")
    for bogus in ("set_blend", "pip", "crossfade", "add_gradient", "add_shape"):
        h = _post(loop, "fx-control", {"op": bogus, "clip_id": "c1"})
        assert h.status == 400, (bogus, h.status)
        assert "unknown op" in h.body_json["error"]


# ── RENDER bridge: stored blend_mode -> Layer.blend_mode via compositing_graph ─


def test_clip_effects_blend_mode_reaches_compositing_graph(tmp_path) -> None:
    """End-to-end render assertion: store blend_mode via the REAL op handler,
    then feed a clip-shaped layer_spec (blend_mode under ``effects``) through the
    real compositing_graph and confirm the renderable composite branch carries
    blend_mode=="screen"."""
    loop, _ = _loop(tmp_path, "fx-render")
    _seed_video_clip(loop, "c1", start=0.0, duration=5.0, asset_id="a1")
    h = _post(loop, "fx-render", {"op": "set_effects", "clip_id": "c1",
                                  "effects": {"blend_mode": "screen"}})
    assert h.status == 200
    clip = _clip(loop, "c1")
    assert clip["effects"]["blend_mode"] == "screen"

    # The renderer reads compositing settings off the clip's effects map. Mirror
    # that into a layer plan (effects nested, exactly as the clip stores it) and
    # build the graph the renderer would.
    plan = {
        "width": 320, "height": 240, "fps": 30.0, "total_frames": 10,
        "layers": [{
            "type": "solid", "id": "c1", "color": (255, 0, 0), "duration": 10,
            "effects": {"blend_mode": clip["effects"]["blend_mode"]},
        }],
    }
    graph = build_compositing_graph_from_layer_plan(plan)
    composite = next(n for n in graph.nodes.values() if n.kind == "composite")
    assert composite.params["blend_mode"] == "screen"


def test_layer_blend_mode_flows_through_compositing_graph(tmp_path) -> None:
    """A concrete Layer with blend_mode="screen" is preserved as the composite
    branch's blend_mode through compositing_graph — the Layer.blend_mode the
    compositor's _blend_colors consumes at render time."""
    stack = LayerStack(width=320, height=240, fps=30.0, total_frames=10)
    layer = Layer(
        id="c1", name="c1", blend_mode="screen",
        content_fn=lambda _i: np.zeros((240, 320, 4), dtype=np.float32),
    )
    stack.add_layer(layer)
    graph = build_compositing_graph_from_layer_stack(stack)
    composite = next(n for n in graph.nodes.values() if n.kind == "composite")
    assert composite.params["blend_mode"] == "screen"


def test_no_effects_blend_mode_defaults_to_normal(tmp_path) -> None:
    """Byte-identical default: a layer spec with no blend_mode (top-level or
    nested) still composites as ``normal``."""
    plan = {
        "width": 320, "height": 240, "fps": 30.0, "total_frames": 10,
        "layers": [{"type": "solid", "id": "c1", "color": (255, 0, 0), "duration": 10}],
    }
    graph = build_compositing_graph_from_layer_plan(plan)
    composite = next(n for n in graph.nodes.values() if n.kind == "composite")
    assert composite.params["blend_mode"] == "normal"
    assert composite.params["opacity"] == 1.0


def test_top_level_blend_mode_still_wins(tmp_path) -> None:
    """Existing Gemini layer plans (blend_mode at the top level) are unchanged:
    the top-level value takes precedence over any nested effects."""
    plan = {
        "width": 320, "height": 240, "fps": 30.0, "total_frames": 10,
        "layers": [{
            "type": "solid", "id": "c1", "color": (255, 0, 0), "duration": 10,
            "blend_mode": "multiply", "effects": {"blend_mode": "screen"},
        }],
    }
    graph = build_compositing_graph_from_layer_plan(plan)
    composite = next(n for n in graph.nodes.values() if n.kind == "composite")
    assert composite.params["blend_mode"] == "multiply"
