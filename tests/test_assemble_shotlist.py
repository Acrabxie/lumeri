"""assemble_shotlist: the full outline→timeline spine, end to end.

Drives the real dispatchers the way the agent loop would: draft a shotlist,
fill shots with real (registered) footage, assemble onto the timeline, and
assert clips land in order with the planned durations, aligned text overlays,
and the shots marked ``placed``. Also covers skip-of-unfilled and rebuild.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

from gemia.project_model import iter_shots
from gemia.project_store import ProjectHandle
from gemia.tools import DISPATCHER
from gemia.tools._context import AssetRegistry, ToolContext


def _make_clip(path: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=duration=2:size=96x54:rate=15",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return path


def _ctx(tmp_path: Path) -> ToolContext:
    handle = ProjectHandle.open(tmp_path / "project", "v3-assemble", session_id="v3-assemble")
    return ToolContext(
        session_id="v3-assemble",
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _u: None,
        project=handle,
    )


def _call(verb: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return asyncio.run(DISPATCHER[verb](args, ctx))


def _clips(ctx: ToolContext) -> list[dict[str, Any]]:
    return ctx.project.load()["timeline"]["clips"]


def _fill_and_seed(ctx: ToolContext, tmp_path: Path) -> tuple[str, str]:
    a = ctx.registry.add_external(_make_clip(tmp_path / "hook.mp4"), summary="hook").asset_id
    b = ctx.registry.add_external(_make_clip(tmp_path / "reveal.mp4"), summary="reveal").asset_id
    _call("set_shotlist", {"shotlist": {
        "logline": "promo",
        "scenes": [{"id": "sc1", "title": "Main", "shots": [
            {"id": "hook", "description": "opening", "duration_sec": 2,
             "on_screen_text": "Hello", "transition_after": {"kind": "dissolve", "duration_sec": 0.5}},
            {"id": "reveal", "description": "the reveal", "duration_sec": 3},
            {"id": "outro", "description": "logo card", "duration_sec": 2},  # left unfilled
        ]}],
    }}, ctx)
    _call("update_shot", {"shot_id": "hook", "fields": {"asset_id": a, "source": "search", "status": "filled"}}, ctx)
    _call("update_shot", {"shot_id": "reveal", "fields": {"asset_id": b, "source": "search", "status": "filled"}}, ctx)
    return a, b


def test_assemble_is_real_not_stub():
    assert "assemble_shotlist" in DISPATCHER
    assert "stub" not in DISPATCHER["assemble_shotlist"].__qualname__.lower()


def test_full_spine_places_filled_shots(tmp_path):
    ctx = _ctx(tmp_path)
    _fill_and_seed(ctx, tmp_path)

    out = _call("assemble_shotlist", {}, ctx)
    assert out["assembled"] == 2, out
    # the unfilled 'outro' shot is reported, not silently dropped
    assert any(s["shot_id"] == "outro" for s in out["skipped"])

    clips = _clips(ctx)
    video = [c for c in clips if c["track_id"] == "V1"]
    assert len(video) == 2
    # placed in scene order, each trimmed to its planned duration
    assert round(video[0]["duration"], 1) == 2.0
    assert round(video[1]["duration"], 1) == 3.0
    # video clips are sequential (second starts at/after first ends)
    assert video[1]["start"] >= video[0]["start"]
    # the on_screen_text became an aligned text overlay clip
    assert any(c.get("media_kind") == "text" and
               (c.get("text_config") or {}).get("content") == "Hello" for c in clips)

    # shots are marked placed with their clip_id
    shotlist = ctx.project.load()["shotlist"]
    by_id = {s["id"]: s for _sc, s in iter_shots(shotlist)}
    assert by_id["hook"]["status"] == "placed" and by_id["hook"]["clip_id"]
    assert by_id["reveal"]["status"] == "placed"
    assert by_id["outro"]["status"] == "draft"  # untouched


def test_rebuild_clears_and_reassembles(tmp_path):
    ctx = _ctx(tmp_path)
    _fill_and_seed(ctx, tmp_path)
    _call("assemble_shotlist", {}, ctx)
    first_video = [c for c in _clips(ctx) if c["track_id"] == "V1"]
    assert len(first_video) == 2

    # re-running without rebuild should place nothing new (both already placed)
    again = _call("assemble_shotlist", {}, ctx)
    assert again["assembled"] == 0
    assert len([c for c in _clips(ctx) if c["track_id"] == "V1"]) == 2

    # rebuild clears then reassembles to the same 2 video clips (no duplication)
    rebuilt = _call("assemble_shotlist", {"rebuild": True}, ctx)
    assert rebuilt["assembled"] == 2
    assert len([c for c in _clips(ctx) if c["track_id"] == "V1"]) == 2


def test_empty_shotlist_is_noop(tmp_path):
    ctx = _ctx(tmp_path)
    out = _call("assemble_shotlist", {}, ctx)
    assert out["assembled"] == 0
    assert "set_shotlist" in out["summary"]
