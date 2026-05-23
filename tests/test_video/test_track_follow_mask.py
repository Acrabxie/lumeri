from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.track_follow_mask import render_track_follow_objects_mask_manifest


def _make_video(path: Path) -> Path:
    """Helper to create a dummy video file for testing."""
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=s=320x180:r=24:d=2.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    """Helper to run real reproductions and return the parsed manifest."""
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(
        render_track_follow_objects_mask_manifest([str(path) for path in paths], str(output_dir))
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        assert Path(source["source_path"]).exists()
        assert source["source_probe"]["media_kind"] == "video"
        assert source["cache_key"]
    return manifest


def test_track_follow_mask_is_planner_visible() -> None:
    """Verify the new function is discoverable by the planner."""
    clear_catalog_cache()
    assert (
        "gemia.video.track_follow_mask.render_track_follow_objects_mask_manifest"
        in catalog_for_prompt("video")
    )


def test_track_follow_mask_writes_bounded_targets_and_normalizes_ids(tmp_path: Path) -> None:
    """Test value clamping, ID normalization, and default values."""
    clip = _make_video(tmp_path / "test_clip.mp4")
    manifest_path = Path(
        render_track_follow_objects_mask_manifest(
            [str(clip)],
            str(tmp_path / "tfm_out"),
            package_id="MaskTrackerProject",
            preset_name="ReviewPreset",
            track_windows={
                "MyRunner": {
                    "target_kind": "person",
                    "mask_shape": "ellipse",
                    "start_rect": [-0.1, 0.2, 1.2, 0.7],  # Test clamping
                    "follow_mode": "planar",
                    "tracking_quality": 6,  # Test clamping
                    "softness": 1.5,  # Test clamping
                    "effect_target": "blur",
                },
                "AnotherObject": {  # Test default values for missing fields
                    "target_kind": "object",
                    "start_rect": [0.1, 0.1, 0.3, 0.3],
                    "tracking_quality": 1,
                },
                "FaceTrack": {
                    "target_kind": "Face",  # Test CamelCase to snake_case
                    "mask_shape": "rectangle",
                    "start_rect": [0.4, 0.4, 0.2, 0.2],
                    "follow_mode": "point",
                }
            },
        )
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "resolve21_track_follow_objects_mask_manifest"
    assert manifest["schema_version"] == 1
    assert manifest["package"]["package_id"] == "mask_tracker_project"
    assert manifest["package"]["preset_name"] == "review_preset"
    assert manifest["sources"][0]["source_probe"]["width"] > 0
    assert manifest["sources"][0]["cache_key"]
    assert manifest["sources"][0]["asset_ref"]

    windows = manifest["track_windows"]
    assert len(windows) == 3

    # Check MyRunner window
    my_runner = next(w for w in windows if w["id"] == "my_runner")
    assert my_runner["id"] == "my_runner"
    assert my_runner["label"] == "MyRunner"  # Original label preserved
    assert my_runner["target_kind"] == "person"
    assert my_runner["mask_shape"] == "ellipse"
    assert my_runner["start_rect"] == [0.0, 0.2, 1.0, 0.7]  # Clamped values
    assert my_runner["follow_mode"] == "planar"
    assert my_runner["tracking_quality"] == 5  # Clamped
    assert my_runner["softness"] == 1.0  # Clamped
    assert my_runner["effect_target"] == "blur"

    # Check AnotherObject window (defaults)
    another_object = next(w for w in windows if w["id"] == "another_object")
    assert another_object["id"] == "another_object"
    assert another_object["label"] == "AnotherObject"
    assert another_object["target_kind"] == "object"
    assert another_object["mask_shape"] == "rectangle"  # Default
    assert another_object["start_rect"] == [0.1, 0.1, 0.3, 0.3]
    assert another_object["follow_mode"] == "point"  # Default
    assert another_object["tracking_quality"] == 1
    assert another_object["softness"] == 0.2  # Default
    assert another_object["effect_target"] == "color_window"  # Default

    # Check FaceTrack window (CamelCase to snake_case)
    face_track = next(w for w in windows if w["id"] == "face_track")
    assert face_track["id"] == "face_track"
    assert face_track["label"] == "FaceTrack"
    assert face_track["target_kind"] == "face" # Converted
    assert face_track["mask_shape"] == "rectangle"
    assert face_track["start_rect"] == [0.4, 0.4, 0.2, 0.2]
    assert face_track["follow_mode"] == "point"

    assert manifest["clip_assignments"][0]["analysis_window"]["estimated_frames"] >= 1


def test_track_follow_mask_rejects_bad_inputs(tmp_path: Path) -> None:
    """Test various bad input scenarios."""
    with pytest.raises(ValueError, match="input_paths"):
        render_track_follow_objects_mask_manifest([], str(tmp_path))
    with pytest.raises(FileNotFoundError):
        render_track_follow_objects_mask_manifest([str(tmp_path / "missing.mp4")], str(tmp_path))
    directory = tmp_path / "folder"
    directory.mkdir()
    with pytest.raises(OSError):
        render_track_follow_objects_mask_manifest([str(directory)], str(tmp_path))
    
    # Test real non-visual media.
    audio_file = tmp_path / "audio.mp3"
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
            "-c:a", "libmp3lame",
            str(audio_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    with pytest.raises(ValueError, match="visual media"):
         render_track_follow_objects_mask_manifest([str(audio_file)], str(tmp_path))


def test_track_follow_mask_reproduces_with_demo_video(tmp_path: Path) -> None:
    """Reproduce with a single demo video."""
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_tfm")
    assert manifest["package"]["clip_count"] == 1
    assert len(manifest["track_windows"]) == 1 # Default window
    assert manifest["track_windows"][0]["id"] == "default_track_window"


def test_track_follow_mask_reproduces_with_two_real_clips(tmp_path: Path) -> None:
    """Reproduce with two real clips."""
    manifest = _run_real_repro(
        [Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "pair_tfm"
    )
    assert manifest["package"]["clip_count"] == 2
    assert len(manifest["clip_assignments"]) == 2
    assert {item["asset_ref"] for item in manifest["clip_assignments"]} == {
        source["asset_ref"] for source in manifest["sources"]
    }
    assert len(manifest["track_windows"]) == 1 # Default window, assigned to both
    assert manifest["track_windows"][0]["id"] == "default_track_window"
