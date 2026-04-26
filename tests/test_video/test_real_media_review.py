from __future__ import annotations

import json
from pathlib import Path

from gemia.video.layer_flow import render_layer_workflow
from gemia.video.review import review_real_media_artifact


def test_real_media_review_writes_quality_report(
    sample_video_path: str,
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "review-output.mp4"
    render_layer_workflow(
        sample_video_path,
        str(output_path),
        title="Review",
        title_font_size=18,
        title_duration_frames=10,
        frame_step=2,
        max_long_edge=96,
    )
    catalog_path = tmp_path / "stock_catalog.json"
    catalog_path.write_text(
        json.dumps(
            [
                {
                    "id": "video-test",
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

    result = review_real_media_artifact(
        sample_video_path,
        output_path,
        preview_manifest_path=output_path.with_suffix(".preview.json"),
        layer_flow_manifest_path=output_path.with_suffix(".layer-flow.json"),
        stock_catalog_path=catalog_path,
        min_output_frames=2,
    )

    report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
    assert result.status == "passed"
    assert report["real_source"]["confirmed"] is True
    assert report["stock_catalog_evidence"]["id"] == "video-test"
    assert report["output"]["frame_count"] >= 2
    assert report["render_context"]["render_backend"]["selected"] == "software"
    assert any(finding["code"] == "output_visual_signal" for finding in result.findings)


def test_real_media_review_fails_when_output_is_missing(
    sample_video_path: str,
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "stock_catalog.json"
    catalog_path.write_text(
        json.dumps(
            [
                {
                    "id": "video-test",
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

    result = review_real_media_artifact(
        sample_video_path,
        tmp_path / "missing.mp4",
        report_path=tmp_path / "missing-review.json",
        stock_catalog_path=catalog_path,
    )

    assert result.status == "failed"
    assert any(finding["code"] == "output_missing" for finding in result.findings)
