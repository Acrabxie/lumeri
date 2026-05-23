from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.replay_editor_multicam import render_replay_editor_multicam_action_manifest


def _make_video(path: Path) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=s=320x180:r=24:d=2.0",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(
        render_replay_editor_multicam_action_manifest([str(path) for path in paths], str(output_dir))
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        assert Path(source["source_path"]).exists()
        assert source["source_probe"]["media_kind"] == "video"
        assert source["cache_key"]
    return manifest


def test_replay_editor_multicam_manifest_is_planner_visible() -> None:
    clear_catalog_cache()
    assert (
        "gemia.video.replay_editor_multicam.render_replay_editor_multicam_action_manifest"
        in catalog_for_prompt("video")
    )


def test_replay_editor_multicam_writes_bounded_actions_and_camera_angles(tmp_path: Path) -> None:
    clip_a = _make_video(tmp_path / "cam_a.mp4")
    clip_b = _make_video(tmp_path / "cam_b.mp4")
    manifest_path = Path(
        render_replay_editor_multicam_action_manifest(
            [str(clip_a), str(clip_b)],
            str(tmp_path / "replay_out"),
            package_id="ReplayEditorProject",
            preset_name="SportsReview",
            camera_angles={
                "MainWide": {"label": "Main Wide", "role": "Wide", "source_index": 0, "quality_rank": 0},
                "ReactionCam": {"label": "Reaction Cam", "role": "Reaction", "source_index": 1, "quality_rank": 9},
            },
            replay_actions={
                "GoalReplay": {
                    "action_kind": "SlowMotion",
                    "preferred_angle": "MainWide",
                    "pre_roll_seconds": -1,
                    "post_roll_seconds": 45,
                    "replay_speed": 0.02,
                    "priority": 10,
                    "marker_color": "Purple",
                    "review_role": "SportsReplay",
                },
                "ReturnLive": {
                    "action_kind": "LiveReturn",
                    "preferred_angle": "Reaction",
                    "replay_speed": 3.5,
                    "priority": 1,
                    "marker_color": "bad",
                    "review_role": "Producer",
                },
            },
        )
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "resolve21_replay_editor_multicam_action_manifest"
    assert manifest["schema_version"] == 1
    assert manifest["package"]["package_id"] == "replay_editor_project"
    assert manifest["package"]["preset_name"] == "sports_review"
    assert manifest["package"]["clip_count"] == 2
    assert manifest["package"]["angle_count"] == 2
    assert manifest["sources"][0]["source_probe"]["width"] > 0
    assert manifest["sources"][0]["asset_ref"]

    angles = manifest["camera_angles"]
    assert angles[0]["angle_id"] == "main_wide"
    assert angles[0]["label"] == "Main Wide"
    assert angles[0]["role"] == "wide"
    assert angles[0]["quality_rank"] == 1
    assert angles[1]["angle_id"] == "reaction_cam"
    assert angles[1]["role"] == "reaction"
    assert angles[1]["quality_rank"] == 5

    actions = manifest["replay_actions"]
    goal = next(action for action in actions if action["id"] == "goal_replay")
    assert goal["action_kind"] == "slow_motion"
    assert goal["preferred_angle"] == "main_wide"
    assert goal["pre_roll_seconds"] == 0.0
    assert goal["post_roll_seconds"] == 30.0
    assert goal["replay_speed"] == 0.1
    assert goal["priority"] == 5
    assert goal["marker_color"] == "purple"
    assert goal["review_role"] == "sports_replay"

    live = next(action for action in actions if action["id"] == "return_live")
    assert live["action_kind"] == "live_return"
    assert live["marker_color"] == "red"
    assert live["review_role"] == "producer"
    assert live["replay_speed"] == 2.0

    assert len(manifest["replay_segments"]) == 2
    first_segment = manifest["replay_segments"][0]
    assert first_segment["camera_selection"]["angle_id"] == "main_wide"
    assert first_segment["source_range"]["in_seconds"] <= first_segment["source_range"]["out_seconds"]
    assert first_segment["estimated_output_seconds"] > 0
    assert first_segment["analysis_window"]["estimated_frames"] >= 1


def test_replay_editor_multicam_rejects_bad_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_replay_editor_multicam_action_manifest([], str(tmp_path))
    with pytest.raises(FileNotFoundError):
        render_replay_editor_multicam_action_manifest([str(tmp_path / "missing.mp4")], str(tmp_path))
    directory = tmp_path / "folder"
    directory.mkdir()
    with pytest.raises(OSError):
        render_replay_editor_multicam_action_manifest([str(directory)], str(tmp_path))

    audio_file = tmp_path / "audio.mp3"
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.5",
            "-c:a",
            "libmp3lame",
            str(audio_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    with pytest.raises(ValueError, match="visual media"):
        render_replay_editor_multicam_action_manifest([str(audio_file)], str(tmp_path))


def test_replay_editor_multicam_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_replay")
    assert manifest["package"]["clip_count"] == 1
    assert len(manifest["camera_angles"]) == 1
    assert len(manifest["replay_actions"]) == 2
    assert manifest["replay_segments"][0]["action"]["id"] == "instant_replay_slow_push"


def test_replay_editor_multicam_reproduces_with_two_real_clips(tmp_path: Path) -> None:
    manifest = _run_real_repro(
        [Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")],
        tmp_path / "pair_replay",
    )
    assert manifest["package"]["clip_count"] == 2
    assert len(manifest["camera_angles"]) == 2
    assert len(manifest["replay_segments"]) == 2
    assert {item["asset_ref"] for item in manifest["replay_segments"]} == {
        source["asset_ref"] for source in manifest["sources"]
    }
    assert {item["camera_selection"]["role"] for item in manifest["replay_segments"]} == {"wide", "close_up"}
