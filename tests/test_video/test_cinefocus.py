from __future__ import annotations

import json
from pathlib import Path

from gemia.engine import PlanEngine
from gemia.registry import catalog_for_prompt
from gemia.video.cinefocus import render_cinefocus_plan
from gemia.video.review import review_real_media_artifact


def test_cinefocus_is_planner_visible() -> None:
    assert "gemia.video.cinefocus.render_cinefocus_plan" in catalog_for_prompt()


def test_render_cinefocus_plan_writes_focus_metadata(sample_video_path: str, tmp_path: Path) -> None:
    output = tmp_path / "cinefocus.mp4"

    result = render_cinefocus_plan(
        sample_video_path,
        str(output),
        focus_keyframes=[
            {"frame": 0, "x": 0.25, "y": 0.5, "radius": 0.22, "aperture": 0.85},
            {"frame": 18, "x": 0.75, "y": 0.45, "radius": 0.30, "aperture": 0.65},
        ],
        frame_step=2,
        max_long_edge=96,
    )

    metadata = json.loads(output.with_suffix(".cinefocus.json").read_text(encoding="utf-8"))
    assert result == str(output.resolve())
    assert output.exists()
    assert metadata["effect"] == "resolve21_ai_cinefocus"
    assert metadata["rendered_frames"] > 0
    assert metadata["focus_keyframes"][0]["x"] == 0.25
    assert metadata["focal_emphasis_samples"][0]["frame"] == 0


def test_plan_engine_runs_cinefocus_and_real_media_review(sample_video_path: str, tmp_path: Path) -> None:
    output = tmp_path / "engine-cinefocus.mp4"
    catalog = tmp_path / "stock_catalog.json"
    catalog.write_text(
        json.dumps(
            [
                {
                    "id": "video-focus",
                    "kind": "video",
                    "status": "completed",
                    "backend": "local_real_video",
                    "source": sample_video_path,
                    "outputs": [sample_video_path],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    plan = {
        "version": "2.0",
        "goal": "rack focus across a real clip",
        "steps": [
            {
                "id": "focus",
                "function": "gemia.video.cinefocus.render_cinefocus_plan",
                "args": {
                    "focus_x": 0.2,
                    "focus_y": 0.5,
                    "rack_to_x": 0.8,
                    "rack_to_y": 0.5,
                    "aperture": 0.8,
                    "frame_step": 3,
                    "max_long_edge": 96,
                },
                "input": "$input",
                "output": "$output",
            }
        ],
    }

    result = PlanEngine(root_dir=tmp_path / "engine").execute(plan, sample_video_path, str(output))
    review = review_real_media_artifact(
        sample_video_path,
        result,
        report_path=tmp_path / "cinefocus-review.json",
        stock_catalog_path=catalog,
        min_output_frames=2,
    )
    metadata = json.loads(output.with_suffix(".cinefocus.json").read_text(encoding="utf-8"))

    assert review.status == "passed"
    assert any(finding["code"] == "cinefocus_metadata_recorded" for finding in review.findings)
    assert metadata["focus_keyframes"][1]["x"] == 0.8
    assert len(metadata["focus_keyframes"]) == 2
