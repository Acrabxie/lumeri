from __future__ import annotations

import json
from pathlib import Path

from gemia.ai.gemini_adapter import build_primitive_plan_system_prompt
from gemia.engine import PlanEngine
from gemia.registry import catalog_for_prompt
from gemia.video.layer_flow import render_layer_workflow


def test_layer_workflow_is_exposed_to_ai_catalog() -> None:
    catalog = catalog_for_prompt()
    prompt = build_primitive_plan_system_prompt()

    assert "gemia.video.layer_flow.render_layer_workflow" in catalog
    assert "gemia.video.preview.render_shadow_preview" not in catalog
    assert "gemia.video.layer_flow.render_layer_workflow" in prompt
    assert "args.overlay_layers" in prompt


def test_render_layer_workflow_runs_controller_layer_plan(
    sample_video_path: str,
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "layer-flow.mp4"

    result = render_layer_workflow(
        sample_video_path,
        str(output_path),
        title="OPEN",
        title_font_size=18,
        title_duration_frames=8,
        frame_step=2,
        max_long_edge=96,
        overlay_layers=[
            {
                "type": "text",
                "text": "lower third",
                "position": [12, 72],
                "font_config": {"size": 14},
                "duration": 12,
                "z_index": 2,
            }
        ],
    )

    assert result == str(output_path.resolve())
    assert output_path.exists()
    preview_manifest = json.loads(output_path.with_suffix(".preview.json").read_text(encoding="utf-8"))
    flow_manifest = json.loads(output_path.with_suffix(".layer-flow.json").read_text(encoding="utf-8"))
    assert preview_manifest["render_backend"]["selected"] == "software"
    assert preview_manifest["execution_graph"]["backend"] == "software"
    assert preview_manifest["compiled_graph"]["metadata"]["authored_metric_sources"] == {
        "width": "inferred",
        "height": "inferred",
        "fps": "inferred",
        "total_frames": "inferred",
    }
    assert flow_manifest["authoring_mode"] == "planner_controller_layer_flow"
    assert flow_manifest["layer_count"] == 3


def test_plan_engine_executes_planner_layer_workflow_step(
    sample_video_path: str,
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "engine-layer-flow.mp4"
    engine = PlanEngine(root_dir=tmp_path / "engine")
    plan = {
        "version": "2.0",
        "goal": "Render a layer-first title preview",
        "steps": [
            {
                "id": "step_1",
                "function": "gemia.video.layer_flow.render_layer_workflow",
                "args": {
                    "title": "Scene 1",
                    "title_font_size": 16,
                    "title_duration_frames": 10,
                    "frame_step": 3,
                    "max_long_edge": 96,
                    "overlay_layers": [
                        {
                            "type": "text",
                            "text": "camera move",
                            "position": [10, 70],
                            "font_config": {"size": 12},
                            "duration": 10,
                            "z_index": 2,
                        }
                    ],
                },
                "input": "$input",
                "output": "$output",
            }
        ],
    }

    result = engine.execute(plan, sample_video_path, str(output_path))

    assert result == str(output_path.resolve())
    assert output_path.exists()
    manifest = json.loads(output_path.with_suffix(".preview.json").read_text(encoding="utf-8"))
    assert manifest["render_backend"]["source_kind"] == "compositing_graph"
    assert manifest["compiled_graph"]["metadata"]["source"] == "layer_plan"
