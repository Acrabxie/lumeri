from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gemia.engine import PlanEngine
from gemia.registry import catalog_for_prompt
from gemia.video.html_graphics import _HtmlBoxParser, render_html_frame, render_html_graphics_plan
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


def _video_codec(path: Path) -> str:
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe is not available")
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def test_html_graphics_plan_is_planner_visible() -> None:
    assert "gemia.video.html_graphics.render_html_graphics_plan" in catalog_for_prompt()
    assert "gemia.video.html_graphics.render_lottie_frame" not in catalog_for_prompt()


def test_html_parser_does_not_burn_style_text_or_duplicate_children() -> None:
    parser = _HtmlBoxParser()
    parser.feed(
        """
        <div style="width:1920px;height:1080px;display:flex;align-items:center;justify-content:center;">
          <div style="color:white;font-size:120px;font-weight:bold;">
            <style>@keyframes slideIn { from { opacity:0; } to { opacity:1; } }</style>
            NEON CUT
          </div>
        </div>
        """
    )

    rendered_texts = [box["text"] for box in parser.boxes]

    assert rendered_texts == ["NEON CUT"]
    assert all("@keyframes" not in text for text in rendered_texts)


def test_html_frame_css_only_markup_does_not_burn_css_source() -> None:
    frame = render_html_frame(
        None,
        """
        <div class="blade-shell">
          <style>
            .blade { position:absolute; width:200vw; height:200vh; background:#111; }
          </style>
          <div class="blade"></div>
        </div>
        """,
        width=180,
        height=96,
    )

    assert float(frame[:, :, 3].max()) == 0.0


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


def test_html_graphics_plan_supports_prompt_only_blank_canvas(tmp_path: Path) -> None:
    output = tmp_path / "blank-html-graphics.mp4"

    result = render_html_graphics_plan(
        "",
        str(output),
        html="<div style='left:28px;top:48px;width:260px;height:64px;background:#00d6c9;color:#071013;font-size:28px;padding:12px;border-radius:8px'>LUMERI PULSE</div>",
        frame_step=3,
        max_long_edge=160,
    )

    metadata = json.loads(output.with_suffix(".html_graphics.json").read_text(encoding="utf-8"))

    assert result == str(output.resolve())
    assert output.exists()
    assert metadata["blank_canvas"] is True
    assert metadata["source_path"] == ""
    assert metadata["overlay_count"] == 1
    assert metadata["overlay_types"] == ["html"]
    assert metadata["plan"]["layers"][0]["id"] == "blank_canvas_background"
    assert all(layer["id"] != "source_video" for layer in metadata["plan"]["layers"])
    assert _video_codec(output) == "h264"


def test_plan_engine_runs_prompt_only_html_graphics_blank_canvas(tmp_path: Path) -> None:
    output = tmp_path / "engine-blank-html-graphics.mp4"
    plan = {
        "version": "2.1",
        "goal": "Render a prompt-only MG title card",
        "steps": [
            {
                "id": "graphics",
                "function": "gemia.video.html_graphics.render_html_graphics_plan",
                "args": {
                    "html": "<div style='left:24px;top:40px;width:220px;height:56px;background:#111;color:#fff;font-size:24px;padding:10px'>LUMERI</div>",
                    "frame_step": 3,
                    "max_long_edge": 160,
                },
                "input": "$input",
                "output": "$output",
            }
        ],
    }

    result = PlanEngine(root_dir=tmp_path / "engine").execute(plan, "", str(output))
    metadata = json.loads(output.with_suffix(".html_graphics.json").read_text(encoding="utf-8"))

    assert result == str(output.resolve())
    assert output.exists()
    assert metadata["blank_canvas"] is True
    assert metadata["source_path"] == ""
