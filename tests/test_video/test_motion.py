from __future__ import annotations

import json
from pathlib import Path

from gemia.engine import PlanEngine
from gemia.registry import catalog_for_prompt
from gemia.video.motion import (
    render_motion_heatmap,
    render_motion_stabilize,
    render_motion_trails,
)


def test_motion_primitives_are_planner_visible() -> None:
    catalog = catalog_for_prompt()

    assert "gemia.video.motion.render_motion_heatmap" in catalog
    assert "gemia.video.motion.render_motion_trails" in catalog
    assert "gemia.video.motion.render_motion_stabilize" in catalog


def test_render_motion_heatmap_writes_video_and_sidecar(sample_video_path: str, tmp_path: Path) -> None:
    output = tmp_path / "motion-heatmap.mp4"

    result = render_motion_heatmap(
        sample_video_path,
        str(output),
        opacity=0.5,
        gain=2.0,
        frame_step=2,
        max_long_edge=96,
    )
    metadata = json.loads(output.with_suffix(".motion.json").read_text(encoding="utf-8"))

    assert result == str(output)
    assert output.exists()
    assert metadata["effect"] == "gemia_motion_heatmap"
    assert metadata["rendered_frames"] > 0
    assert metadata["metrics"]["average_motion_px"] >= 0
    assert metadata["samples"]


def test_render_motion_trails_writes_video_and_sidecar(sample_video_path: str, tmp_path: Path) -> None:
    output = tmp_path / "motion-trails.mp4"

    result = render_motion_trails(
        sample_video_path,
        str(output),
        threshold=8,
        frame_step=2,
        max_long_edge=96,
    )
    metadata = json.loads(output.with_suffix(".motion.json").read_text(encoding="utf-8"))

    assert result == str(output)
    assert output.exists()
    assert metadata["effect"] == "gemia_motion_trails"
    assert metadata["rendered_frames"] > 0
    assert metadata["metrics"]["average_motion_coverage"] >= 0


def test_plan_engine_runs_motion_heatmap(sample_video_path: str, tmp_path: Path) -> None:
    output = tmp_path / "engine-motion.mp4"
    plan = {
        "version": "2.0",
        "goal": "show where the clip is moving",
        "steps": [
            {
                "id": "motion_heatmap",
                "function": "gemia.video.motion.render_motion_heatmap",
                "args": {"frame_step": 2, "max_long_edge": 96},
                "input": "$input",
                "output": "$output",
            }
        ],
    }

    result = PlanEngine(root_dir=tmp_path / "engine").execute(plan, sample_video_path, str(output))

    assert result == str(output)
    assert output.exists()
    assert output.with_suffix(".motion.json").exists()


def test_render_motion_stabilize_writes_video_and_sidecar(sample_video_path: str, tmp_path: Path) -> None:
    output = tmp_path / "motion-stabilize.mp4"

    result = render_motion_stabilize(
        sample_video_path,
        str(output),
        smoothing_radius=2,
        crop_zoom=1.01,
        max_features=60,
        max_long_edge=96,
    )
    metadata = json.loads(output.with_suffix(".motion.json").read_text(encoding="utf-8"))

    assert result == str(output)
    assert output.exists()
    assert metadata["effect"] == "gemia_motion_stabilize"
    assert metadata["rendered_frames"] > 0
    assert metadata["metrics"]["tracked_pairs"] >= 0
