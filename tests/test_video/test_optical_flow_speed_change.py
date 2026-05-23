from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.optical_flow_speed_change import render_optical_flow_speed_change_manifest


def test_optical_flow_speed_change_manifest_is_planner_visible() -> None:
    clear_catalog_cache()
    assert (
        "gemia.video.optical_flow_speed_change.render_optical_flow_speed_change_manifest"
        in catalog_for_prompt("video")
    )


def test_optical_flow_speed_change_manifest_writes_bounded_targets(tmp_path: Path) -> None:
    clip = _make_video(tmp_path / "fast.mp4")
    manifest_path = Path(
        render_optical_flow_speed_change_manifest(
            [str(clip)],
            str(tmp_path / "of"),
            package_id="OpticalFlow",
            preset_name="SlowMo",
            retime_targets={
                "extreme_slow": {
                    "speed_factor": 0.05,  # Should be clamped to 0.1
                    "interpolation_quality": 10,  # Should be clamped to 5
                    "generated_frame_range": "invalid_range",  # Should default to 'full_clip'
                }
            },
        )
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "resolve21_optical_flow_speed_change_manifest"
    assert manifest["schema_version"] == 1
    assert manifest["package"]["package_id"] == "optical_flow"
    assert manifest["package"]["preset_name"] == "slow_mo"
    assert manifest["sources"][0]["source_probe"]["width"] > 0
    assert manifest["sources"][0]["cache_key"]
    assert manifest["sources"][0]["asset_ref"]

    target = manifest["retime_targets"][0]
    assert target["id"] == "extreme_slow"
    assert target["speed_factor"] == 0.1
    assert target["interpolation_quality"] == 5
    assert target["generated_frame_range"] == "full_clip"
    assert manifest["clip_assignments"][0]["analysis_window"]["estimated_frames"] >= 1


def test_optical_flow_speed_change_manifest_rejects_bad_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_optical_flow_speed_change_manifest([], str(tmp_path))
    with pytest.raises(FileNotFoundError):
        render_optical_flow_speed_change_manifest([str(tmp_path / "missing.mp4")], str(tmp_path))
    directory = tmp_path / "folder"
    directory.mkdir()
    with pytest.raises(OSError):
        render_optical_flow_speed_change_manifest([str(directory)], str(tmp_path))


def test_optical_flow_speed_change_manifest_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_of")
    assert manifest["package"]["clip_count"] == 1
    assert manifest["retime_targets"][0]["speed_factor"] == 0.5  # Default speed factor


def test_optical_flow_speed_change_manifest_reproduces_with_two_real_clips(tmp_path: Path) -> None:
    manifest = _run_real_repro(
        [Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "pair_of"
    )
    assert manifest["package"]["clip_count"] == 2
    assert len(manifest["clip_assignments"]) == 2
    assert {item["asset_ref"] for item in manifest["clip_assignments"]} == {
        source["asset_ref"] for source in manifest["sources"]
    }
    assert manifest["retime_targets"][0]["speed_factor"] == 0.5


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(
        render_optical_flow_speed_change_manifest([str(path) for path in paths], str(output_dir))
    )
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
            "-f", "lavfi", "-i", "testsrc=s=320x180:r=24:d=2.0",  # Increased duration for better flow
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path
