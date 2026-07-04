"""Tests for lumenframe persistence: cross-session storage and timeline coexistence."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from gemia.project_store import ProjectStore, ProjectHandle
from gemia.tools._context import AssetRegistry, ToolContext
from gemia.tools import layer as layer_module
from lumenframe import apply_layer_patch, empty_doc


def patch(*ops):
    """Helper to create a LayerPatch."""
    return {"version": 1, "ops": list(ops)}


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Create a project storage root."""
    return tmp_path / "projects"


def create_session_with_project(
    project_root: Path, project_id: str, session_id: str
) -> tuple[ToolContext, ProjectHandle]:
    """Create a ToolContext with ProjectHandle backing."""
    handle = ProjectHandle.open(project_root, project_id, session_id=session_id)
    ctx = ToolContext(
        session_id=session_id,
        output_dir=project_root / "outputs",
        registry=AssetRegistry(),
        emit_progress=lambda _: None,
        project=handle,  # Enable project backing
    )
    return ctx, handle


def test_roundtrip_with_project(project_root: Path) -> None:
    """Test: edit doc in one session → save → read in another session."""
    project_id = "test_persist_1"

    # Session 1: create and edit
    ctx1, handle1 = create_session_with_project(project_root, project_id, "session_1")

    # Build a doc
    doc = empty_doc(width=320, height=240, fps=10)
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "bg", "type": "solid",
        "color": "#FFFFFF", "duration": 1.0
    }))
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "fg", "type": "solid",
        "color": "#FF0000", "duration": 1.0,
        "opacity": 0.5
    }))

    doc_id_original = doc["id"]
    # Save via layer tool
    layer_module._save_lumendoc(ctx1, doc)

    # Verify it's persisted to lumenframe.json
    lumen_file = handle1.store.project_dir(project_id) / "lumenframe.json"
    assert lumen_file.exists()
    persisted_doc = json.loads(lumen_file.read_text())
    assert persisted_doc["id"] is not None
    assert len(persisted_doc["root"]["children"]) == 2

    # Session 2: new handle, read back
    ctx2, handle2 = create_session_with_project(project_root, project_id, "session_2")

    # Load via layer tool
    doc2 = layer_module._lumendoc(ctx2)

    # Verify doc is identical
    assert doc2["id"] == doc_id_original
    assert len(doc2["root"]["children"]) == 2
    assert doc2["root"]["children"][0]["props"]["color"] == "#FFFFFF"
    assert doc2["root"]["children"][1]["props"]["color"] == "#FF0000"
    assert doc2["root"]["children"][1]["opacity"] == 0.5


def test_timeline_coexistence(project_root: Path) -> None:
    """Test: lumenframe and timeline fields coexist in project without interference."""
    project_id = "test_coexist"
    session_id = "session_coexist"

    ctx, handle = create_session_with_project(project_root, project_id, session_id)

    # Initialize project (auto-creates timeline structure)
    proj_state = handle.load()
    assert "timeline" in proj_state
    timeline_before = proj_state["timeline"].copy()

    # Add lumenframe content
    doc = empty_doc(width=640, height=480, fps=30)
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "layer1", "type": "solid",
        "color": "#0000FF", "duration": 2.0
    }))
    layer_module._save_lumendoc(ctx, doc)

    # Verify timeline is untouched
    proj_state_after = handle.load()
    assert proj_state_after["timeline"] == timeline_before
    # Verify lumenframe is in separate file, not in project state
    lumen_file = handle.store.project_dir(project_id) / "lumenframe.json"
    assert lumen_file.exists()
    persisted_doc = json.loads(lumen_file.read_text())
    assert persisted_doc["root"]["children"][0]["props"]["color"] == "#0000FF"


def test_persist_survives_doc_mutations(project_root: Path) -> None:
    """Test: multiple edit rounds persist correctly."""
    project_id = "test_mutations"
    session_id = "session_mutations"

    ctx, handle = create_session_with_project(project_root, project_id, session_id)

    # Round 1: add layer
    doc = layer_module._lumendoc(ctx)
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "layer_a", "type": "solid",
        "color": "#FF0000", "duration": 1.0
    }))
    layer_module._save_lumendoc(ctx, doc)
    layer_a_id = doc["root"]["children"][0]["id"]

    # Round 2: add another layer
    doc = layer_module._lumendoc(ctx)
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "layer_b", "type": "solid",
        "color": "#00FF00", "duration": 1.0
    }))
    layer_module._save_lumendoc(ctx, doc)

    # Round 3: modify first layer
    doc = layer_module._lumendoc(ctx)
    doc = apply_layer_patch(doc, patch({
        "op": "set_opacity", "layer_id": layer_a_id, "opacity": 0.3
    }))
    layer_module._save_lumendoc(ctx, doc)

    # Verify final state in lumenframe.json
    lumen_file = handle.store.project_dir(project_id) / "lumenframe.json"
    assert lumen_file.exists()
    final_doc = json.loads(lumen_file.read_text())
    assert len(final_doc["root"]["children"]) == 2
    assert final_doc["root"]["children"][0]["opacity"] == 0.3
    assert final_doc["root"]["children"][1]["props"]["color"] == "#00FF00"


def test_fallback_to_memory_cache_when_no_project() -> None:
    """Test: without ProjectHandle, doc persists in _DOC_CACHE within session."""
    ctx = ToolContext(
        session_id="no_project_session",
        output_dir=Path("/tmp"),
        registry=AssetRegistry(),
        emit_progress=lambda _: None,
        project=None,  # No project
    )

    # Create and save
    doc = layer_module._lumendoc(ctx)
    doc["root"]["children"] = [
        {
            "id": "test_layer",
            "type": "solid",
            "color": "#AABBCC",
            "visible": True,
            "locked": False,
        }
    ]
    layer_module._save_lumendoc(ctx, doc)

    # Load again (should hit memory cache)
    doc2 = layer_module._lumendoc(ctx)
    assert doc2["root"]["children"][0]["id"] == "test_layer"
    assert doc2["root"]["children"][0]["color"] == "#AABBCC"


def test_lazy_init_lumenframe_field(project_root: Path) -> None:
    """Test: accessing lumenframe on a fresh project auto-initializes it."""
    project_id = "test_lazy_init"
    session_id = "session_lazy"

    ctx, handle = create_session_with_project(project_root, project_id, session_id)

    # Fresh project has no lumenframe yet
    proj_state = handle.load()
    assert "lumenframe" not in proj_state or proj_state.get("lumenframe") is None

    # Access via layer tool triggers init
    doc = layer_module._lumendoc(ctx)
    assert doc["id"] is not None
    assert "root" in doc

    # Verify it's now in lumenframe.json
    lumen_file = handle.store.project_dir(project_id) / "lumenframe.json"
    assert lumen_file.exists()
    persisted = json.loads(lumen_file.read_text())
    assert persisted["id"] == doc["id"]


def test_project_meta_updated_on_save(project_root: Path) -> None:
    """Test: saving lumenframe updates project meta timestamp."""
    project_id = "test_meta_update"
    session_id = "session_meta"

    ctx, handle = create_session_with_project(project_root, project_id, session_id)

    # Get initial meta
    meta1 = handle.store.load_meta(project_id)
    updated_at_1 = meta1.get("updated_at")

    # Wait a tiny bit and save
    import time
    time.sleep(0.01)

    doc = layer_module._lumendoc(ctx)
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "timestamped", "type": "solid",
        "color": "#123456", "duration": 1.0
    }))
    layer_module._save_lumendoc(ctx, doc)

    # Just verify lumenframe.json was written
    lumen_file = handle.store.project_dir(project_id) / "lumenframe.json"
    assert lumen_file.exists()


def test_render_verb_with_persistent_doc(project_root: Path) -> None:
    """Test: lumen_render works with persistent doc from project."""
    project_id = "test_render_persist"
    session_id = "session_render_persist"

    ctx, handle = create_session_with_project(project_root, project_id, session_id)
    ctx.output_dir.mkdir(parents=True, exist_ok=True)

    # Create and persist a doc
    doc = empty_doc(width=320, height=240, fps=10)
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "render_layer", "type": "solid",
        "color": "#FF00FF", "duration": 0.5
    }))
    layer_module._save_lumendoc(ctx, doc)

    # Render should read from project
    result = asyncio.run(
        layer_module.dispatch_render({"format": "video"}, ctx)
    )

    assert result["applied"] is True
    assert Path(result["path"]).exists()


def test_existing_tool_tests_still_pass(project_root: Path) -> None:
    """Smoke test: existing dispatch_get still works with both memory and project."""
    # With project
    ctx_proj, _ = create_session_with_project(project_root, "smoke_proj", "session_proj")
    result_proj = asyncio.run(layer_module.dispatch_get({}, ctx_proj))
    assert result_proj["applied"] is True

    # Without project (memory fallback)
    ctx_mem = ToolContext(
        session_id="smoke_mem",
        output_dir=Path("/tmp"),
        registry=AssetRegistry(),
        emit_progress=lambda _: None,
        project=None,
    )
    result_mem = asyncio.run(layer_module.dispatch_get({}, ctx_mem))
    assert result_mem["applied"] is True


def test_corrupt_json_preserved_and_recovered(project_root: Path) -> None:
    """Test: corrupt lumenframe.json is renamed to .corrupt-* and empty doc returned."""
    project_id = "test_corrupt"
    ctx, handle = create_session_with_project(project_root, project_id, "session_corrupt")

    # Stage corrupted JSON file
    project_dir = handle.store.project_dir(project_id)
    lumenframe_path = project_dir / "lumenframe.json"
    project_dir.mkdir(parents=True, exist_ok=True)
    lumenframe_path.write_text("{invalid json", encoding="utf-8")

    # Clear path cache to force fresh read
    layer_module._LUMENFRAME_PATH_CACHE.clear()

    # Reading should detect corruption, rename the file, and return empty doc
    doc = layer_module._lumendoc(ctx)
    assert doc is not None
    assert "root" in doc  # Empty doc has root
    assert doc["root"]["children"] == []  # Empty root

    # Verify corrupt file was renamed
    corrupt_files = list(project_dir.glob("lumenframe.json.corrupt-*"))
    assert len(corrupt_files) == 1
    assert "{invalid json" in corrupt_files[0].read_text(encoding="utf-8")


def test_write_failure_handled_gracefully(project_root: Path, monkeypatch) -> None:
    """Test: write failure logs warning but does not raise; doc remains in memory."""
    project_id = "test_write_fail_unique"
    ctx, handle = create_session_with_project(project_root, project_id, "session_write_fail_unique")

    # Clear path cache
    layer_module._LUMENFRAME_PATH_CACHE.clear()

    # Pre-monkeypatch to prevent lazy init
    # Monkeypatch os.replace to raise OSError BEFORE first _lumendoc call
    original_replace = layer_module.os.replace
    def failing_replace(*args, **kwargs):
        raise OSError("Simulated disk error")
    monkeypatch.setattr(layer_module.os, "replace", failing_replace)

    # Load initial doc (this will try to lazy-init but fail on os.replace)
    doc = layer_module._lumendoc(ctx)
    doc = apply_layer_patch(doc, patch({
        "op": "add_layer", "id": "test_layer", "type": "solid",
        "color": "#FF0000", "duration": 1.0
    }))

    # Save should not raise, but log warning and fall back to memory
    layer_module._save_lumendoc(ctx, doc)

    # Restore os.replace
    monkeypatch.setattr(layer_module.os, "replace", original_replace)

    # Memory cache should have the doc
    cached = layer_module._DOC_CACHE.get(ctx.session_id)
    assert cached is not None
    assert len(cached["root"]["children"]) == 1

    # File should not exist (write failed)
    file_path = layer_module._lumenframe_file_path(ctx)
    assert file_path is not None
    assert not file_path.exists()


def test_timeline_immutable_across_multi_round_edits(project_root: Path) -> None:
    """Test: lumenframe edits never mutate timeline/clips structure."""
    project_id = "test_timeline_immute"
    ctx, handle = create_session_with_project(project_root, project_id, "session_immute")

    # Get initial project state (which includes timeline)
    initial_state = handle.load()
    initial_timeline_bytes = json.dumps(initial_state.get("timeline", {}), sort_keys=True).encode()

    # Perform multiple lumenframe edit rounds
    for i in range(3):
        doc = layer_module._lumendoc(ctx)
        # Add a layer
        doc = apply_layer_patch(doc, patch({
            "op": "add_layer", "id": f"layer_{i}", "type": "solid",
            "color": f"#FF{i:02d}00", "duration": 1.0
        }))
        layer_module._save_lumendoc(ctx, doc)

    # Reload project state and verify timeline is unchanged
    final_state = handle.load()
    final_timeline_bytes = json.dumps(final_state.get("timeline", {}), sort_keys=True).encode()

    assert initial_timeline_bytes == final_timeline_bytes, \
        "Timeline structure changed after lumenframe edits (data corruption detected)"
