from __future__ import annotations

import json
from pathlib import Path

from gemia.engine import PlanEngine
from gemia.registry import catalog_for_prompt
from gemia.video.blender_link import (
    blender_link_capabilities,
    blender_link_status,
    render_blender_link_operation,
    render_blender_spatial_scene,
)


def test_blender_link_primitive_is_planner_visible() -> None:
    catalog = catalog_for_prompt()

    assert "gemia.video.blender_link.render_blender_spatial_scene" in catalog
    assert "gemia.video.blender_link.render_blender_link_operation" in catalog


def test_blender_link_status_shape() -> None:
    status = blender_link_status()

    assert isinstance(status["available"], bool)
    assert "blender_path" in status
    assert "version" in status


def test_blender_link_capabilities_list_backend_operations() -> None:
    capabilities = blender_link_capabilities()
    operation_ids = {item["id"] for item in capabilities["operations"]}

    assert capabilities["protocol"] == "lumerilink.blender.v1"
    assert capabilities["execute_endpoint"] == "/blender-link/execute"
    assert {"spatial_scene", "parallax_orbit", "depth_grid", "neon_hologram"} <= operation_ids


def test_render_blender_spatial_fallback_writes_video(sample_video_path: str, tmp_path: Path) -> None:
    output = tmp_path / "spatial.mp4"

    result = render_blender_spatial_scene(
        sample_video_path,
        str(output),
        duration_sec=0.35,
        width=320,
        height=180,
        fps=12,
        prefer_blender=False,
        preserve_audio=False,
    )
    metadata = json.loads(output.with_suffix(".blenderlink.json").read_text(encoding="utf-8"))

    assert result == str(output)
    assert output.exists()
    assert metadata["effect"] == "lumerilink_blender_spatial_scene"
    assert metadata["operation"] == "spatial_scene"
    assert metadata["renderer"] == "opencv_fallback"


def test_render_blender_link_operation_fallback_writes_operation_metadata(sample_video_path: str, tmp_path: Path) -> None:
    output = tmp_path / "depth-grid.mp4"

    result = render_blender_link_operation(
        sample_video_path,
        str(output),
        operation="depth_grid",
        duration_sec=0.3,
        width=320,
        height=180,
        fps=12,
        prefer_blender=False,
        preserve_audio=False,
    )
    metadata = json.loads(output.with_suffix(".blenderlink.json").read_text(encoding="utf-8"))

    assert result == str(output)
    assert output.exists()
    assert metadata["protocol"] == "lumerilink.blender.v1"
    assert metadata["operation"] == "depth_grid"
    assert metadata["operation_label"] == "Depth grid"


def test_plan_engine_runs_blender_spatial_fallback(sample_video_path: str, tmp_path: Path) -> None:
    output = tmp_path / "engine-spatial.mp4"
    plan = {
        "version": "2.0",
        "goal": "render spatial video through LumeriLink",
        "steps": [
            {
                "id": "blender_spatial",
                "function": "gemia.video.blender_link.render_blender_link_operation",
                "args": {
                    "operation": "parallax_orbit",
                    "duration_sec": 0.3,
                    "width": 320,
                    "height": 180,
                    "fps": 12,
                    "prefer_blender": False,
                    "preserve_audio": False,
                },
                "input": "$input",
                "output": "$output",
            }
        ],
    }

    result = PlanEngine(root_dir=tmp_path / "engine").execute(plan, sample_video_path, str(output))

    assert result == str(output)
    assert output.exists()
    assert output.with_suffix(".blenderlink.json").exists()
