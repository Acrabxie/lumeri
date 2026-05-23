from __future__ import annotations

import json
from pathlib import Path

from gemia.engine import PlanEngine
from gemia.registry import catalog_for_prompt
from gemia.video.face_reshaper import render_face_reshaper_plan
from gemia.video.review import review_real_media_artifact


def test_face_reshaper_is_planner_visible() -> None:
    assert "gemia.video.face_reshaper.render_face_reshaper_plan" in catalog_for_prompt()


def test_render_face_reshaper_plan_writes_warp_or_no_face_metadata(
    sample_video_path: str,
    tmp_path: Path,
) -> None:
    output = tmp_path / "face-reshaper.mp4"

    result = render_face_reshaper_plan(
        sample_video_path,
        str(output),
        cheek_scale=-0.16,
        jaw_scale=-0.1,
        eye_spacing=0.08,
        smile_lift=0.06,
        frame_step=2,
        max_long_edge=96,
    )

    metadata = json.loads(output.with_suffix(".face_reshaper.json").read_text(encoding="utf-8"))
    assert result == str(output)
    assert output.exists()
    assert metadata["effect"] == "resolve21_ai_face_reshaper"
    assert metadata["rendered_frames"] > 0
    assert metadata["face_detection"]["total_faces"] >= 0
    assert metadata["preview_kind"] in {"tracked_local_warp", "no_face_diagnostic_passthrough"}
    assert metadata["reshape_controls"]["cheek_scale"] == -0.16
    assert metadata["tracking"]["warp_model"] == "face_box_region_remap"


def test_plan_engine_runs_face_reshaper_and_real_media_review(sample_video_path: str, tmp_path: Path) -> None:
    output = tmp_path / "engine-face-reshaper.mp4"
    catalog = tmp_path / "stock_catalog.json"
    catalog.write_text(
        json.dumps(
            [
                {
                    "id": "video-face-reshaper",
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
        "goal": "preview tracked face reshaping or no-face evidence",
        "steps": [
            {
                "id": "face-reshaper",
                "function": "gemia.video.face_reshaper.render_face_reshaper_plan",
                "args": {
                    "cheek_scale": -0.14,
                    "jaw_scale": -0.08,
                    "eye_spacing": 0.04,
                    "smile_lift": 0.05,
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
        report_path=tmp_path / "face-reshaper-review.json",
        stock_catalog_path=catalog,
        min_output_frames=2,
    )

    assert review.status == "passed"
    assert any(finding["code"] == "face_reshaper_metadata_recorded" for finding in review.findings)
    assert any(
        finding["code"] in {"face_reshaper_warp_recorded", "face_reshaper_no_face_evidence_recorded"}
        for finding in review.findings
    )
