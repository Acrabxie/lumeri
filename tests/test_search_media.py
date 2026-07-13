"""search_media: FTS semantic search wired end-to-end through ToolContext.

The FTS search_media works over media_annotations — assets must be annotated
before they appear in search results.  This file tests the tool dispatcher
wiring; exhaustive FTS/tokenization/annotation tests live in test_media_search.py.
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


def test_requires_query(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ValueError):
        _call("search_media", {"query": "  "}, ctx)


def test_empty_when_no_annotations(tmp_path):
    ctx = _ctx(tmp_path)
    out = _call("search_media", {"query": "anything"}, ctx)
    assert out["result_count"] == 0


def test_returns_zero_with_registered_but_unannotated_assets(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    clip = _make_clip(media / "city_sunrise.mp4")
    ctx = _ctx(tmp_path)
    ctx.registry.add_external(clip, summary="city sunrise timelapse")
    out = _call("search_media", {"query": "city sunrise"}, ctx)
    assert out["result_count"] == 0
