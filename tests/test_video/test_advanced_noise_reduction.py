from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.advanced_noise_reduction import render_advanced_noise_reduction_profile_manifest


def test_advanced_noise_reduction_profile_manifest_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.advanced_noise_reduction.render_advanced_noise_reduction_profile_manifest" in catalog_for_prompt("video")


def test_advanced_noise_reduction_profile_manifest_writes_bounded_profiles(tmp_path: Path) -> None:
    clip = _make_video(tmp_path / "noisy.mp4")
    manifest_path = Path(render_advanced_noise_reduction_profile_manifest(
        [str(clip)],
        str(tmp_path / "nr"),
        package_id="NR Review",
        profile_name="Night Street",
        profiles={
            "aggressive": {
                "temporal_frames": 9,
                "temporal_nr_strength": 1.4,
                "spatial_nr_strength": -0.2,
                "motion_estimation": "better",
            }
        },
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "resolve21_advanced_noise_reduction_profile_manifest"
    assert manifest["schema_version"] == 1
    assert manifest["package"]["package_id"] == "nr_review"
    assert manifest["package"]["profile_name"] == "night_street"
    assert manifest["sources"][0]["source_probe"]["width"] > 0
    assert manifest["sources"][0]["cache_key"]
    assert manifest["sources"][0]["asset_ref"]
    profile = manifest["denoise_profiles"][0]
    assert profile["profile_id"] == "aggressive"
    assert profile["temporal"]["frames"] == 5
    assert profile["temporal"]["strength"] == 1.0
    assert profile["spatial"]["strength"] == 0.0
    assert manifest["clip_assignments"][0]["analysis_window"]["estimated_frames"] >= 1


def test_advanced_noise_reduction_profile_manifest_rejects_bad_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_advanced_noise_reduction_profile_manifest([], str(tmp_path))
    with pytest.raises(FileNotFoundError):
        render_advanced_noise_reduction_profile_manifest([str(tmp_path / "missing.mp4")], str(tmp_path))
    directory = tmp_path / "folder"
    directory.mkdir()
    with pytest.raises(OSError):
        render_advanced_noise_reduction_profile_manifest([str(directory)], str(tmp_path))


def test_advanced_noise_reduction_profile_manifest_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_nr")
    assert manifest["package"]["clip_count"] == 1
    assert manifest["denoise_profiles"][0]["temporal"]["enabled"] is True


def test_advanced_noise_reduction_profile_manifest_reproduces_with_two_real_clips(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "pair_nr")
    assert manifest["package"]["clip_count"] == 2
    assert len(manifest["clip_assignments"]) == 2
    assert {item["asset_ref"] for item in manifest["clip_assignments"]} == {source["asset_ref"] for source in manifest["sources"]}


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(render_advanced_noise_reduction_profile_manifest([str(path) for path in paths], str(output_dir)))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        assert Path(source["source_path"]).exists()
        assert source["source_probe"]["media_kind"] == "video"
        assert source["cache_key"]
    return manifest


def _make_video(path: Path) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=s=320x180:r=12:d=1.0",
            "-f", "lavfi", "-i", "anoisesrc=color=pink:duration=1.0:amplitude=0.02",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path
