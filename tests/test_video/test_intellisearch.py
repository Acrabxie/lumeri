from __future__ import annotations

import json
import shutil
from pathlib import Path

from gemia.registry import catalog_for_prompt
from gemia.video.intellisearch import index_real_media, search_media_index


def test_intellisearch_indexes_stock_catalog_labels(
    sample_video_path: str,
    tmp_path: Path,
) -> None:
    mountain = tmp_path / "video-0003-mountain-lake.mp4"
    desert = tmp_path / "video-0004-desert-highway.mp4"
    shutil.copyfile(sample_video_path, mountain)
    shutil.copyfile(sample_video_path, desert)
    catalog_path = tmp_path / "stock_catalog.json"
    catalog_path.write_text(
        json.dumps(
            [
                {
                    "id": "video-0003",
                    "kind": "video",
                    "prompt": "mountain lake, camera pushes in slowly, real clip-test b-roll.",
                    "status": "completed",
                    "backend": "local_real_video",
                    "outputs": [str(mountain)],
                },
                {
                    "id": "video-0004",
                    "kind": "video",
                    "prompt": "desert highway, locked-off observation, real clip-test b-roll.",
                    "status": "completed",
                    "backend": "local_real_video",
                    "outputs": [str(desert)],
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = index_real_media(
        [str(mountain), str(desert)],
        str(tmp_path / "intellisearch.json"),
        stock_catalog_path=str(catalog_path),
    )
    search = search_media_index(result.index_path, "desert highway", limit=1)

    assert result.clip_count == 2
    assert search.match_count == 1
    assert Path(search.matches[0]["path"]) == desert.resolve()
    assert search.matches[0]["stock_catalog_evidence"]["id"] == "video-0004"
    assert search.matches[0]["time_ranges"][0]["label"] == "semantic_clip"


def test_intellisearch_uses_review_and_dialog_sidecar_labels(
    sample_video_path: str,
    tmp_path: Path,
) -> None:
    clip = tmp_path / "reviewed-real-source.mp4"
    shutil.copyfile(sample_video_path, clip)
    clip.with_suffix(".srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nsunrise dialog cue\n", encoding="utf-8")
    review_path = tmp_path / "review.json"
    review_path.write_text(
        json.dumps(
            {
                "review_kind": "real_media_review_pass",
                "status": "passed",
                "source": {"path": str(clip)},
                "output": {"path": str(tmp_path / "rendered.mp4")},
                "real_source": {"confirmed": True, "method": "stock_catalog"},
                "render_context": {
                    "render_backend": {"selected": "software"},
                    "authoring_mode": "planner_controller_layer_flow",
                },
                "quality_findings": [{"code": "output_visual_signal"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = index_real_media(
        [str(clip)],
        str(tmp_path / "review-index.json"),
        review_report_paths=[str(review_path)],
        extra_labels=["focus candidate"],
    )
    search = search_media_index(result.index_path, "sunrise dialog software", limit=1)

    assert search.match_count == 1
    labels = set(search.matches[0]["semantic_labels"])
    assert "dialog_keywords" in labels
    assert "software" in labels
    assert "output" in labels
    assert "visual" in labels


def test_intellisearch_primitives_are_planner_visible() -> None:
    catalog = catalog_for_prompt()

    assert "gemia.video.intellisearch.index_real_media" in catalog
    assert "gemia.video.intellisearch.search_media_index" in catalog
