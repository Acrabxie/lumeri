from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image
import pytest

from gemia.budget_guard import BudgetGuard
from gemia.plan_mode import is_plan_safe
from gemia.project_store import ProjectHandle
from gemia.tools import DISPATCHER
from gemia.tools import assemble_quanta as assemble_tool
from gemia.tools import refine_quantum as refine_tool
from gemia.tools._context import AssetRegistry, ToolContext


def _ctx(tmp_path: Path) -> ToolContext:
    handle = ProjectHandle.open(tmp_path / "project", "quanta-refine", session_id="session_1")
    return ToolContext(
        session_id="session_1",
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _update: None,
        project=handle,
    )


def _call(name: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return asyncio.run(DISPATCHER[name](args, ctx))


def _quanta() -> dict[str, Any]:
    return {
        "slides": [
            {
                "id": "s1", "layout": "content", "title": "Before",
                "blocks": [{"id": "a", "kind": "shape", "role": "accent"}],
                "builds": [
                    {"id": "b1", "dwell_sec": 1, "visible_block_ids": []},
                    {"id": "b2", "dwell_sec": 2, "visible_block_ids": ["a"]},
                ],
            },
            {
                "id": "s2", "layout": "content", "title": "Stable",
                "blocks": [{"id": "b", "kind": "shape", "role": "accent"}],
                "builds": [
                    {"id": "b1", "dwell_sec": 3, "visible_block_ids": ["b"]},
                ],
            },
        ],
        "default_path": ["s1", "s2"],
    }


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (16, 9), "#5fc6de").save(output, format="PNG")
    return output.getvalue()


def _install_assemble_fakes(monkeypatch, ctx: ToolContext):
    calls = {"materialize": 0, "black": 0}

    def fake_materialize(_quanta, call_ctx, **_kwargs):
        calls["materialize"] += 1
        frames = []
        asset_ids = []
        for index, (slide_id, build_id, dwell) in enumerate((
            ("s1", "b1", 1.0), ("s1", "b2", 2.0), ("s2", "b1", 3.0),
        )):
            asset_id = call_ctx.registry.allocate_id("image")
            path = call_ctx.child_path(asset_id, ".png")
            path.write_bytes(_png())
            call_ctx.registry.register_output(
                asset_id, kind="image", path=path, summary="quanta frame"
            )
            asset_ids.append(asset_id)
            frames.append({
                "slide_index": 0 if slide_id == "s1" else 1,
                "build_index": index if slide_id == "s1" else 0,
                "slide_id": slide_id,
                "build_id": build_id,
                "dwell_sec": dwell,
                "asset_id": asset_id,
                "source_asset_ids": [],
                "overflow": [],
            })
        return {
            "kind": "quanta", "asset_id": asset_ids[0],
            "frame_asset_ids": asset_ids, "frames": frames,
            "pager_url": "/v3/quanta.html?session_id=session_1",
            "first_build_pager_url": "/v3/quanta.html?session_id=session_1",
            "slide_count": 2, "frame_count": 3, "overflow": [],
            "summary": "rendered", "rematerialization_scope": "quanta",
        }

    async def fake_black(call_ctx, **kwargs):
        cached = call_ctx.extra.get("quanta_black_video_cache")
        if isinstance(cached, dict) and cached.get("key") == kwargs["cache_key"]:
            return cached["asset_id"]
        calls["black"] += 1
        asset_id = call_ctx.registry.allocate_id("video")
        path = call_ctx.child_path(asset_id, ".mp4")
        path.write_bytes(b"fake video")
        call_ctx.registry.register_output(
            asset_id, kind="video", path=path, summary="quanta background"
        )
        call_ctx.extra["quanta_black_video_cache"] = {
            "key": kwargs["cache_key"], "asset_id": asset_id,
        }
        return asset_id

    monkeypatch.setattr(assemble_tool, "materialize_quanta_frame_assets", fake_materialize)
    monkeypatch.setattr(assemble_tool, "_ensure_black_video", fake_black)
    return calls


def test_refine_quantum_uses_current_cache_and_reassembles(tmp_path, monkeypatch) -> None:
    ctx = _ctx(tmp_path)
    _call("set_quanta", {"quanta": _quanta()}, ctx)
    calls = _install_assemble_fakes(monkeypatch, ctx)
    initial = _call("assemble_quanta", {}, ctx)
    old_cache_result = ctx.extra["quanta_frame_cache"]["result"]
    selective_calls = []

    def fake_rematerialize(quanta, _ctx, **kwargs):
        selective_calls.append(kwargs)
        assert quanta["slides"][0]["title"] == "After"
        assert kwargs["previous"] is old_cache_result
        result = dict(old_cache_result)
        result["rematerialization_scope"] = "slide"
        result["rematerialized_slide_id"] = "s1"
        result["summary"] = "rematerialized slide s1 and reused 1 unchanged slide"
        return result

    monkeypatch.setattr(refine_tool, "rematerialize_quanta_slide_assets", fake_rematerialize)
    result = _call(
        "refine_quantum", {"slide_id": "s1", "fields": {"title": "After"}}, ctx
    )

    assert result["refined"] is True and result["updated_slide"] == "s1"
    assert result["rematerialization_scope"] == "slide"
    assert result["frame_asset_ids"] == initial["frame_asset_ids"]
    assert ctx.project.load()["quanta"]["slides"][0]["title"] == "After"
    assert len(selective_calls) == 1
    assert calls == {"materialize": 1, "black": 1}
    assert len(ctx.project.load()["timeline"]["clips"]) == 4


def test_refine_quantum_invalid_id_keeps_current_frame_cache(tmp_path, monkeypatch) -> None:
    ctx = _ctx(tmp_path)
    _call("set_quanta", {"quanta": _quanta()}, ctx)
    _install_assemble_fakes(monkeypatch, ctx)
    _call("assemble_quanta", {}, ctx)
    cache = ctx.extra["quanta_frame_cache"]
    monkeypatch.setattr(
        refine_tool,
        "rematerialize_quanta_slide_assets",
        lambda *_args, **_kwargs: pytest.fail("must not render an invalid edit"),
    )

    with pytest.raises(ValueError, match="no slide with id ghost"):
        _call(
            "refine_quantum",
            {"slide_id": "ghost", "fields": {"title": "No"}},
            ctx,
        )
    assert ctx.extra["quanta_frame_cache"] is cache


def test_refine_quantum_empty_quanta_is_actionable(tmp_path) -> None:
    ctx = _ctx(tmp_path)
    with pytest.raises(ValueError, match="quanta is empty"):
        _call(
            "refine_quantum",
            {"slide_id": "s1", "fields": {"title": "No"}},
            ctx,
        )


def test_refine_quantum_is_registered_blocked_in_plan_and_budgeted() -> None:
    assert "refine_quantum" in DISPATCHER
    assert "stub" not in DISPATCHER["refine_quantum"].__qualname__.lower()
    assert is_plan_safe("refine_quantum") is False
    decision = BudgetGuard(max_usd=1, max_seconds=100).check("refine_quantum")
    assert decision.ok is True and decision.estimated_eta_sec == 5.0
