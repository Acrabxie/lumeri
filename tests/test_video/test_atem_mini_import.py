from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.atem_mini_import import render_atem_mini_project_import_timeline_manifest


def test_atem_mini_project_import_timeline_manifest_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.atem_mini_import.render_atem_mini_project_import_timeline_manifest" in catalog_for_prompt("video")


def test_atem_mini_project_import_timeline_manifest_writes_editable_timelines(tmp_path: Path) -> None:
    clip_a = _make_video(tmp_path / "cam_a.mp4", size="384x216")
    clip_b = _make_video(tmp_path / "cam_b.mp4", size="320x180")
    manifest_path = Path(render_atem_mini_project_import_timeline_manifest(
        [str(clip_a), str(clip_b)],
        str(tmp_path / "atem"),
        package_id="Live Show",
        project_name="Launch Event",
        camera_labels=["Wide", "Host"],
        switcher_cuts=[{"camera": "wide", "start": 0, "end": 0.75}, {"camera": "host", "start": 0.75, "end": 1.5}],
        relink_policy="asset_ref",
        audio_source="camera_a",
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "resolve21_atem_mini_project_import_timeline_manifest"
    assert manifest["package"]["package_id"] == "live_show"
    assert manifest["package"]["iso_source_count"] == 2
    assert manifest["timelines"]["multicam_timeline"]["editable_after_import"] is True
    assert manifest["timelines"]["program_timeline"]["audio_track_source"] == "camera_a"
    assert manifest["timelines"]["program_timeline"]["edits"][0]["camera_label"] == "wide"
    assert manifest["relink_manifest"]["items"][0]["target_bin"] == "ATEM ISO/wide"


def test_atem_mini_project_import_timeline_manifest_validation_and_clamps(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_atem_mini_project_import_timeline_manifest([], str(tmp_path / "out"))
    with pytest.raises(FileNotFoundError):
        render_atem_mini_project_import_timeline_manifest([str(tmp_path / "missing.mp4")], str(tmp_path / "out"))
    clip = _make_video(tmp_path / "single.mp4", size="320x180")
    manifest_path = Path(render_atem_mini_project_import_timeline_manifest(
        [str(clip)],
        str(tmp_path / "out"),
        relink_policy="bad",
        audio_source="bad",
        switcher_cuts=[{"camera": "", "start": -5, "end": -1}],
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["package"]["relink_policy"] == "asset_ref"
    assert manifest["package"]["audio_source"] == "camera_a"
    assert manifest["timelines"]["program_timeline"]["edits"][0]["end_seconds"] > 0


def test_atem_mini_project_import_timeline_manifest_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_atem")
    assert manifest["iso_sources"][0]["source_probe"]["duration"] > 0
    assert manifest["timelines"]["program_timeline"]["edits"][0]["asset_ref"]


def test_atem_mini_project_import_timeline_manifest_reproduces_with_two_real_clips(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "pair_atem")
    assert manifest["package"]["iso_source_count"] == 2
    assert manifest["timelines"]["multicam_timeline"]["angle_count"] == 2
    refs = {source["asset_ref"] for source in manifest["iso_sources"]}
    assert {edit["asset_ref"] for edit in manifest["timelines"]["program_timeline"]["edits"]} == refs
    assert all(item["ready"] for item in manifest["relink_manifest"]["items"])


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(render_atem_mini_project_import_timeline_manifest([str(path) for path in paths], str(output_dir)))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for source in manifest["iso_sources"]:
        assert source["source_probe"]["width"] > 0
        assert source["asset_ref"]
    return manifest


def _make_video(path: Path, *, size: str) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=s={size}:r=12:d=1.0",
            "-f", "lavfi", "-i", "sine=frequency=700:duration=1.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path
