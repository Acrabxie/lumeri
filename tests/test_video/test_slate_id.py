from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from gemia.engine import PlanEngine
from gemia.registry import catalog_for_prompt
from gemia.video.review import review_real_media_artifact
from gemia.video.slate_id import render_slate_id_metadata_plan


def test_slate_id_is_planner_visible() -> None:
    assert "gemia.video.slate_id.render_slate_id_metadata_plan" in catalog_for_prompt()


def test_render_slate_id_metadata_plan_writes_no_slate_diagnostics(
    sample_video_path: str,
    tmp_path: Path,
) -> None:
    output = tmp_path / "slate-no-diagnostic.mp4"

    result = render_slate_id_metadata_plan(
        sample_video_path,
        str(output),
        frame_step=4,
        max_long_edge=96,
        min_confidence=0.9,
    )

    metadata = json.loads(output.with_suffix(".slate_id.json").read_text(encoding="utf-8"))
    assert result == str(output)
    assert output.exists()
    assert metadata["effect"] == "resolve21_ai_slate_id_metadata"
    assert metadata["rendered_frames"] > 0
    assert metadata["preview_kind"] == "no_slate_diagnostic_passthrough"
    assert metadata["slate_detection"]["no_slate_evidence"] is True
    assert "ai_slate_id" in metadata["clip_metadata"]["searchable_tokens"]


def test_render_slate_id_metadata_detects_synthetic_slate(tmp_path: Path) -> None:
    source = _write_slate_video(tmp_path / "synthetic-slate.mp4")
    output = tmp_path / "slate-detected.mp4"

    render_slate_id_metadata_plan(
        str(source),
        str(output),
        frame_step=1,
        max_long_edge=160,
        min_confidence=0.25,
        metadata_hints={"scene": "A12", "take": "03", "roll": "B"},
    )

    metadata = json.loads(output.with_suffix(".slate_id.json").read_text(encoding="utf-8"))
    assert metadata["preview_kind"] == "slate_metadata_detected"
    assert metadata["slate_detection"]["frames_with_slate"] >= 1
    assert metadata["clip_metadata"]["slate_id"] == "A12"
    assert "scene:A12" in metadata["clip_metadata"]["searchable_tokens"]


def test_plan_engine_runs_slate_id_and_real_media_review(tmp_path: Path) -> None:
    source = _write_slate_video(tmp_path / "engine-slate-source.mp4")
    output = tmp_path / "engine-slate.mp4"
    catalog = tmp_path / "stock_catalog.json"
    catalog.write_text(
        json.dumps(
            [
                {
                    "id": "video-slate",
                    "kind": "video",
                    "status": "completed",
                    "backend": "local_real_video",
                    "source": str(source),
                    "outputs": [str(source)],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    plan = {
        "version": "2.0",
        "goal": "extract slate id metadata from a clapperboard/title-card frame",
        "steps": [
            {
                "id": "slate-id",
                "function": "gemia.video.slate_id.render_slate_id_metadata_plan",
                "args": {
                    "frame_step": 1,
                    "max_long_edge": 160,
                    "min_confidence": 0.25,
                    "metadata_hints": {"scene": "A12", "take": "03"},
                },
                "input": "$input",
                "output": "$output",
            }
        ],
    }

    result = PlanEngine(root_dir=tmp_path / "engine").execute(plan, str(source), str(output))
    review = review_real_media_artifact(
        str(source),
        result,
        report_path=tmp_path / "slate-review.json",
        stock_catalog_path=catalog,
        min_output_frames=2,
    )

    assert review.status == "passed"
    assert any(finding["code"] == "slate_id_metadata_recorded" for finding in review.findings)
    assert any(finding["code"] == "slate_id_detected_recorded" for finding in review.findings)


def _write_slate_video(path: Path) -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 12.0, (160, 96))
    if not writer.isOpened():
        raise RuntimeError("Could not create synthetic slate video.")
    for frame_idx in range(18):
        frame = np.zeros((96, 160, 3), dtype=np.uint8)
        frame[:] = (20 + frame_idx, 22 + frame_idx, 24 + frame_idx)
        cv2.rectangle(frame, (14, 14), (146, 82), (238, 238, 238), -1)
        cv2.rectangle(frame, (14, 14), (146, 82), (15, 15, 15), 2)
        cv2.line(frame, (14, 31), (146, 31), (15, 15, 15), 2)
        cv2.line(frame, (14, 50), (146, 50), (15, 15, 15), 1)
        cv2.line(frame, (76, 31), (76, 82), (15, 15, 15), 1)
        cv2.putText(frame, "SCENE A12", (22, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 0, 0), 1, cv2.LINE_AA)
        cv2.putText(frame, "TAKE 03", (22, 47), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 1, cv2.LINE_AA)
        cv2.putText(frame, "ROLL B", (22, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 1, cv2.LINE_AA)
        writer.write(frame)
    writer.release()
    return path
