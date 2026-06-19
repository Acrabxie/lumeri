"""M8 regression: OTIO interchange file formats.

Two concerns:
  * M8-C — otioz/otiod bundles actually contain the referenced media, and a
    missing/non-file media reference is dropped (MissingIfNotFile) rather than
    crashing the export.
  * M8-D — fidelity matrix: every available adapter round-trips a non-trivial
    project to a file and OTIO reads it back; lossless formats preserve clip
    count/order/timing + lumeri metadata (incl. duck_under); lossy formats
    degrade gracefully (no crash, cut points survive). EDL/FCP tests SKIP when
    the optional `interop` plugins are absent, so the suite stays green on a
    clean machine.
"""
from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path
from typing import Any

import opentimelineio as otio
import pytest

from gemia.project_model import empty_project, normalize_project
from lumerai.otio_adapter import (
    available_formats,
    format_extension,
    write_project_to_file,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _real_video(tmp_path: Path, name: str, duration: float = 2.0) -> Path:
    out = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"testsrc2=duration={duration}:size=128x128:rate=15",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
        capture_output=True, check=True,
    )
    return out


def _video_asset(asset_id: str, path: Path, duration: float = 2.0) -> dict[str, Any]:
    return {
        "id": asset_id, "asset_id": asset_id, "name": path.name,
        "media_kind": "video", "mime_type": "video/mp4",
        "source_path": str(path), "duration": duration, "metadata": {},
    }


def _video_clip(clip_id: str, asset_id: str, name: str, *, start: float, duration: float,
                source_in: float = 0.0, track_id: str = "V1") -> dict[str, Any]:
    return {
        "id": clip_id, "asset_id": asset_id, "track_id": track_id, "name": name,
        "media_kind": "video", "start": start, "duration": duration,
        "source_in": source_in, "source_out": source_in + duration, "enabled": True,
    }


def _single_video_project(media: Path) -> dict[str, Any]:
    p = empty_project(title="Bundle")
    p["assets"] = [_video_asset("v1", media)]
    p["timeline"]["clips"] = [_video_clip("c1", "v1", media.name, start=0.0, duration=2.0)]
    return normalize_project(p)


# ── M8-C: media bundles (otioz / otiod) ─────────────────────────────────────


def test_otioz_bundles_referenced_media(tmp_path: Path) -> None:
    media = _real_video(tmp_path, "clip.mp4")
    out = tmp_path / "bundle.otioz"
    write_project_to_file(_single_video_project(media), out, "otioz")

    assert out.exists()
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert "content.otio" in names
    assert any(n.endswith(".mp4") for n in names), f"media not bundled: {names}"


def test_otiod_bundles_referenced_media(tmp_path: Path) -> None:
    media = _real_video(tmp_path, "clip.mp4")
    out = tmp_path / "bundle.otiod"
    write_project_to_file(_single_video_project(media), out, "otiod")

    assert out.is_dir()
    files = [str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()]
    assert any(f.endswith("content.otio") for f in files)
    assert any(f.endswith(".mp4") for f in files), f"media not bundled: {files}"


def test_otioz_missing_media_is_graceful(tmp_path: Path) -> None:
    """A clip whose source file does not exist must not crash the bundle export;
    the bundle is written without that media (MissingIfNotFile)."""
    project = _single_video_project(tmp_path / "does_not_exist.mp4")
    out = tmp_path / "bundle_missing.otioz"
    write_project_to_file(project, out, "otioz")  # must not raise

    assert out.exists()
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert "content.otio" in names
    assert not any(n.endswith(".mp4") for n in names)
