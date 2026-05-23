from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from gemia.engine import PlanEngine
from gemia.registry import catalog_for_prompt
from gemia.video.ad_graphics import (
    compose_overlay_on_video,
    render_ad_title_pack,
    render_cta_card,
    render_lower_third,
    render_product_callout,
    render_shimmer_sweep,
)


def test_ad_graphics_primitives_are_planner_visible() -> None:
    catalog = catalog_for_prompt()

    assert "gemia.video.ad_graphics.render_ad_title_pack" in catalog
    assert "gemia.video.ad_graphics.render_product_callout" in catalog
    assert "gemia.video.ad_graphics.compose_overlay_on_video" in catalog


def test_render_ad_title_pack_writes_video_and_hyperframes_style_sidecars(tmp_path: Path) -> None:
    output = tmp_path / "ad-title.mp4"

    result = render_ad_title_pack(
        "",
        str(output),
        title="Lumeri",
        subtitle="一句话把素材变成成片",
        kicker="NEW WORKFLOW",
        cta="立即体验",
        duration=0.35,
        width=320,
        height=180,
        fps=12,
    )
    metadata = json.loads(output.with_suffix(".ad_graphics.json").read_text(encoding="utf-8"))
    html = output.with_suffix(".ad_composition.html").read_text(encoding="utf-8")

    assert result == str(output)
    assert output.exists()
    assert metadata["effect"] == "lumeri_ad_title_pack"
    assert metadata["hyperframes_inspired"] is True
    assert metadata["composition_contract"]["deterministic_seekable_timeline"] is True
    assert "data-composition-id" in html
    assert "Lumeri" in html


def test_ad_graphics_variants_write_sidecars(sample_video_path: str, tmp_path: Path) -> None:
    outputs = [
        tmp_path / "lower.mp4",
        tmp_path / "cta.mp4",
        tmp_path / "callout.mp4",
        tmp_path / "shimmer.mp4",
    ]

    render_lower_third(sample_video_path, str(outputs[0]), title="Founder Mode", subtitle="快速成片", duration=0.3, width=320, height=180, fps=12)
    render_cta_card(sample_video_path, str(outputs[1]), headline="Ready?", body="Make the cut", button_text="Try", duration=0.3, width=320, height=180, fps=12)
    render_product_callout(sample_video_path, str(outputs[2]), label="AI Timeline", detail="Layer-first control", badge="NEW", duration=0.3, width=320, height=180, fps=12)
    render_shimmer_sweep("", str(outputs[3]), text="Lumeri", subtitle="Ad graphics", duration=0.3, width=320, height=180, fps=12)

    for output in outputs:
        assert output.exists()
        metadata = json.loads(output.with_suffix(".ad_graphics.json").read_text(encoding="utf-8"))
        assert metadata["composition_path"].endswith(".ad_composition.html")
        assert metadata["frame_count"] > 0


def test_compose_overlay_on_video_uses_overlay_and_writes_metadata(sample_video_path: str, tmp_path: Path) -> None:
    overlay = tmp_path / "overlay.png"
    Image.new("RGBA", (48, 24), (121, 216, 255, 160)).save(overlay)
    output = tmp_path / "composited.mp4"

    result = compose_overlay_on_video(
        sample_video_path,
        str(output),
        overlay_path=str(overlay),
        position="top-right",
        scale=1.0,
        start_sec=0.0,
        duration=0.3,
    )
    metadata = json.loads(output.with_suffix(".ad_graphics.json").read_text(encoding="utf-8"))

    assert result == str(output)
    assert output.exists()
    assert metadata["effect"] == "lumeri_overlay_composite"
    assert metadata["params"]["overlay_path"] == str(overlay.resolve())


def test_plan_engine_runs_ad_graphics_title_pack(tmp_path: Path) -> None:
    output = tmp_path / "engine-ad-title.mp4"
    plan = {
        "version": "2.1",
        "goal": "render commercial ad text",
        "steps": [
            {
                "id": "ad_title",
                "function": "gemia.video.ad_graphics.render_ad_title_pack",
                "args": {
                    "title": "Lumeri",
                    "subtitle": "商业广告图文动效",
                    "cta": "开始创作",
                    "duration": 0.35,
                    "width": 320,
                    "height": 180,
                    "fps": 12,
                },
                "input": "$input",
                "output": "$output",
            }
        ],
    }

    result = PlanEngine(root_dir=tmp_path / "engine").execute(plan, "", str(output))

    assert result == str(output)
    assert output.exists()
    assert output.with_suffix(".ad_composition.html").exists()
