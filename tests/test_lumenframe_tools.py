"""Tests for lumenframe layer editing tools (gemia.tools.layer)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from gemia.tools._context import AssetRegistry, ProgressUpdate, ToolContext
from gemia.tools import layer as layer_module

# Import lumenframe directly for test utilities
from lumenframe import find_layer


@pytest.fixture
def tmp_session(tmp_path: Path) -> ToolContext:
    """Create a minimal ToolContext for layer tests.

    Each test gets a unique session_id to avoid cross-test state pollution
    in the _DOC_CACHE.
    """
    import uuid
    session_id = f"test_layer_{uuid.uuid4().hex[:8]}"
    return ToolContext(
        session_id=session_id,
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _: None,
    )


def test_get_lumenframe_empty_doc(tmp_session: ToolContext) -> None:
    """Test get_lumenframe on a fresh, empty document."""
    result = asyncio.run(layer_module.dispatch_get({}, tmp_session))

    assert result["applied"] is True
    assert "canvas" in result
    assert "root_layers" in result
    assert "selection_ids" in result
    assert result["canvas"]["width"] == 1920
    assert result["canvas"]["height"] == 1080


def test_lumen_patch_add_single_layer(tmp_session: ToolContext) -> None:
    """Test adding a single layer via lumen_patch."""
    ops = [
        {
            "op": "add_layer",
            "type": "video",
            "name": "my_video",
        }
    ]
    result = asyncio.run(layer_module.dispatch_patch({"ops": ops}, tmp_session))

    assert result["applied"] is True
    assert result["ops_count"] == 1
    assert "my_video" in result["root_layers"]


def test_lumen_add_layer_convenience(tmp_session: ToolContext) -> None:
    """Test the convenience lumen_add_layer verb."""
    result = asyncio.run(
        layer_module.dispatch_add_layer(
            {"type": "image", "name": "photo"},
            tmp_session
        )
    )

    assert result["applied"] is True
    assert "photo" in result["root_layers"]


def test_lumen_patch_multiple_ops(tmp_session: ToolContext) -> None:
    """Test applying multiple ops in one patch."""
    ops = [
        {"op": "add_layer", "type": "video", "name": "v1"},
        {"op": "add_layer", "type": "audio", "name": "a1"},
        {"op": "add_layer", "type": "text", "name": "title"},
    ]
    result = asyncio.run(layer_module.dispatch_patch({"ops": ops}, tmp_session))

    assert result["applied"] is True
    assert result["ops_count"] == 3
    tree = result["root_layers"]
    assert "v1" in tree
    assert "a1" in tree
    assert "title" in tree


def test_lumen_set_opacity(tmp_session: ToolContext) -> None:
    """Test setting layer opacity."""
    # First add a layer
    add_result = asyncio.run(
        layer_module.dispatch_add_layer(
            {"type": "video", "name": "transparent"},
            tmp_session
        )
    )
    assert add_result["applied"] is True

    # Get the doc to extract the layer id
    get_result = asyncio.run(layer_module.dispatch_get({}, tmp_session))
    doc = layer_module._lumendoc(tmp_session)
    root = doc.get("root", {})
    children = root.get("children", [])
    assert len(children) > 0
    layer_id = children[0]["id"]

    # Set opacity
    opacity_result = asyncio.run(
        layer_module.dispatch_set_opacity(
            {"layer_id": layer_id, "opacity": 0.5},
            tmp_session
        )
    )

    assert opacity_result["applied"] is True
    doc = layer_module._lumendoc(tmp_session)
    layer = find_layer(doc, layer_id)
    assert layer is not None
    assert layer.get("opacity") == 0.5


def test_lumen_set_transform(tmp_session: ToolContext) -> None:
    """Test setting layer transform (position, scale, rotation)."""
    # Add a layer
    add_result = asyncio.run(
        layer_module.dispatch_add_layer(
            {"type": "video", "name": "moving"},
            tmp_session
        )
    )
    doc = layer_module._lumendoc(tmp_session)
    root = doc.get("root", {})
    children = root.get("children", [])
    layer_id = children[0]["id"]

    # Set transform
    result = asyncio.run(
        layer_module.dispatch_set_transform(
            {
                "layer_id": layer_id,
                "x": 100,
                "y": 200,
                "scale": 1.5,
                "rotation": 45,
            },
            tmp_session
        )
    )

    assert result["applied"] is True
    doc = layer_module._lumendoc(tmp_session)
    layer = find_layer(doc, layer_id)
    assert layer is not None
    transform = layer.get("transform", {})
    assert transform.get("x") == 100
    assert transform.get("y") == 200
    assert transform.get("scale_x") == 1.5
    assert transform.get("scale_y") == 1.5
    assert transform.get("rotation") == 45


def test_lumen_set_visibility(tmp_session: ToolContext) -> None:
    """Test hiding and showing layers."""
    # Add a layer
    add_result = asyncio.run(
        layer_module.dispatch_add_layer(
            {"type": "video", "name": "toggle_me"},
            tmp_session
        )
    )
    doc = layer_module._lumendoc(tmp_session)
    root = doc.get("root", {})
    children = root.get("children", [])
    layer_id = children[0]["id"]

    # Hide it
    hide_result = asyncio.run(
        layer_module.dispatch_set_visibility(
            {"layer_id": layer_id, "visible": False},
            tmp_session
        )
    )
    assert hide_result["applied"] is True

    doc = layer_module._lumendoc(tmp_session)
    layer = find_layer(doc, layer_id)
    assert layer is not None
    assert layer.get("visible") is False

    # Show it
    show_result = asyncio.run(
        layer_module.dispatch_set_visibility(
            {"layer_id": layer_id, "visible": True},
            tmp_session
        )
    )
    assert show_result["applied"] is True

    doc = layer_module._lumendoc(tmp_session)
    layer = layer_module.find_layer(doc, layer_id)
    assert layer.get("visible") is True


def test_lumen_delete_layer(tmp_session: ToolContext) -> None:
    """Test deleting layers."""
    # Add three layers
    for i, name in enumerate(["v1", "v2", "v3"]):
        asyncio.run(
            layer_module.dispatch_add_layer(
                {"type": "video", "name": name},
                tmp_session
            )
        )

    doc = layer_module._lumendoc(tmp_session)
    root = doc.get("root", {})
    children = root.get("children", [])
    assert len(children) == 3

    layer_id_to_delete = children[1]["id"]

    # Delete the middle one
    result = asyncio.run(
        layer_module.dispatch_delete_layer(
            {"layer_id": layer_id_to_delete},
            tmp_session
        )
    )
    assert result["applied"] is True

    doc = layer_module._lumendoc(tmp_session)
    root = doc.get("root", {})
    children = root.get("children", [])
    assert len(children) == 2
    assert all(c["id"] != layer_id_to_delete for c in children)


def test_lumen_move_layer(tmp_session: ToolContext) -> None:
    """Test moving a layer (reorder, reparent, retime)."""
    # Add a composition and some layers
    ops = [
        {"op": "add_layer", "type": "composition", "name": "comp1"},
        {"op": "add_layer", "type": "video", "name": "v1"},
    ]
    asyncio.run(layer_module.dispatch_patch({"ops": ops}, tmp_session))

    doc = layer_module._lumendoc(tmp_session)
    root = doc.get("root", {})
    children = root.get("children", [])
    comp_id = children[0]["id"]
    v1_id = children[1]["id"]

    # Move v1 into comp1 at start time 1.0
    result = asyncio.run(
        layer_module.dispatch_move_layer(
            {
                "layer_id": v1_id,
                "parent_id": comp_id,
                "start": 1.0,
            },
            tmp_session
        )
    )
    if not result["applied"]:
        pytest.fail(f"move_layer failed: {result}")
    assert result["applied"] is True

    doc = layer_module._lumendoc(tmp_session)
    comp = layer_module.find_layer(doc, comp_id)
    assert comp is not None
    comp_children = comp.get("children", [])
    assert len(comp_children) == 1
    assert comp_children[0]["id"] == v1_id
    assert comp_children[0]["start"] == 1.0


def test_lumen_select(tmp_session: ToolContext) -> None:
    """Test changing selection."""
    # Add two layers
    for name in ["a", "b"]:
        asyncio.run(
            layer_module.dispatch_add_layer(
                {"type": "video", "name": name},
                tmp_session
            )
        )

    doc = layer_module._lumendoc(tmp_session)
    root = doc.get("root", {})
    children = root.get("children", [])
    id_a = children[0]["id"]
    id_b = children[1]["id"]

    # Select both
    result = asyncio.run(
        layer_module.dispatch_select(
            {"layer_ids": [id_a, id_b], "mode": "replace"},
            tmp_session
        )
    )
    assert result["applied"] is True

    doc = layer_module._lumendoc(tmp_session)
    selection = doc.get("selection", [])
    assert set(selection) == {id_a, id_b}


def test_lumen_patch_error_handling(tmp_session: ToolContext) -> None:
    """Test that invalid patches return structured errors."""
    # Try to add a layer with unknown type
    ops = [
        {"op": "add_layer", "type": "unknown_type", "name": "bad"}
    ]
    result = asyncio.run(layer_module.dispatch_patch({"ops": ops}, tmp_session))

    assert result["applied"] is False
    assert "error_code" in result
    assert "error_message" in result


def test_lumen_patch_missing_args(tmp_session: ToolContext) -> None:
    """Test that malformed ops raise errors."""
    # add_layer with unknown type should fail validation
    ops = [{"op": "add_layer", "type": "nonexistent_type"}]
    result = asyncio.run(layer_module.dispatch_patch({"ops": ops}, tmp_session))

    assert result["applied"] is False
    assert "error_code" in result


def test_tool_registration_in_dispatcher() -> None:
    """Verify lumenframe tools are registered in DISPATCHER."""
    from gemia.tools import DISPATCHER

    assert "get_lumenframe" in DISPATCHER
    assert "lumen_patch" in DISPATCHER
    assert "lumen_add_layer" in DISPATCHER
    assert "lumen_set_transform" in DISPATCHER
    assert "lumen_set_opacity" in DISPATCHER
    assert "lumen_delete_layer" in DISPATCHER
    assert "lumen_move_layer" in DISPATCHER
    assert "lumen_set_visibility" in DISPATCHER
    assert "lumen_select" in DISPATCHER


def test_tool_schemas_in_registry() -> None:
    """Verify lumenframe tool schemas are in TOOL_SCHEMAS."""
    from gemia.tools import TOOL_SCHEMAS

    tool_names = [t["function"]["name"] for t in TOOL_SCHEMAS]

    assert "get_lumenframe" in tool_names
    assert "lumen_patch" in tool_names
    assert "lumen_add_layer" in tool_names
    assert "lumen_set_transform" in tool_names
    assert "lumen_set_opacity" in tool_names
    assert "lumen_delete_layer" in tool_names
    assert "lumen_move_layer" in tool_names
    assert "lumen_set_visibility" in tool_names
    assert "lumen_select" in tool_names


def test_lumenframe_doc_persistence_across_calls(tmp_session: ToolContext) -> None:
    """Verify that the document state persists across tool calls."""
    # Add a layer
    asyncio.run(
        layer_module.dispatch_add_layer(
            {"type": "video", "name": "persistent"},
            tmp_session
        )
    )

    # Get the doc and inspect it
    get_result = asyncio.run(layer_module.dispatch_get({}, tmp_session))
    assert "persistent" in get_result["root_layers"]

    # Call a different dispatcher, then check the layer is still there
    doc = layer_module._lumendoc(tmp_session)
    root = doc.get("root", {})
    children = root.get("children", [])
    layer_id = children[0]["id"]

    asyncio.run(
        layer_module.dispatch_set_opacity(
            {"layer_id": layer_id, "opacity": 0.7},
            tmp_session
        )
    )

    # Check that the layer is still there with updated opacity
    doc = layer_module._lumendoc(tmp_session)
    layer = find_layer(doc, layer_id)
    assert layer is not None
    assert layer["name"] == "persistent"
    assert layer.get("opacity") == 0.7


def test_delete_layer_removes_from_tree(tmp_session: ToolContext) -> None:
    """Test that delete_layer removes layer from tree."""
    # Add a layer via convenience verb
    result = asyncio.run(
        layer_module.dispatch_add_layer(
            {"type": "video", "name": "to_delete"},
            tmp_session
        )
    )
    assert result["applied"] is True

    # Delete it
    doc = layer_module._lumendoc(tmp_session)
    layer_id = doc["root"]["children"][0]["id"]

    del_result = asyncio.run(
        layer_module.dispatch_delete_layer({"layer_id": layer_id}, tmp_session)
    )
    assert del_result["applied"] is True

    # Verify it's gone
    doc = layer_module._lumendoc(tmp_session)
    assert len(doc["root"].get("children", [])) == 0


def test_move_layer_reparents(tmp_session: ToolContext) -> None:
    """Test move_layer with reparenting."""
    # Create composition and layer
    ops = [
        {"op": "add_layer", "type": "composition", "name": "parent"},
        {"op": "add_layer", "type": "video", "name": "child"},
    ]
    asyncio.run(layer_module.dispatch_patch({"ops": ops}, tmp_session))

    doc = layer_module._lumendoc(tmp_session)
    parent_id = doc["root"]["children"][0]["id"]
    child_id = doc["root"]["children"][1]["id"]

    # Move child into parent
    result = asyncio.run(
        layer_module.dispatch_move_layer(
            {"layer_id": child_id, "parent_id": parent_id},
            tmp_session
        )
    )
    assert result["applied"] is True

    # Verify parentage
    doc = layer_module._lumendoc(tmp_session)
    parent = find_layer(doc, parent_id)
    assert parent is not None
    assert len(parent.get("children", [])) == 1
    assert parent["children"][0]["id"] == child_id


def test_set_visibility_toggle(tmp_session: ToolContext) -> None:
    """Test toggling visibility on a layer."""
    # Add layer
    asyncio.run(
        layer_module.dispatch_add_layer(
            {"type": "audio", "name": "sound"},
            tmp_session
        )
    )
    doc = layer_module._lumendoc(tmp_session)
    layer_id = doc["root"]["children"][0]["id"]

    # Hide it
    result = asyncio.run(
        layer_module.dispatch_set_visibility(
            {"layer_id": layer_id, "visible": False},
            tmp_session
        )
    )
    assert result["applied"] is True

    doc = layer_module._lumendoc(tmp_session)
    layer = find_layer(doc, layer_id)
    assert layer is not None
    assert layer.get("visible") is False


def test_select_multiple_layers(tmp_session: ToolContext) -> None:
    """Test multi-layer selection."""
    # Add three layers
    for name in ["v1", "v2", "v3"]:
        asyncio.run(
            layer_module.dispatch_add_layer(
                {"type": "video", "name": name},
                tmp_session
            )
        )

    doc = layer_module._lumendoc(tmp_session)
    ids = [c["id"] for c in doc["root"]["children"]]

    # Select first two
    result = asyncio.run(
        layer_module.dispatch_select(
            {"layer_ids": ids[:2], "mode": "replace"},
            tmp_session
        )
    )
    assert result["applied"] is True

    doc = layer_module._lumendoc(tmp_session)
    selection = doc.get("selection", [])
    assert set(selection) == set(ids[:2])


def test_invalid_op_returns_structured_error(tmp_session: ToolContext) -> None:
    """Test that invalid ops return structured error_code."""
    ops = [{"op": "invalid_op_type", "some_arg": "value"}]
    result = asyncio.run(layer_module.dispatch_patch({"ops": ops}, tmp_session))

    assert result["applied"] is False
    assert "error_code" in result
    assert "error_message" in result
    # Should be a specific code from lumenframe
    assert result["error_code"] == "E_OP_UNKNOWN"


def test_prompt_injection_placeholders_replaced() -> None:
    """Test that system prompt placeholders are properly replaced."""
    from gemia.agent_loop_v3 import AgentLoopV3
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        loop = AgentLoopV3(
            session_id="test_injection",
            output_dir=Path(tmpdir),
            emit_event=lambda _: None,
        )

        messages = loop.render_messages()
        assert len(messages) > 0

        system_msg = messages[0]
        assert system_msg["role"] == "system"
        content = system_msg["content"]

        # Both placeholders should be replaced (not literally present)
        assert "{{lumenframe_ops}}" not in content
        assert "{{lumenframe}}" not in content

        # Should contain lumenframe sections
        assert "Layer Document" in content or "lumenframe" in content.lower()


def test_clear_lumenframe_session_removes_cache(tmp_session: ToolContext) -> None:
    """Test that clear_lumenframe_session cleans up the doc cache."""
    # Add a doc to the cache
    asyncio.run(
        layer_module.dispatch_add_layer(
            {"type": "video", "name": "test"},
            tmp_session
        )
    )

    # Verify it's in cache
    assert tmp_session.session_id in layer_module._DOC_CACHE

    # Clear it
    layer_module.clear_lumenframe_session(tmp_session.session_id)

    # Verify it's gone
    assert tmp_session.session_id not in layer_module._DOC_CACHE
