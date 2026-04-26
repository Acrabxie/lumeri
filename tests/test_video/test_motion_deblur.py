from __future__ import annotations

import json
from pathlib import Path

from gemia.engine import PlanEngine
from gemia.registry import catalog_for_prompt
from gemia.video.motion_deblur import render_motion_deblur_plan
from gemia.video.review import review_real_media_artifact


def test_motion_deblur_is_planner_visible() -> None:
    assert "gemia.video.motion_deblur.render_motion_deblur_plan" in catalog_for_prompt()


def test_render_motion_deblur_plan_writes_sharpness_metadata(sample_video_path: str, tmp_path: Path) -> None:
    output = tmp_path / "motion-deblur.mp4"

    result = render_motion_deblur_plan(
        sample_video_path,
        str(output),
        strength=0.8,
        blur_radius=5,
        frame_step=2,
        max_long_edge=96,
    )

    metadata = json.loads(output.with_suffix(".motion_deblur.json").read_text(encoding="utf-8"))
    assert result == str(output.resolve())
    assert output.exists()
    assert metadata["effect"] == "resolve21_ai_motion_deblur"
    assert metadata["rendered_frames"] > 0
    assert metadata["sharpness"]["output_laplacian_variance"] >= metadata["sharpness"]["input_laplacian_variance"]
    assert metadata["samples"][0]["frame"] == 0


def test_plan_engine_runs_motion_deblur_and_real_media_review(sample_video_path: str, tmp_path: Path) -> None:
    output = tmp_path / "engine-motion-deblur.mp4"
    catalog = tmp_path / "stock_catalog.json"
    catalog.write_text(
        json.dumps(
            [
                {
                    "id": "video-motion",
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
        "goal": "reduce motion streaking in a real clip",
        "steps": [
            {
                "id": "deblur",
                "function": "gemia.video.motion_deblur.render_motion_deblur_plan",
                "args": {"strength": 0.75, "blur_radius": 5, "frame_step": 3, "max_long_edge": 96},
                "input": "$input",
                "output": "$output",
            }
        ],
    }

    result = PlanEngine(root_dir=tmp_path / "engine").execute(plan, sample_video_path, str(output))
    review = review_real_media_artifact(
        sample_video_path,
        result,
        report_path=tmp_path / "motion-deblur-review.json",
        stock_catalog_path=catalog,
        min_output_frames=2,
    )
    metadata = json.loads(output.with_suffix(".motion_deblur.json").read_text(encoding="utf-8"))

    assert review.status == "passed"
    assert any(finding["code"] == "motion_deblur_metadata_recorded" for finding in review.findings)
    assert metadata["sharpness"]["delta"] >= 0
