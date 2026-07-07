"""shotlist_* verb layer end-to-end through ToolContext + a real ProjectHandle.

Op semantics live in ``test_shotlist_patches.py``; here we assert the verb
adapters (``set_shotlist`` / ``update_shot`` / ``get_shotlist``) build the right
ops, persist through the patch log, and return the compact post-state view the
agent loop relays back to the model.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from gemia.project_store import ProjectHandle
from gemia.tools import DISPATCHER
from gemia.tools._context import AssetRegistry, ToolContext


def _ctx(tmp_path: Path) -> ToolContext:
    handle = ProjectHandle.open(tmp_path / "project", "v3-shotlist01", session_id="v3-shotlist01")
    return ToolContext(
        session_id="v3-shotlist01",
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _u: None,
        project=handle,
    )


def _call(verb: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return asyncio.run(DISPATCHER[verb](args, ctx))


_STORYBOARD = {
    "logline": "30s product promo",
    "style": "cinematic, warm",
    "target_duration_sec": 30,
    "scenes": [
        {"id": "sc1", "title": "Hook", "shots": [
            {"id": "hook", "description": "city sunrise timelapse", "duration_sec": 4,
             "source": "search", "search_query": "city sunrise timelapse",
             "on_screen_text": "Every morning."},
        ]},
        {"id": "sc2", "title": "Reveal", "shots": [
            {"id": "reveal", "description": "product hero rotate", "duration_sec": 5, "source": "generate"},
        ]},
    ],
}


def test_dispatchers_are_real_not_stubs():
    for name in ("set_shotlist", "update_shot", "get_shotlist"):
        assert name in DISPATCHER
        assert "stub" not in getattr(DISPATCHER[name], "__qualname__", "").lower()


def test_set_then_get_roundtrips(tmp_path):
    ctx = _ctx(tmp_path)
    out = _call("set_shotlist", {"shotlist": _STORYBOARD}, ctx)
    assert out["applied"] is True and out["shot_count"] == 2
    assert "Hook" in out["shotlist"] and 'search "city sunrise timelapse"' in out["shotlist"]

    got = _call("get_shotlist", {}, ctx)
    assert got["shot_count"] == 2
    ids = [s["id"] for sc in got["shotlist"]["scenes"] for s in sc["shots"]]
    assert ids == ["hook", "reveal"]
    assert got["shotlist"]["target_duration_sec"] == 30.0


def test_update_shot_marks_filled(tmp_path):
    ctx = _ctx(tmp_path)
    _call("set_shotlist", {"shotlist": _STORYBOARD}, ctx)
    out = _call("update_shot", {"shot_id": "hook",
                                "fields": {"asset_id": "v_001", "source": "search", "status": "filled"}}, ctx)
    assert out["updated_shot"] == "hook"
    got = _call("get_shotlist", {}, ctx)
    hook = got["shotlist"]["scenes"][0]["shots"][0]
    assert hook["asset_id"] == "v_001" and hook["status"] == "filled"
    # untouched sibling
    reveal = got["shotlist"]["scenes"][1]["shots"][0]
    assert reveal["asset_id"] is None and reveal["status"] == "draft"


def test_set_shotlist_rejects_non_object(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ValueError):
        _call("set_shotlist", {"shotlist": "not an object"}, ctx)


def test_update_shot_requires_fields(tmp_path):
    ctx = _ctx(tmp_path)
    _call("set_shotlist", {"shotlist": _STORYBOARD}, ctx)
    with pytest.raises(ValueError):
        _call("update_shot", {"shot_id": "hook", "fields": {}}, ctx)


def test_mutations_are_undoable(tmp_path):
    ctx = _ctx(tmp_path)
    _call("set_shotlist", {"shotlist": _STORYBOARD}, ctx)
    _call("update_shot", {"shot_id": "hook", "fields": {"asset_id": "v_001", "status": "filled"}}, ctx)
    # timeline_undo rewinds the last patch (the update_shot)
    _call("timeline_undo", {"steps": 1}, ctx)
    hook = _call("get_shotlist", {}, ctx)["shotlist"]["scenes"][0]["shots"][0]
    assert hook["asset_id"] is None and hook["status"] == "draft"
