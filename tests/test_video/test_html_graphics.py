from __future__ import annotations

import json
from pathlib import Path

from gemia.engine import PlanEngine
from gemia.registry import catalog_for_prompt
from gemia.video.html_graphics import render_html_graphics_plan
from gemia.video.layers import render_layer_plan
from gemia.video.review import review_real_media_artifact


def _write_lottie(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "v": "5.9.0",
                "fr": 15,
                "ip": 0,
                "op": 30,
                "w": 128,
                "h": 128,
                "layers": [
                    {
                        "ty": 4,
                        "ip": 0,
                        "op": 30,
                        "ks": {
                            "p": {"k": [{"t": 0, "s": [32, 64, 0], "e": [96, 64, 0]}, {"t": 29, "s": [96, 64, 0]}]},
                            "s": {"k": [100, 100, 100]},
                            "o": {"k": [{"t": 0, "s": [35], "e": [100]}, {"t": 12, "s": [100]}]},
                        },
                        "shapes": [
                            {
                                "ty": "gr",
                                "it": [
                                    {"ty": "el", "s": {"k": [44, 44]}, "p": {"k": [0, 0]}},
                                    {"ty": "fl", "c": {"k": [0.1, 0.65, 1.0, 1.0]}, "o": {"k": 92}},
                                ],
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_html_graphics_plan_is_planner_visible() -> None:
    assert "gemia.video.html_graphics.render_html_graphics_plan" in catalog_for_prompt()
    assert "gemia.video.html_graphics.render_lottie_frame" not in catalog_for_prompt()


def test_layer_plan_accepts_html_and_lottie_layers(tmp_path: Path) -> None:
    lottie = tmp_path / "badge.json"
    _write_lottie(lottie)
    output = tmp_path / "graphics.mp4"
    plan = {
        "width": 128,
        "height": 128,
        "fps": 15,
        "total_frames": 12,
        "layers": [
            {"id": "bg", "type": "solid", "color": [0.02, 0.03, 0.06, 1.0], "duration": 12},
            {
                "id": "html",
                "type": "html",
                "html": "<div style='background:rgba(20,20,20,.72);color:#fff;font-size:18px;padding:8px;border-radius:6px'>LIVE SCORE</div>",
                "duration": 12,
                "position": [12, 78],
                "z_index": 2,
            },
            {
                "id": "lottie",
                "type": "lottie",
                "source": str(lottie),
                "duration": 12,
                "size": [56, 56],
                "position": [64, 18],
                "z_index": 3,
            },
        ],
    }

    result = render_layer_plan(plan, output)

    assert result == str(output)
    assert output.exists()


def test_plan_engine_runs_html_graphics_and_real_media_review(sample_video_path: str, tmp_path: Path) -> None:
    lottie = tmp_path / "badge.json"
    _write_lottie(lottie)
    output = tmp_path / "engine-html-graphics.mp4"
    catalog = tmp_path / "stock_catalog.json"
    catalog.write_text(
        json.dumps(
            [
                {
                    "id": "video-html",
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
        "goal": "add html lower third and lottie badge over a real clip",
        "steps": [
            {
                "id": "graphics",
                "function": "gemia.video.html_graphics.render_html_graphics_plan",
                "args": {
                    "html": "<div style='background:#111c;color:white;font-size:20px;padding:10px;border-radius:6px'>MATCH CUT</div>",
                    "lottie_source": str(lottie),
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
        report_path=tmp_path / "html-graphics-review.json",
        stock_catalog_path=catalog,
        min_output_frames=2,
    )
    metadata = json.loads(output.with_suffix(".html_graphics.json").read_text(encoding="utf-8"))

    assert review.status == "passed"
    assert any(finding["code"] == "html_graphics_metadata_recorded" for finding in review.findings)
    assert any(finding["code"] == "html_graphics_alpha_recorded" for finding in review.findings)
    assert metadata["overlay_types"] == ["html", "lottie"]
