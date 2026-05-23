from __future__ import annotations

import json
from pathlib import Path

from gemia.engine import PlanEngine
from gemia.registry import catalog_for_prompt
from gemia.video.motion_graphics import (
    render_mg_formula_reveal,
    render_mg_process_diagram,
    render_mg_title_card,
)


def test_motion_graphics_primitives_are_planner_visible() -> None:
    catalog = catalog_for_prompt()

    assert "gemia.video.motion_graphics.render_mg_title_card" in catalog
    assert "gemia.video.motion_graphics.render_mg_formula_reveal" in catalog
    assert "gemia.video.motion_graphics.render_mg_process_diagram" in catalog


def test_render_mg_title_card_fallback_writes_video_and_sidecar(tmp_path: Path) -> None:
    output = tmp_path / "title.mp4"

    result = render_mg_title_card(
        "",
        str(output),
        title="Lumeri",
        subtitle="Motion graphics",
        duration=0.4,
        width=320,
        height=180,
        fps=12,
        prefer_manim=False,
    )
    metadata = json.loads(output.with_suffix(".mg.json").read_text(encoding="utf-8"))

    assert result == str(output)
    assert output.exists()
    assert metadata["effect"] == "gemia_mg_title_card"
    assert metadata["renderer"] == "opencv_fallback"
    assert metadata["payload"]["title"] == "Lumeri"


def test_render_mg_formula_reveal_fallback_writes_video_and_sidecar(tmp_path: Path) -> None:
    output = tmp_path / "formula.mp4"

    result = render_mg_formula_reveal(
        "",
        str(output),
        formula="E = mc^2",
        caption="energy relation",
        duration=0.4,
        width=320,
        height=180,
        fps=12,
        prefer_manim=False,
    )
    metadata = json.loads(output.with_suffix(".mg.json").read_text(encoding="utf-8"))

    assert result == str(output)
    assert output.exists()
    assert metadata["effect"] == "gemia_mg_formula_reveal"
    assert metadata["payload"]["formula"] == "E = mc^2"


def test_render_mg_process_diagram_fallback_writes_video_and_sidecar(tmp_path: Path) -> None:
    output = tmp_path / "process.mp4"

    result = render_mg_process_diagram(
        "",
        str(output),
        title="Pipeline",
        steps=["Import", "Read", "Plan", "Render"],
        duration=0.5,
        width=360,
        height=200,
        fps=12,
        prefer_manim=False,
    )
    metadata = json.loads(output.with_suffix(".mg.json").read_text(encoding="utf-8"))

    assert result == str(output)
    assert output.exists()
    assert metadata["effect"] == "gemia_mg_process_diagram"
    assert metadata["payload"]["steps"] == ["Import", "Read", "Plan", "Render"]


def test_plan_engine_runs_mg_title_card(tmp_path: Path) -> None:
    output = tmp_path / "engine-title.mp4"
    plan = {
        "version": "2.0",
        "goal": "make an MG title card",
        "steps": [
            {
                "id": "title_card",
                "function": "gemia.video.motion_graphics.render_mg_title_card",
                "args": {
                    "title": "Lumeri",
                    "subtitle": "Editor",
                    "duration": 0.4,
                    "width": 320,
                    "height": 180,
                    "fps": 12,
                    "prefer_manim": False,
                },
                "input": "$input",
                "output": "$output",
            }
        ],
    }

    result = PlanEngine(root_dir=tmp_path / "engine").execute(plan, "", str(output))

    assert result == str(output)
    assert output.exists()
    assert output.with_suffix(".mg.json").exists()
