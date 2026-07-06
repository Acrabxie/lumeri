from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from gemia import accounts
from gemia.media_library import import_media
from gemia.project_export import _apply_overlays, _render_lottie_sequence_for_clip
from gemia.project_store import ProjectHandle
from gemia.tools import DISPATCHER
from gemia.tools._context import AssetRegistry, ToolContext
from gemia.video.timeline_assets import media_kind_for_path, probe_media


def _patch_account_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "accounts"
    monkeypatch.setattr(accounts, "ACCOUNTS_ROOT", root)
    monkeypatch.setattr(accounts, "ACTIVE_ACCOUNT_PATH", root / "active.json")
    monkeypatch.setattr(accounts, "CONFIG_PATH", tmp_path / "config.json")


def _write_lottie(path: Path) -> Path:
    data = {
        "v": "5.10.0",
        "fr": 12,
        "ip": 0,
        "op": 24,
        "w": 96,
        "h": 54,
        "layers": [
            {
                "ty": 4,
                "ip": 0,
                "op": 24,
                "ks": {
                    "o": {"k": 100},
                    "p": {"k": [48, 27, 0]},
                    "s": {"k": [100, 100, 100]},
                },
                "shapes": [
                    {
                        "ty": "gr",
                        "it": [
                            {"ty": "rc", "s": {"k": [40, 20]}, "p": {"k": [0, 0]}, "r": {"k": 4}},
                            {"ty": "fl", "c": {"k": [0.1, 0.8, 0.7, 1]}, "o": {"k": 100}},
                        ],
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _ctx(tmp_path: Path) -> ToolContext:
    registry = AssetRegistry()
    handle = ProjectHandle.open(tmp_path / "project", "v3-lottie-test", session_id="v3-lottie-test")
    return ToolContext(
        session_id="v3-lottie-test",
        output_dir=tmp_path,
        registry=registry,
        emit_progress=lambda _u: None,
        project=handle,
    )


def _call(verb: str, args: dict, ctx: ToolContext) -> dict:
    return asyncio.run(DISPATCHER[verb](args, ctx))


def test_lottie_probe_and_media_library_import(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_account_roots(monkeypatch, tmp_path)
    lottie = _write_lottie(tmp_path / "badge.json")

    assert media_kind_for_path(lottie) == "lottie"
    meta = probe_media(str(lottie))
    assert meta["media_kind"] == "lottie"
    assert meta["duration"] == pytest.approx(2.0)
    assert meta["frames"] == 24

    asset = import_media("google_account_one", lottie)
    assert asset["media_kind"] == "lottie"
    assert asset["duration"] == pytest.approx(2.0)
    assert asset["thumbnails"]
    assert asset["metadata"]["frames"] == 24


def test_inspect_lottie_registers_frame_asset(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    source = _write_lottie(tmp_path / "badge.json")
    aid = ctx.registry.add_external(source, summary="badge lottie").asset_id

    out = _call("inspect_lottie", {"asset_id": aid, "time_sec": 0.5}, ctx)

    assert out["source_asset_id"] == aid
    assert out["frame"] == 6
    assert out["thumbnail_for_next_message"] is True
    assert ctx.registry.contains(out["asset_id"])
    assert ctx.registry.get(out["asset_id"]).path.exists()


def test_timeline_insert_lottie_uses_overlay_track_and_real_duration(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    source = _write_lottie(tmp_path / "badge.json")
    aid = ctx.registry.add_external(source, summary="badge lottie").asset_id

    out = _call("timeline_insert_clip", {"asset_id": aid}, ctx)

    assert out["track_id"] == "OV1"
    clip = ctx.project.load()["timeline"]["clips"][0]
    assert clip["media_kind"] == "lottie"
    assert clip["duration"] == pytest.approx(2.0)
    tracks = {track["id"]: track for track in ctx.project.load()["timeline"]["tracks"]}
    assert tracks["OV1"]["kind"] == "overlay"


def test_lottie_export_sequence_renders_requested_project_frames(tmp_path: Path) -> None:
    source = _write_lottie(tmp_path / "badge.json")
    pattern = _render_lottie_sequence_for_clip(
        source,
        {"duration": 0.5, "source_in": 0.0},
        str(tmp_path / "seq"),
        width=96,
        height=54,
        fps=10,
    )

    assert pattern.endswith("frame_%05d.png")
    assert len(sorted((tmp_path / "seq").glob("frame_*.png"))) == 5


def test_lottie_overlay_participates_in_ffmpeg_export(tmp_path: Path) -> None:
    base = tmp_path / "base.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=96x54:d=0.6:r=10",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(base),
        ],
        capture_output=True,
        check=True,
    )
    lottie = _write_lottie(tmp_path / "badge.json")
    output = tmp_path / "out.mp4"

    _apply_overlays(
        base,
        [
            {
                "id": "clip_lottie",
                "asset_id": "asset_lottie",
                "media_kind": "lottie",
                "start": 0.0,
                "duration": 0.5,
                "source_in": 0.0,
                "source_out": 0.5,
                "effects": {"x": 0, "y": 0, "scale": 1, "opacity": 1},
            }
        ],
        {"asset_lottie": {"source_path": str(lottie)}},
        output=output,
        width=96,
        height=54,
        fps=10,
        quality="draft",
        timeout_sec=30,
    )

    assert output.exists()
    assert output.stat().st_size > 0
