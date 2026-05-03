from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.apple_immersive_foveated import render_apple_immersive_foveated_rendering_manifest


def test_apple_immersive_foveated_manifest_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.apple_immersive_foveated.render_apple_immersive_foveated_rendering_manifest" in catalog_for_prompt("video")


def test_apple_immersive_foveated_manifest_writes_render_passes(tmp_path: Path) -> None:
    clip = _make_video(tmp_path / "immersive.mp4")
    manifest_path = Path(render_apple_immersive_foveated_rendering_manifest(
        [str(clip)],
        str(tmp_path / "manifest"),
        package_id="Apple Immersive Shot",
        device_profile="Vision Pro Review",
        field_of_view_degrees=190,
        render_profiles=[
            {"id": "Preview", "mode": "preview", "eye_buffer": "4096x4096", "foveation": "light"},
            {"id": "Master", "mode": "export", "eye_buffer": "8192x8192", "foveation": "quality", "periphery_scale": 0.8},
        ],
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "resolve21_apple_immersive_foveated_rendering_manifest"
    assert manifest["package"]["package_id"] == "apple_immersive_shot"
    assert manifest["package"]["field_of_view_degrees"] == 190
    assert manifest["render_passes"][0]["resolve_controls"]["preview_control"] is True
    assert manifest["render_passes"][1]["validation"]["export_ready"] is True
    assert manifest["render_passes"][1]["foveation_map"]["periphery_region"]["quality_scale"] == 0.8


def test_apple_immersive_foveated_manifest_validation_and_defaults(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_apple_immersive_foveated_rendering_manifest([], str(tmp_path / "out"))
    with pytest.raises(FileNotFoundError):
        render_apple_immersive_foveated_rendering_manifest([str(tmp_path / "missing.mp4")], str(tmp_path / "out"))
    clip = _make_video(tmp_path / "default.mp4")
    manifest_path = Path(render_apple_immersive_foveated_rendering_manifest(
        [str(clip)],
        str(tmp_path / "out"),
        field_of_view_degrees=360,
        render_profiles=[{"id": "Odd Profile", "mode": "bad", "eye_buffer": "broken", "periphery_scale": 2.5}],
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["package"]["field_of_view_degrees"] == 220
    profile = manifest["render_passes"][0]["profile"]
    assert profile["profile_id"] == "odd_profile"
    assert profile["mode"] == "preview"
    assert profile["eye_buffer_width"] == 4320
    assert profile["periphery_scale"] == 1.0


def test_apple_immersive_foveated_manifest_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_apple_immersive")
    assert manifest["sources"][0]["source_probe"]["duration"] > 0
    assert manifest["render_passes"][0]["clip_asset_refs"] == [manifest["sources"][0]["asset_ref"]]


def test_apple_immersive_foveated_manifest_reproduces_with_two_real_clips(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "pair_apple_immersive")
    assert manifest["package"]["clip_count"] == 2
    refs = {source["asset_ref"] for source in manifest["sources"]}
    for render_pass in manifest["render_passes"]:
        assert set(render_pass["clip_asset_refs"]) == refs
        assert render_pass["foveation_map"]["center_region"]["quality_scale"] == 1.0


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(render_apple_immersive_foveated_rendering_manifest([str(path) for path in paths], str(output_dir)))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        assert source["source_probe"]["width"] > 0
        assert source["asset_ref"]
        assert source["immersive_readiness"]["supports_preview"] is True
    return manifest


def _make_video(path: Path) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=s=240x120:r=12:d=1.0",
            "-f", "lavfi", "-i", "sine=frequency=880:duration=1.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path
