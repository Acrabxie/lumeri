from __future__ import annotations

import json
from pathlib import Path

from gemia.engine import PlanEngine
from gemia.registry import catalog_for_prompt
from gemia.video.face_age import render_face_age_plan
from gemia.video.review import review_real_media_artifact


def test_face_age_is_planner_visible() -> None:
    assert "gemia.video.face_age.render_face_age_plan" in catalog_for_prompt()


def test_render_face_age_plan_writes_no_face_or_tracks_metadata(sample_video_path: str, tmp_path: Path) -> None:
    output = tmp_path / "face-age.mp4"

    result = render_face_age_plan(
        sample_video_path,
        str(output),
        age_offset=18,
        strength=0.7,
        frame_step=2,
        max_long_edge=96,
    )

    metadata = json.loads(output.with_suffix(".face_age.json").read_text(encoding="utf-8"))
    assert result == str(output)
    assert output.exists()
    assert metadata["effect"] == "resolve21_ai_face_age_transformer"
    assert metadata["rendered_frames"] > 0
    assert metadata["face_detection"]["total_faces"] >= 0
    assert metadata["preview_kind"] in {"localized_age_offset", "no_face_diagnostic_passthrough"}
    assert metadata["samples"][0]["frame"] == 0


def test_plan_engine_runs_face_age_and_real_media_review(sample_video_path: str, tmp_path: Path) -> None:
    output = tmp_path / "engine-face-age.mp4"
    catalog = tmp_path / "stock_catalog.json"
    catalog.write_text(
        json.dumps(
            [
                {
                    "id": "video-face-age",
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
        "goal": "preview a face-age continuity adjustment or no-face evidence",
        "steps": [
            {
                "id": "face-age",
                "function": "gemia.video.face_age.render_face_age_plan",
                "args": {"age_offset": -10, "strength": 0.62, "frame_step": 3, "max_long_edge": 96},
                "input": "$input",
                "output": "$output",
            }
        ],
    }

    result = PlanEngine(root_dir=tmp_path / "engine").execute(plan, sample_video_path, str(output))
    review = review_real_media_artifact(
        sample_video_path,
        result,
        report_path=tmp_path / "face-age-review.json",
        stock_catalog_path=catalog,
        min_output_frames=2,
    )

    assert review.status == "passed"
    assert any(finding["code"] == "face_age_metadata_recorded" for finding in review.findings)
    assert any(
        finding["code"] in {"face_age_tracks_recorded", "face_age_no_face_evidence_recorded"}
        for finding in review.findings
    )
