from __future__ import annotations

import json
from pathlib import Path

from gemia.engine import PlanEngine
from gemia.registry import catalog_for_prompt
from gemia.video.blemish import render_blemish_removal_plan
from gemia.video.review import review_real_media_artifact


def test_blemish_removal_is_planner_visible() -> None:
    assert "gemia.video.blemish.render_blemish_removal_plan" in catalog_for_prompt()


def test_render_blemish_removal_plan_writes_cleanup_or_no_face_metadata(
    sample_video_path: str,
    tmp_path: Path,
) -> None:
    output = tmp_path / "blemish.mp4"

    result = render_blemish_removal_plan(
        sample_video_path,
        str(output),
        strength=0.7,
        texture_preservation=0.55,
        skin_threshold=0.3,
        frame_step=2,
        max_long_edge=96,
    )

    metadata = json.loads(output.with_suffix(".blemish.json").read_text(encoding="utf-8"))
    assert result == str(output)
    assert output.exists()
    assert metadata["effect"] == "resolve21_ai_blemish_removal"
    assert metadata["rendered_frames"] > 0
    assert metadata["face_detection"]["total_faces"] >= 0
    assert metadata["preview_kind"] in {"skin_cleanup_texture_preserving", "no_face_diagnostic_passthrough"}
    assert metadata["cleanup"]["texture_preservation_score"] >= 0
    assert metadata["parameters"]["texture_preservation"] == 0.55


def test_plan_engine_runs_blemish_removal_and_real_media_review(sample_video_path: str, tmp_path: Path) -> None:
    output = tmp_path / "engine-blemish.mp4"
    catalog = tmp_path / "stock_catalog.json"
    catalog.write_text(
        json.dumps(
            [
                {
                    "id": "video-blemish",
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
        "goal": "preview texture-preserving blemish removal or no-face evidence",
        "steps": [
            {
                "id": "blemish-removal",
                "function": "gemia.video.blemish.render_blemish_removal_plan",
                "args": {
                    "strength": 0.62,
                    "texture_preservation": 0.6,
                    "skin_threshold": 0.28,
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
        report_path=tmp_path / "blemish-review.json",
        stock_catalog_path=catalog,
        min_output_frames=2,
    )

    assert review.status == "passed"
    assert any(finding["code"] == "blemish_metadata_recorded" for finding in review.findings)
    assert any(
        finding["code"] in {"blemish_cleanup_recorded", "blemish_no_face_evidence_recorded"}
        for finding in review.findings
    )
