from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.mainconcept_delivery import render_mainconcept_h265_mvhevc_delivery_manifest


def test_mainconcept_h265_mvhevc_delivery_manifest_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.mainconcept_delivery.render_mainconcept_h265_mvhevc_delivery_manifest" in catalog_for_prompt("video")


def test_mainconcept_h265_mvhevc_delivery_manifest_writes_profiles(tmp_path: Path) -> None:
    clip = _make_video(tmp_path / "master.mp4", size="384x216")
    manifest_path = Path(render_mainconcept_h265_mvhevc_delivery_manifest(
        [str(clip)],
        str(tmp_path / "delivery"),
        package_id="MainConcept Spot",
        target_platforms=["Vision Pro", "Archive Master"],
        delivery_profiles=[
            {"id": "H265 HDR", "codec": "hevc", "container": "mp4", "bit_depth": 10, "target_bitrate_mbps": 55},
            {"id": "MV HEVC", "codec": "mv_hevc", "container": "mov", "view_count": 2, "gop_seconds": 0.5},
        ],
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "resolve21_mainconcept_h265_mvhevc_delivery_manifest"
    assert manifest["package"]["package_id"] == "mainconcept_spot"
    assert manifest["package"]["deliverable_count"] == 2
    assert manifest["deliverables"][0]["mainconcept_settings"]["codec"] == "h265"
    assert manifest["deliverables"][0]["mainconcept_settings"]["target_bitrate_mbps"] == 55
    assert manifest["deliverables"][1]["resolve_controls"]["codec_menu"] == "MV-HEVC"
    assert manifest["deliverables"][1]["validation"]["mv_hevc_ready"] is True


def test_mainconcept_h265_mvhevc_delivery_manifest_validation_and_defaults(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_mainconcept_h265_mvhevc_delivery_manifest([], str(tmp_path / "out"))
    with pytest.raises(FileNotFoundError):
        render_mainconcept_h265_mvhevc_delivery_manifest([str(tmp_path / "missing.mp4")], str(tmp_path / "out"))
    clip = _make_video(tmp_path / "default.mp4", size="320x180")
    manifest_path = Path(render_mainconcept_h265_mvhevc_delivery_manifest(
        [str(clip)],
        str(tmp_path / "out"),
        delivery_profiles=[{"id": "", "codec": "bad", "container": "avi", "target_bitrate_mbps": 999, "gop_seconds": 0}],
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    settings = manifest["deliverables"][0]["mainconcept_settings"]
    assert settings["codec"] == "h265"
    assert settings["container"] == "mp4"
    assert settings["target_bitrate_mbps"] == 300
    assert settings["gop_seconds"] == 1.0


def test_mainconcept_h265_mvhevc_delivery_manifest_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_mainconcept")
    assert manifest["sources"][0]["source_probe"]["duration"] > 0
    assert manifest["deliverables"][0]["clip_asset_refs"] == [manifest["sources"][0]["asset_ref"]]


def test_mainconcept_h265_mvhevc_delivery_manifest_reproduces_with_two_real_clips(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "pair_mainconcept")
    assert manifest["package"]["clip_count"] == 2
    refs = {source["asset_ref"] for source in manifest["sources"]}
    for deliverable in manifest["deliverables"]:
        assert set(deliverable["clip_asset_refs"]) == refs
        assert deliverable["validation"]["source_count"] == 2


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(render_mainconcept_h265_mvhevc_delivery_manifest([str(path) for path in paths], str(output_dir)))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        assert source["source_probe"]["width"] > 0
        assert source["asset_ref"]
        assert source["encode_readiness"]["has_video"] is True
    return manifest


def _make_video(path: Path, *, size: str) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=s={size}:r=12:d=1.0",
            "-f", "lavfi", "-i", "sine=frequency=660:duration=1.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path
