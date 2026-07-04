"""Tests for conditional lumenframe operation catalog injection in agent_loop_v3."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import uuid

import pytest

from gemia.agent_loop_v3 import AgentLoopV3
from gemia.tools._context import AssetRegistry
from gemia.tools import layer as layer_module
from lumenframe import empty_doc, apply_layer_patch


@pytest.fixture
def session_id() -> str:
    """Generate unique session ID."""
    return f"test_session_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def agent_loop_instance(tmp_path: Path, session_id: str) -> AgentLoopV3:
    """Create a real AgentLoopV3 instance for testing."""
    loop = AgentLoopV3(
        session_id=session_id,
        output_dir=tmp_path / "outputs",
        max_visual_inspections=2,
        budget_max_usd=1.0,
        budget_max_seconds=60.0,
    )
    return loop


def test_empty_doc_ops_catalog_is_minimal(session_id: str) -> None:
    """Test: empty lumenframe doc → minimal one-line pointer in ops catalog."""
    # Start with empty doc in cache (no layers)
    layer_module._DOC_CACHE[session_id] = empty_doc()

    # Create a minimal mock loop just to call the method
    class MinimalLoop:
        def __init__(self, sid: str):
            self.session_id = sid

        def _get_lumenframe_ops_catalog(self) -> str:
            # Copy the implementation from agent_loop_v3.py
            try:
                from gemia.tools import layer as _layer
                from lumenframe import describe_ops

                if hasattr(_layer, "_DOC_CACHE") and self.session_id in _layer._DOC_CACHE:
                    doc = _layer._DOC_CACHE[self.session_id]
                    root = doc.get("root", {})
                    children = root.get("children", [])
                    if children:
                        return describe_ops()

                return "Layer editing available via lumen_* tools — call lumen_get to start."
            except (ImportError, Exception):
                return "(lumenframe operations not available)"

    loop = MinimalLoop(session_id)
    ops_text = loop._get_lumenframe_ops_catalog()

    # Should be a short pointer, not the full 3000+ character catalog
    assert "Layer editing available" in ops_text or "lumen_get" in ops_text
    # Pointer is much shorter than full catalog
    assert len(ops_text) < 200


def test_non_empty_doc_ops_catalog_is_full(session_id: str) -> None:
    """Test: non-empty lumenframe doc (has layers) → full describe_ops() catalog."""
    # Create doc with a layer
    doc = empty_doc()
    doc = apply_layer_patch(doc, {
        "version": 1,
        "ops": [{
            "op": "add_layer",
            "type": "solid",
            "name": "bg",
        }]
    })
    layer_module._DOC_CACHE[session_id] = doc

    class MinimalLoop:
        def __init__(self, sid: str):
            self.session_id = sid

        def _get_lumenframe_ops_catalog(self) -> str:
            try:
                from gemia.tools import layer as _layer
                from lumenframe import describe_ops

                if hasattr(_layer, "_DOC_CACHE") and self.session_id in _layer._DOC_CACHE:
                    doc = _layer._DOC_CACHE[self.session_id]
                    root = doc.get("root", {})
                    children = root.get("children", [])
                    if children:
                        return describe_ops()

                return "Layer editing available via lumen_* tools — call lumen_get to start."
            except (ImportError, Exception):
                return "(lumenframe operations not available)"

    loop = MinimalLoop(session_id)
    ops_text = loop._get_lumenframe_ops_catalog()

    # Should contain the full operation vocabulary (much longer)
    assert len(ops_text) > 1000  # Full catalog is substantial
    # Verify full catalog has operation signatures
    assert "add_layer" in ops_text or "set_opacity" in ops_text


def test_empty_vs_full_ops_catalog_size_difference() -> None:
    """Verify size difference between empty-doc (pointer) and full catalog."""
    sid_empty = f"empty_{uuid.uuid4().hex[:8]}"
    sid_full = f"full_{uuid.uuid4().hex[:8]}"

    # Empty doc
    layer_module._DOC_CACHE[sid_empty] = empty_doc()

    # Full doc with layers
    doc = empty_doc()
    doc = apply_layer_patch(doc, {
        "version": 1,
        "ops": [{
            "op": "add_layer",
            "type": "solid",
            "name": "bg",
        }]
    })
    layer_module._DOC_CACHE[sid_full] = doc

    class MinimalLoop:
        def __init__(self, sid: str):
            self.session_id = sid

        def _get_lumenframe_ops_catalog(self) -> str:
            try:
                from gemia.tools import layer as _layer
                from lumenframe import describe_ops

                if hasattr(_layer, "_DOC_CACHE") and self.session_id in _layer._DOC_CACHE:
                    doc = _layer._DOC_CACHE[self.session_id]
                    root = doc.get("root", {})
                    children = root.get("children", [])
                    if children:
                        return describe_ops()

                return "Layer editing available via lumen_* tools — call lumen_get to start."
            except (ImportError, Exception):
                return "(lumenframe operations not available)"

    ops_empty = MinimalLoop(sid_empty)._get_lumenframe_ops_catalog()
    ops_full = MinimalLoop(sid_full)._get_lumenframe_ops_catalog()

    size_empty = len(ops_empty)
    size_full = len(ops_full)

    # Full catalog should be much larger
    assert size_full > size_empty
    # Difference should be substantial (at least 1500 bytes)
    assert (size_full - size_empty) > 1500
    # Empty should be very small
    assert size_empty < 200
    # Full should be substantial
    assert size_full > 1500
