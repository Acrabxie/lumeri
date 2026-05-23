from __future__ import annotations
import json
import subprocess
from pathlib import Path
import pytest
from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.keyframes_curves_manifest import render_keyframes_curves_loop_pingpong_manifest
def test_keyframes_curves_manifest_is_planner_visible() -> None:
    clear_catalog_cache()
    assert (
        "gemia.video.keyframes_curves_manifest.render_keyframes_curves_loop_pingpong_manifest"
        in catalog_for_prompt("video")
    )
def test_keyframes_curves_manifest_normalizes_modes_bezier_and_offsets(tmp_path: Path) -> None:
    clip = _make_video(tmp_path / "curve.mp4")
    manifest_path = Path(
        render_keyframes_curves_loop_pingpong_manifest(
            [str(clip)],
            str(tmp_path / "curves"),
            package_id="CurveLoopProject",
            preset_name="PingPongReview",
            curve_tracks={
                "OpacityPulse": {
                    "parameter": "Opacity",
                    "mode": "PingPong",
                    "curve_editor": "EditPageCurves",
                    "keyframes": [
                        {"time_fraction": -1, "value": -20000, "bezier_handles": [-1, 0.2, 0.8, 2]},
                        {"time_fraction": 2, "value": 20000, "easing": "bad"},
                    ],
                }
            },
            clip_offsets=[12.0],
        )
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "resolve21_keyframes_curves_loop_pingpong_manifest"
    assert manifest["schema_version"] == 1
    assert manifest["package"]["package_id"] == "curve_loop_project"
    track = manifest["curve_tracks"][0]
    assert track["id"] == "opacity_pulse"
    assert track["parameter"] == "opacity"
    assert track["mode"] == "pingpong"
    assert track["curve_editor"] == "edit_page_curves"
    assert track["keyframes"][0]["time_fraction"] == 0.0
    assert track["keyframes"][0]["value"] == -10000.0
    assert track["keyframes"][0]["easing"] == "bezier(0,0.2,0.8,1)"
    assert track["keyframes"][1]["time_fraction"] == 1.0
    assert track["keyframes"][1]["value"] == 10000.0
    assert track["keyframes"][1]["easing"] == "linear"
    assignment = manifest["clip_assignments"][0]
    assert assignment["timeline_offset_seconds"] == 12.0
    assert assignment["analysis_window"]["estimated_frames"] >= 1
    adjusted = assignment["adjusted_curve_tracks"][0]["keyframes"]
    assert adjusted[0]["timeline_seconds"] == 12.0
    assert adjusted[-1]["timeline_seconds"] > 12.0
def test_keyframes_curves_manifest_rejects_bad_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_keyframes_curves_loop_pingpong_manifest([], str(tmp_path))
    with pytest.raises(FileNotFoundError):
        render_keyframes_curves_loop_pingpong_manifest([str(tmp_path / "missing.mp4")], str(tmp_path))
    directory = tmp_path / "folder"
    directory.mkdir()
    with pytest.raises(OSError):
        render_keyframes_curves_loop_pingpong_manifest([str(directory)], str(tmp_path))
    audio_file = tmp_path / "audio.mp3"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5", "-c:a", "libmp3lame", str(audio_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    with pytest.raises(ValueError, match="visual media"):
        render_keyframes_curves_loop_pingpong_manifest([str(audio_file)], str(tmp_path))
def test_keyframes_curves_manifest_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_curves")
    assert manifest["package"]["clip_count"] == 1
    assert {track["mode"] for track in manifest["curve_tracks"]} >= {"loop", "pingpong", "relative"}
    assert manifest["clip_assignments"][0]["adjusted_curve_tracks"][0]["keyframes"][0]["timeline_seconds"] == 0.0
def test_keyframes_curves_manifest_reproduces_with_two_real_clips(tmp_path: Path) -> None:
    manifest = _run_real_repro(
        [Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")],
        tmp_path / "pair_curves",
    )
    assert manifest["package"]["clip_count"] == 2
    assert len(manifest["clip_assignments"]) == 2
    assert manifest["clip_assignments"][1]["timeline_offset_seconds"] > 0
    assert {item["asset_ref"] for item in manifest["clip_assignments"]} == {
        source["asset_ref"] for source in manifest["sources"]
    }
def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(
        render_keyframes_curves_loop_pingpong_manifest([str(path) for path in paths], str(output_dir))
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
            "-f", "lavfi", "-i", "testsrc=s=320x180:r=24:d=2.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path
