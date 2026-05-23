from __future__ import annotations

import json
from pathlib import Path

from gemia.engine import PlanEngine
from gemia.registry import catalog_for_prompt
from gemia.video.review import review_real_media_artifact
from gemia.video.ultrasharpen import render_ultrasharpen_plan


def test_ultrasharpen_is_planner_visible() -> None:
    assert "gemia.video.ultrasharpen.render_ultrasharpen_plan" in catalog_for_prompt()


def test_render_ultrasharpen_plan_writes_detail_metadata(sample_video_path: str, tmp_path: Path) -> None:
    output = tmp_path / "ultrasharpen.mp4"

    result = render_ultrasharpen_plan(
        sample_video_path,
        str(output),
        strength=0.82,
        detail_radius=5,
        frame_step=2,
        max_long_edge=96,
    )

    metadata = json.loads(output.with_suffix(".ultrasharpen.json").read_text(encoding="utf-8"))
    assert result == str(output)
    assert output.exists()
    assert metadata["effect"] == "resolve21_ai_ultrasharpen"
    assert metadata["rendered_frames"] > 0
    assert metadata["sharpness"]["output_laplacian_variance"] >= metadata["sharpness"]["input_laplacian_variance"]
    assert metadata["average_edge_density"] >= 0
    assert metadata["samples"][0]["frame"] == 0


def test_plan_engine_runs_ultrasharpen_and_real_media_review(sample_video_path: str, tmp_path: Path) -> None:
    output = tmp_path / "engine-ultrasharpen.mp4"
    catalog = tmp_path / "stock_catalog.json"
    catalog.write_text(
        json.dumps(
            [
                {
                    "id": "video-ultrasharpen",
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
        "goal": "recover detail and clarity in a real clip",
        "steps": [
            {
                "id": "ultrasharpen",
                "function": "gemia.video.ultrasharpen.render_ultrasharpen_plan",
                "args": {"strength": 0.78, "detail_radius": 5, "frame_step": 3, "max_long_edge": 96},
                "input": "$input",
                "output": "$output",
            }
        ],
    }

    result = PlanEngine(root_dir=tmp_path / "engine").execute(plan, sample_video_path, str(output))
    review = review_real_media_artifact(
        sample_video_path,
        result,
        report_path=tmp_path / "ultrasharpen-review.json",
        stock_catalog_path=catalog,
        min_output_frames=2,
    )
    metadata = json.loads(output.with_suffix(".ultrasharpen.json").read_text(encoding="utf-8"))

    assert review.status == "passed"
    assert any(finding["code"] == "ultrasharpen_metadata_recorded" for finding in review.findings)
    assert any(finding["code"] == "ultrasharpen_sharpness_recorded" for finding in review.findings)
    assert metadata["sharpness"]["delta"] >= 0
