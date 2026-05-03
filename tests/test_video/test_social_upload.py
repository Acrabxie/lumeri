from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.social_upload import render_social_media_upload_preset_manifest


def test_social_media_upload_preset_manifest_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.social_upload.render_social_media_upload_preset_manifest" in catalog_for_prompt("video")


def test_social_media_upload_preset_manifest_writes_safe_jobs(tmp_path: Path) -> None:
    clip = _make_video(tmp_path / "short.mp4", size="384x216")
    manifest_path = Path(render_social_media_upload_preset_manifest(
        [str(clip)],
        str(tmp_path / "upload"),
        package_id="Upload Presets",
        privacy="unlisted",
        compression_profile="quality",
        platforms=[{"id": "Shorts", "label": "Shorts", "max_width": 720, "max_height": 1280, "max_bitrate_mbps": 9}],
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    job = manifest["upload_jobs"][0]
    assert manifest["effect"] == "resolve21_social_media_upload_preset_manifest"
    assert manifest["package"]["package_id"] == "upload_presets"
    assert manifest["package"]["job_count"] == 1
    assert job["platform"]["platform_id"] == "shorts"
    assert job["metadata_template"]["privacy"] == "unlisted"
    assert job["credential_policy"]["stores_credentials"] is False
    assert job["validation"]["no_credentials_serialized"] is True
    assert "token" not in json.dumps(manifest).lower()


def test_social_media_upload_preset_manifest_validation_and_clamps(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_social_media_upload_preset_manifest([], str(tmp_path / "out"))
    with pytest.raises(FileNotFoundError):
        render_social_media_upload_preset_manifest([str(tmp_path / "missing.mp4")], str(tmp_path / "out"))
    clip = _make_video(tmp_path / "clamp.mp4", size="320x180")
    manifest_path = Path(render_social_media_upload_preset_manifest(
        [str(clip)],
        str(tmp_path / "out"),
        privacy="bad",
        compression_profile="bad",
        platforms=[{"id": "", "max_width": 1, "max_height": 99999, "max_bitrate_mbps": 999, "container": "bad"}],
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    platform = manifest["upload_platforms"][0]
    assert manifest["package"]["privacy"] == "draft"
    assert manifest["package"]["compression_profile"] == "balanced"
    assert platform["platform_id"] == "platform_0"
    assert platform["max_width"] == 240
    assert platform["max_height"] == 4320
    assert platform["max_bitrate_mbps"] == 80
    assert platform["container"] == "mp4"


def test_social_media_upload_preset_manifest_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_upload")
    assert manifest["assets"][0]["upload_readiness"]["duration_seconds"] > 0
    assert manifest["upload_jobs"][0]["validation"]["ready_for_manual_upload"] is True


def test_social_media_upload_preset_manifest_reproduces_with_two_real_clips(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "pair_upload")
    assert manifest["package"]["asset_count"] == 2
    assert manifest["package"]["job_count"] == 8
    refs = {asset["asset_ref"] for asset in manifest["assets"]}
    assert {job["asset_ref"] for job in manifest["upload_jobs"]} == refs
    assert all(job["credential_policy"]["secret_fields"] == [] for job in manifest["upload_jobs"])


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(render_social_media_upload_preset_manifest([str(path) for path in paths], str(output_dir)))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for asset in manifest["assets"]:
        assert asset["source_probe"]["width"] > 0
        assert asset["asset_ref"]
    return manifest


def _make_video(path: Path, *, size: str) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=s={size}:r=12:d=1.0",
            "-f", "lavfi", "-i", "sine=frequency=820:duration=1.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path
