from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image as PILImage

from gemia.ai.gemini_adapter import build_primitive_plan_system_prompt
from gemia.engine import PlanEngine
from gemia.registry import catalog_for_prompt
from gemia.video.layers import execute_layer_plan
from gemia.video.layer_flow import render_layer_workflow
from gemia.video.preview import _scale_layer_plan


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


def test_render_layer_workflow_supports_prompt_only_blank_canvas(tmp_path: Path) -> None:
    output_path = tmp_path / "blank-layer-flow.mp4"

    result = render_layer_workflow(
        "",
        str(output_path),
        title="Acrab",
        title_position=[72, 92],
        title_font_size=32,
        frame_step=3,
        max_long_edge=160,
        canvas={"width": 320, "height": 180, "fps": 30, "total_frames": 30},
        overlay_layers=[
            {
                "id": "bouncing_ball",
                "type": "html",
                "html": "<div style='left:20px;top:20px;width:32px;height:32px;background:#80d8ff;border-radius:16px'>●</div>",
                "z_index": 10,
            }
        ],
    )

    assert result == str(output_path.resolve())
    assert output_path.exists()
    flow_manifest = json.loads(output_path.with_suffix(".layer-flow.json").read_text(encoding="utf-8"))
    authored = flow_manifest["authored_plan"]
    assert authored["metadata"]["blank_canvas"] is True
    assert authored["metadata"]["source_input"] == ""
    assert authored["total_frames"] == 30
    assert all(layer["id"] != "source_video" for layer in authored["layers"])
    assert authored["layers"][0]["type"] == "html"
    assert "html" in authored["layers"][0]


def test_plan_engine_executes_blank_canvas_layer_workflow_step(tmp_path: Path) -> None:
    output_path = tmp_path / "engine-blank-layer-flow.mp4"
    engine = PlanEngine(root_dir=tmp_path / "engine")
    plan = {
        "version": "2.1",
        "goal": "Render a prompt-only bouncing-ball title",
        "steps": [
            {
                "id": "step_1",
                "function": "gemia.video.layer_flow.render_layer_workflow",
                "args": {
                    "title": "Acrab",
                    "title_font_size": 32,
                    "canvas": {"width": 320, "height": 180, "fps": 30, "total_frames": 30},
                    "frame_step": 3,
                    "max_long_edge": 160,
                    "overlay_layers": [
                        {
                            "id": "ball",
                            "type": "solid",
                            "color": [0.5, 0.85, 1.0, 1.0],
                            "position": [24, 24],
                            "size": [24, 24],
                            "duration": 30,
                            "z_index": 20,
                        }
                    ],
                },
                "input": "$input",
                "output": "$output",
            }
        ],
    }

    result = engine.execute(plan, "", str(output_path))

    assert result == str(output_path.resolve())
    assert output_path.exists()
    flow_manifest = json.loads(output_path.with_suffix(".layer-flow.json").read_text(encoding="utf-8"))
    assert flow_manifest["authored_plan"]["metadata"]["blank_canvas"] is True


def test_render_layer_workflow_materializes_builtin_lumeri_primitive_url(tmp_path: Path) -> None:
    output_path = tmp_path / "builtin-ball-layer-flow.mp4"
    remote_url = "https://lumeri.ai/assets/primitives/ball.png"

    result = render_layer_workflow(
        "",
        str(output_path),
        canvas={"width": 320, "height": 180, "fps": 30, "total_frames": 30},
        frame_step=5,
        max_long_edge=160,
        overlay_layers=[
            {
                "id": "bouncing_ball",
                "type": "image",
                "source": remote_url,
                "color": [1.0, 0.84, 0.12, 1.0],
                "position": [24, 24],
                "size": [32, 32],
                "duration": 30,
                "z_index": 20,
                "keyframes": {"scale": {"0": 1.0, "15": 1.2, "29": 1.0}},
            },
            {
                "id": "wordmark",
                "type": "text",
                "text": "Acrab",
                "position": [82, 72],
                "font_config": {"size": 28},
                "duration": 30,
                "z_index": 10,
            },
        ],
    )

    assert result == str(output_path.resolve())
    assert output_path.exists()
    flow_manifest = json.loads(output_path.with_suffix(".layer-flow.json").read_text(encoding="utf-8"))
    authored_layer = flow_manifest["authored_plan"]["layers"][0]
    assert authored_layer["metadata"]["original_source_url"] == remote_url
    assert authored_layer["source"] != remote_url
    assert Path(authored_layer["source"]).exists()
    primitive_image = PILImage.open(authored_layer["source"]).convert("RGBA")
    center = primitive_image.getpixel((16, 16))
    assert center[0] > 220
    assert center[1] > 170
    assert center[2] < 90


def test_render_layer_workflow_materializes_builtin_shadow_primitive(tmp_path: Path) -> None:
    output_path = tmp_path / "builtin-shadow-layer-flow.mp4"
    remote_url = "https://lumeri.ai/assets/primitives/shadow.png"

    result = render_layer_workflow(
        "",
        str(output_path),
        canvas={"width": 320, "height": 180, "fps": 30, "total_frames": 12},
        frame_step=3,
        max_long_edge=160,
        overlay_layers=[
            {
                "id": "contact_shadow",
                "type": "image",
                "source": remote_url,
                "position": [90, 118],
                "size": [96, 18],
                "duration": 12,
                "opacity": 0.7,
                "z_index": 4,
            },
            {
                "id": "wordmark",
                "type": "text",
                "text": "Acrab",
                "position": [82, 72],
                "font_config": {"size": 28},
                "duration": 12,
                "z_index": 10,
            },
        ],
    )

    assert result == str(output_path.resolve())
    flow_manifest = json.loads(output_path.with_suffix(".layer-flow.json").read_text(encoding="utf-8"))
    authored_layer = flow_manifest["authored_plan"]["layers"][0]
    assert authored_layer["metadata"]["original_source_url"] == remote_url
    assert authored_layer["source"] != remote_url
    assert Path(authored_layer["source"]).exists()


def test_shadow_preview_scales_position_keyframes_and_solid_sizes() -> None:
    scaled = _scale_layer_plan(
        {
            "width": 1280,
            "height": 720,
            "fps": 30,
            "total_frames": 30,
            "layers": [
                {
                    "id": "floor",
                    "type": "solid",
                    "color": [0.0, 0.0, 0.0, 1.0],
                    "position": [100, 200],
                    "size": [400, 20],
                    "duration": 30,
                },
                {
                    "id": "ball",
                    "type": "image",
                    "source": "/tmp/ball.png",
                    "position": [0, 0],
                    "size": [60, 60],
                    "duration": 30,
                    "keyframes": {
                        "position": {
                            "points": [
                                {"frame": 0, "value": [320, 240]},
                                {"frame": 10, "value": [640, 120]},
                            ]
                        }
                    },
                },
            ],
        },
        max_long_edge=640,
    )

    assert scaled["width"] == 640
    assert scaled["height"] == 360
    assert scaled["layers"][0]["position"] == [50, 100]
    assert scaled["layers"][0]["size"] == [200, 10]
    assert scaled["layers"][1]["keyframes"]["position"]["points"][0]["value"] == [160.0, 120.0]
    assert scaled["layers"][1]["keyframes"]["position"]["points"][1]["value"] == [320.0, 60.0]


def test_layer_plan_supports_position_keyframes_for_motion_graphics() -> None:
    stack = execute_layer_plan(
        {
            "width": 100,
            "height": 60,
            "fps": 30,
            "total_frames": 11,
            "layers": [
                {
                    "id": "moving_dot",
                    "type": "solid",
                    "color": [1.0, 0.0, 0.0, 1.0],
                    "position": [10, 12],
                    "size": [10, 10],
                    "duration": 11,
                    "keyframes": {
                        "position": {
                            "points": [
                                {"frame": 0, "value": [10, 12]},
                                {"frame": 10, "value": [70, 12]},
                            ]
                        }
                    },
                }
            ],
        }
    )

    first = stack.render_frame(0)
    last = stack.render_frame(10)

    assert first[16, 14, 3] > 0.9
    assert first[16, 74, 3] < 0.1
    assert last[16, 74, 3] > 0.9
    assert np.sum(np.abs(first - last)) > 1.0


def test_execute_layer_plan_respects_solid_layer_size() -> None:
    stack = execute_layer_plan(
        {
            "width": 80,
            "height": 60,
            "fps": 30,
            "total_frames": 1,
            "layers": [
                {
                    "id": "small_solid",
                    "type": "solid",
                    "color": [0.2, 0.8, 1.0, 1.0],
                    "position": [10, 12],
                    "size": [16, 14],
                    "duration": 1,
                }
            ],
        }
    )

    frame = stack.render_frame(0)
    assert frame[12:26, 10:26, 3].max() == 1.0
    assert frame[:8, :, 3].max() == 0.0
    assert frame[:, 32:, 3].max() == 0.0


def test_layer_workflow_rejects_directory_input(tmp_path: Path) -> None:
    output_path = tmp_path / "directory-input.mp4"

    with pytest.raises(IsADirectoryError, match="must be a media file"):
        render_layer_workflow(str(tmp_path), str(output_path), title="Directory")
