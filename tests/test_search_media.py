"""search_media: semantic footage search wired end-to-end through ToolContext.

intellisearch derives labels from filename + probed visual/dialog signal. For
synthetic testsrc clips the filename is the reliable signal, so these name the
fixtures semantically (``city_sunrise.mp4``) and query against them. We assert
the tool indexes candidate footage, returns the right ranked asset_id, registers
matches into the session registry, and degrades to an empty (non-throwing)
result when there is nothing to search.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest

from gemia.project_store import ProjectHandle
from gemia.tools import DISPATCHER
from gemia.tools._context import AssetRegistry, ToolContext


def _make_clip(path: Path, *, color: str = "blue") -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"testsrc2=duration=0.5:size=96x54:rate=10",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return path


def _ctx(tmp_path: Path) -> ToolContext:
    handle = ProjectHandle.open(tmp_path / "project", "v3-searchmedia", session_id="v3-searchmedia")
    return ToolContext(
        session_id="v3-searchmedia",
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _u: None,
        project=handle,
    )


def _call(verb: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return asyncio.run(DISPATCHER[verb](args, ctx))


def test_search_media_is_real_not_stub():
    assert "search_media" in DISPATCHER
    assert "stub" not in DISPATCHER["search_media"].__qualname__.lower()


def test_finds_and_registers_named_footage(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    city = _make_clip(media / "city_sunrise_timelapse.mp4")
    office = _make_clip(media / "office_meeting_indoor.mp4")
    ctx = _ctx(tmp_path)
    # register both into the session registry so they are candidates
    for p in (city, office):
        ctx.registry.add_external(p, summary=p.stem)

    out = _call("search_media", {"query": "city sunrise", "kind": "video"}, ctx)
    assert out["result_count"] >= 1, out
    top = out["results"][0]
    # the winning asset resolves to the city clip in the registry
    assert ctx.registry.get(top["asset_id"]).path.name == "city_sunrise_timelapse.mp4"
    # re-searching reuses the same session asset_id (no double-register)
    out2 = _call("search_media", {"query": "city sunrise", "kind": "video"}, ctx)
    assert out2["results"][0]["asset_id"] == top["asset_id"]


def test_extra_paths_are_searchable(tmp_path):
    clip = _make_clip(tmp_path / "mountain_river_drone.mp4")
    ctx = _ctx(tmp_path)  # nothing pre-registered
    out = _call("search_media", {"query": "mountain river", "paths": [str(clip)]}, ctx)
    assert out["result_count"] >= 1
    assert ctx.registry.get(out["results"][0]["asset_id"]).path.name == "mountain_river_drone.mp4"


def test_empty_when_no_candidates(tmp_path):
    ctx = _ctx(tmp_path)
    out = _call("search_media", {"query": "anything"}, ctx)
    assert out["result_count"] == 0
    assert "generate" in out["summary"].lower()  # steers the model to generate instead


def test_requires_query(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ValueError):
        _call("search_media", {"query": "  "}, ctx)
