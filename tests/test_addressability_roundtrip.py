"""FM2 gate — verbatim-addressability round-trip (charter §10 failure-mode-2, P9).

Every handle shown to the model in the host layer-tree MUST be usable verbatim
as a tool argument. The lived bug: the tree truncated layer ids to 12 chars
(``shape_1571eb06a035`` → ``shape_1571eb``), so every delete/edit the model
copied from the display failed ``E_NOT_FOUND``. These tests render layers whose
real ids are longer than 12 chars through the EXACT function that feeds both the
tool result (``get_lumenframe`` ``root_layers``) and the prompt injection
(``_get_lumenframe_prompt_text``), and assert the shown id is full and resolves.
Reintroducing any ``[:12]`` truncation makes the full id absent → RED. See
``gemia/docs/point-library-charter.md`` §10 (failure mode 2).
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from gemia.tools._context import AssetRegistry, ToolContext
from gemia.tools import layer as layer_module
from gemia.tools.layer import _compact_tree_summary
from lumenframe.model import find_layer

# Real ids are `<type>_<uuid.hex[:12]>` → 17-18 chars, longer than the old
# 12-char truncation width. These are the exact shapes the lived bug produced.
_LONG_IDS = ("shape_1571eb06a035", "text_f384694679cf", "group_0a1b2c3d4e5f")


def _doc_with_long_ids() -> dict:
    return {
        "root": {
            "id": "root", "type": "composition", "name": "Root", "visible": True,
            "children": [
                {"id": _LONG_IDS[0], "type": "shape", "name": "Orb", "visible": True},
                {"id": _LONG_IDS[1], "type": "text", "name": "Title", "visible": True},
                {
                    "id": _LONG_IDS[2], "type": "group", "name": "G", "visible": True,
                    "children": [
                        {"id": "shape_deadbeef0011", "type": "shape", "name": "Dot", "visible": True},
                    ],
                },
            ],
        }
    }


def _all_ids(node: dict) -> list[str]:
    ids = [node["id"]]
    for child in node.get("children") or []:
        ids.extend(_all_ids(child))
    return ids


def test_tree_shows_full_ids_verbatim_and_they_resolve() -> None:
    doc = _doc_with_long_ids()
    tree = _compact_tree_summary(doc["root"])
    for lid in _all_ids(doc["root"]):
        # Shown verbatim (a 12-char truncation would make the full id absent).
        assert lid in tree, f"id {lid!r} not shown verbatim in layer tree:\n{tree}"
        # Round-trip: the id the model would copy actually resolves.
        assert find_layer(doc, lid) is not None, f"shown id {lid!r} does not resolve"


def test_no_handle_is_ellipsis_truncated() -> None:
    tree = _compact_tree_summary(_doc_with_long_ids()["root"])
    assert "…" not in tree and "..." not in tree, (
        f"a single id must never be shown ellipsis-truncated:\n{tree}"
    )


def test_get_lumenframe_tool_selection_ids_match_shown_tree() -> None:
    """The full round-trip through the real tool: an id in ``selection_ids``
    must appear verbatim in ``root_layers`` and be deletable by that string."""
    ctx = ToolContext(
        session_id=f"test_addr_{uuid.uuid4().hex[:8]}",
        output_dir=Path("/tmp"),
        registry=AssetRegistry(),
        emit_progress=lambda _: None,
    )
    asyncio.run(
        layer_module.dispatch_add_layer(
            {"type": "shape", "name": "Vector Motion Demo"}, ctx
        )
    )
    got = asyncio.run(layer_module.dispatch_get({}, ctx))
    selected = got["selection_ids"][0]
    assert selected in got["root_layers"], (
        f"selected id {selected!r} not shown verbatim in root_layers:\n{got['root_layers']}"
    )
    deleted = asyncio.run(
        layer_module.dispatch_delete_layer({"layer_id": selected}, ctx)
    )
    assert deleted["applied"] is True, "id read back from the tree must be deletable verbatim"
