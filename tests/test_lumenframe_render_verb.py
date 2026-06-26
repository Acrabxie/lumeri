"""Tests for lumenframe render verb (lumen_render dispatcher)."""
from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile

import pytest

from gemia.tools._context import AssetRegistry, ToolContext
from gemia.tools import layer as layer_module
from lumenframe import apply_layer_patch, empty_doc


@pytest.fixture
def tmp_session(tmp_path: Path) -> ToolContext:
    """Create a ToolContext with output directory."""
    import uuid
    session_id = f"render_test_{uuid.uuid4().hex[:8]}"
    return ToolContext(
        session_id=session_id,
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _: None,
    )


def patch(*ops):
    """Helper to create a LayerPatch."""
    return {"version": 1, "ops": list(ops)}


def test_render_video_mp4(tmp_session: ToolContext) -> None:
    """Test rendering a doc with solids to MP4 video."""
    # Build a simple doc with solid layers and text
    doc = empty_doc(width=320, height=240, fps=10)

    # Background (black solid)
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "bg", "type": "solid",
        "color": "#000000", "duration": 1.0
    }))

    # Red solid on top
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "red", "type": "solid",
        "color": "#FF0000", "duration": 1.0
    }))

    # Save doc to cache
    layer_module._save_lumendoc(tmp_session, doc)

    # Render to MP4
    result = asyncio.run(
        layer_module.dispatch_render(
            {"format": "video"},
            tmp_session
        )
    )

    assert result["applied"] is True
    assert result["asset_id"] is not None
    assert result["width"] == 320
    assert result["height"] == 240
    assert result["fps"] == 10.0
    assert result["total_frames"] == 10
    assert result["duration_sec"] == 1.0
    assert result["format"] == "mp4"

    # Verify output file exists and is non-empty
    out_path = Path(result["path"])
    assert out_path.exists()
    assert out_path.stat().st_size > 0


def test_render_frame_png(tmp_session: ToolContext) -> None:
    """Test rendering a single frame to PNG."""
    doc = empty_doc(width=256, height=192, fps=10)

    # Green solid
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "green", "type": "solid",
        "color": "#00FF00", "duration": 1.0
    }))

    layer_module._save_lumendoc(tmp_session, doc)

    # Render frame 0 as PNG
    result = asyncio.run(
        layer_module.dispatch_render(
            {"format": "frame", "frame_index": 0},
            tmp_session
        )
    )

    assert result["applied"] is True
    assert result["asset_id"] is not None
    assert result["width"] == 256
    assert result["height"] == 192
    assert result["frame_index"] == 0
    assert result["total_frames"] == 10
    assert result["format"] == "png"

    # Verify PNG exists
    out_path = Path(result["path"])
    assert out_path.exists()
    assert out_path.suffix == ".png"
    assert out_path.stat().st_size > 0


def test_render_with_color_grade_effect(tmp_session: ToolContext) -> None:
    """Test rendering with per-layer effects (color_grade)."""
    doc = empty_doc(width=320, height=240, fps=5)

    # Layer with effect
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "colored", "type": "solid",
        "color": "#808080", "duration": 0.5,
        "effects": [
            {
                "type": "color_grade",
                "params": {"brightness": 0.2, "saturation": 0.5}
            }
        ]
    }))

    layer_module._save_lumendoc(tmp_session, doc)

    result = asyncio.run(
        layer_module.dispatch_render(
            {"format": "video"},
            tmp_session
        )
    )

    assert result["applied"] is True
    assert result["total_frames"] == 2  # 0.5 sec @ 5 fps
    assert Path(result["path"]).exists()


def test_render_registry_output_called(tmp_session: ToolContext) -> None:
    """Verify that register_output is called with correct params."""
    doc = empty_doc(width=160, height=120, fps=2)
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "blue", "type": "solid",
        "color": "#0000FF", "duration": 1.0
    }))

    layer_module._save_lumendoc(tmp_session, doc)

    # Render
    result = asyncio.run(
        layer_module.dispatch_render(
            {"format": "video"},
            tmp_session
        )
    )

    assert result["applied"] is True
    asset_id = result["asset_id"]

    # Check registry contains the output
    record = tmp_session.registry.get(asset_id)
    assert record is not None
    assert record.kind == "video"
    assert "lumenframe" in record.summary.lower()


def test_render_degraded_missing_media(tmp_session: ToolContext) -> None:
    """Test that render gracefully skips missing media assets."""
    doc = empty_doc(width=320, height=240, fps=10)

    # Solid layer (will render)
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "solid1", "type": "solid",
        "color": "#FFFFFF", "duration": 1.0
    }))

    # Image layer with missing asset (will be skipped)
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "missing_img", "type": "image",
        "asset_id": "nonexistent_asset", "duration": 1.0
    }))

    layer_module._save_lumendoc(tmp_session, doc)

    # Should render successfully despite missing asset
    result = asyncio.run(
        layer_module.dispatch_render(
            {"format": "video"},
            tmp_session
        )
    )

    assert result["applied"] is True
    assert Path(result["path"]).exists()


def test_render_frame_index_clamped(tmp_session: ToolContext) -> None:
    """Test that frame_index is clamped to valid range."""
    doc = empty_doc(width=160, height=120, fps=10)
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "layer1", "type": "solid",
        "color": "#FF00FF", "duration": 0.5  # 5 frames total
    }))

    layer_module._save_lumendoc(tmp_session, doc)

    # Try to render frame 999 (beyond available frames)
    result = asyncio.run(
        layer_module.dispatch_render(
            {"format": "frame", "frame_index": 999},
            tmp_session
        )
    )

    assert result["applied"] is True
    # Should clamp to last frame (4)
    assert result["frame_index"] == 4
    assert Path(result["path"]).exists()


def test_render_no_doc_error(tmp_session: ToolContext) -> None:
    """Test rendering empty session doc returns valid output."""
    # Don't save any doc; will use empty doc
    doc = empty_doc(width=100, height=100, fps=5)
    layer_module._save_lumendoc(tmp_session, doc)

    # Should render empty composition (transparent canvas)
    result = asyncio.run(
        layer_module.dispatch_render(
            {"format": "video"},
            tmp_session
        )
    )

    assert result["applied"] is True
    assert Path(result["path"]).exists()


def test_lumen_render_in_dispatcher() -> None:
    """Verify lumen_render is registered in DISPATCHER."""
    from gemia.tools import DISPATCHER

    assert "lumen_render" in DISPATCHER
    assert DISPATCHER["lumen_render"] is not None


def test_lumen_render_in_schemas() -> None:
    """Verify lumen_render schema is in TOOL_SCHEMAS."""
    from gemia.tools import TOOL_SCHEMAS

    tool_names = [t["function"]["name"] for t in TOOL_SCHEMAS]
    assert "lumen_render" in tool_names

    # Check schema structure
    schema = next(t for t in TOOL_SCHEMAS if t["function"]["name"] == "lumen_render")
    props = schema["function"]["parameters"]["properties"]
    assert "format" in props
    assert "frame_index" in props
