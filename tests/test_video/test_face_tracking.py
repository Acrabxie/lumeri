from __future__ import annotations

import json
from pathlib import Path

from gemia.engine import PlanEngine
from gemia.registry import catalog_for_prompt
from gemia.video.face_tracking import render_face_tracking_plan


def test_face_tracking_is_planner_visible() -> None:
    assert "gemia.video.face_tracking.render_face_tracking_plan" in catalog_for_prompt()


def test_render_face_tracking_plan_writes_preview_and_metadata(sample_video_path: str, tmp_path: Path) -> None:
    output = tmp_path / "face-tracking.mp4"

    result = render_face_tracking_plan(sample_video_path, str(output), max_long_edge=128)

    assert result == str(output.resolve())
    assert output.exists()
    metadata = json.loads(output.with_suffix(".face_tracking.json").read_text(encoding="utf-8"))
    assert metadata["effect"] == "lumeri_face_tracking"
    assert metadata["target"] == "most_prominent_face"
    assert metadata["time_scope"] == "full_clip"
    assert metadata["face_detection"]["frames_with_faces"] >= 0
    assert metadata["tracking"]["default_target_policy"] == "largest detected face per sampled frame"


def test_plan_engine_runs_face_tracking_without_clarification_slots(sample_video_path: str, tmp_path: Path) -> None:
    output = tmp_path / "engine-face-tracking.mp4"
    plan = {
        "version": "2.1",
        "goal": "人脸跟踪",
        "steps": [
            {
                "id": "face_tracking",
                "function": "gemia.video.face_tracking.render_face_tracking_plan",
                "args": {"target": "most_prominent_face", "overlay": True, "trail": True},
                "input": "$input",
                "output": "$output",
            }
        ],
    }

    result = PlanEngine(root_dir=tmp_path / "engine").execute(plan, sample_video_path, str(output))

    assert result == str(output)
    assert output.exists()
    assert output.with_suffix(".face_tracking.json").exists()
