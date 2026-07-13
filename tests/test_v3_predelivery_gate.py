"""Pre-delivery gate (RC4+) tests: visual self-check + failure disclosure.

The gate rides the existing one-shot completion check: when the model stops
calling tools, the host injects ONE synthetic user message composed of
(a) a visual self-check (with thumbnails) when the turn registered new visual
assets, (b) a failure-disclosure list when tool calls actually failed, and
(c) the RC4 goal check. Fake-client pattern mirrors test_v3_completion_gate.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import shutil
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

import gemia.agent_loop_v3 as loop_mod
from gemia.agent_loop_v3 import AgentLoopV3

# Minimal valid 1x1 PNG so ffmpeg has a real image to thumbnail.
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class _ScriptedClient:
    """Call 1: one tool call. Call 2: plain text (triggers the gate).
    Call 3: confirmation text. Captures the messages of every call."""

    model = "fake"

    def __init__(self, tool_name: str, tool_args: str = "{}") -> None:
        self.calls = 0
        self.seen_messages: list[list[dict[str, Any]]] = []
        self._tool_name = tool_name
        self._tool_args = tool_args

    async def stream_turn(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[dict[str, Any]]:
        self.calls += 1
        self.seen_messages.append(copy.deepcopy(messages))
        if self.calls == 1:
            yield {
                "kind": "tool_call_start",
                "index": 0,
                "id": "c1",
                "name": self._tool_name,
            }
            yield {"kind": "tool_call_args_delta", "index": 0, "delta": self._tool_args}
            yield {"kind": "finish", "reason": "tool_calls"}
        elif self.calls == 2:
            yield {"kind": "text_delta", "text": "做完了。"}
            yield {"kind": "finish", "reason": "stop"}
        else:
            yield {"kind": "text_delta", "text": "已自检确认。"}
            yield {"kind": "finish", "reason": "stop"}


class _TextOnlyClient:
    """Never calls tools; used for the plain-gate control test."""

    model = "fake"

    def __init__(self) -> None:
        self.calls = 0
        self.seen_messages: list[list[dict[str, Any]]] = []

    async def stream_turn(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[dict[str, Any]]:
        self.calls += 1
        self.seen_messages.append(copy.deepcopy(messages))
        yield {"kind": "text_delta", "text": "好的。"}
        yield {"kind": "finish", "reason": "stop"}


def _gate_message(client) -> Any:
    """The synthetic user message injected by the gate = the last user message
    the model saw on its final call."""
    last_call = client.seen_messages[-1]
    users = [m for m in last_call if m.get("role") == "user"]
    assert users, "no user message found in final model call"
    return users[-1]["content"]


def _gate_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return " ".join(
        p.get("text", "") for p in content if p.get("type") == "text"
    )


def _completion_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e.get("kind") == "completion_check"]


def _make_loop(tmp_path: Path, client, events: list[dict[str, Any]]) -> AgentLoopV3:
    return AgentLoopV3(
        session_id="t-gate",
        output_dir=tmp_path,
        gemini_client=client,  # type: ignore[arg-type]
        emit_event=events.append,
    )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_visual_selfcheck_attaches_thumbnails(tmp_path, monkeypatch):
    png = tmp_path / "gen.png"
    png.write_bytes(_PNG_1X1)

    async def fake_gen(args: dict[str, Any], ctx) -> dict[str, Any]:
        asset_id = ctx.registry.allocate_id("image")
        ctx.registry.register_output(
            asset_id, kind="image", path=png, summary="fake generated image"
        )
        return {"asset_id": asset_id, "status": "ok"}

    monkeypatch.setitem(loop_mod.DISPATCHER, "fake_gen_image", fake_gen)

    events: list[dict[str, Any]] = []
    client = _ScriptedClient("fake_gen_image")
    loop = _make_loop(tmp_path, client, events)
    asyncio.run(loop.run_turn("用blender做个房子"))

    # tool round + gate round + confirmation round
    assert client.calls == 3
    checks = _completion_events(events)
    assert len(checks) == 1
    assert "visual_selfcheck" in checks[0]["sections"]
    assert "goal_check" in checks[0]["sections"]

    content = _gate_message(client)
    assert isinstance(content, list), "gate with thumbnails must be multimodal"
    kinds = {p.get("type") for p in content}
    assert "image_url" in kinds
    text = _gate_text(content)
    assert "视觉自检" in text
    assert "目标核对" in text
    assert "img_001" in text

    completes = [e for e in events if e.get("kind") == "turn_complete"]
    assert len(completes) == 1
    assert completes[0]["final_asset_ids"] == ["img_001"]


def test_failure_disclosure_lists_failed_calls(tmp_path, monkeypatch):
    async def fake_flaky(args: dict[str, Any], ctx) -> dict[str, Any]:
        raise RuntimeError("boom")

    monkeypatch.setitem(loop_mod.DISPATCHER, "fake_flaky", fake_flaky)

    events: list[dict[str, Any]] = []
    client = _ScriptedClient("fake_flaky")
    loop = _make_loop(tmp_path, client, events)
    asyncio.run(loop.run_turn("做个特效"))

    assert client.calls == 3
    checks = _completion_events(events)
    assert len(checks) == 1
    assert "failure_disclosure" in checks[0]["sections"]
    assert "visual_selfcheck" not in checks[0]["sections"]

    content = _gate_message(client)
    assert isinstance(content, str), "no visuals → gate stays plain text"
    assert "失败披露" in content
    assert "`fake_flaky`(E_UNCAUGHT)×1" in content
    assert "禁止把失败包装成成功" in content
    # Turn still completes honestly — disclosure is a nudge, not a stop.
    assert any(e.get("kind") == "turn_complete" for e in events)


def test_no_gate_for_pure_conversation(tmp_path):
    """A conversational turn that does no work (no tools, no assets, no
    failures) and is not in plan mode gets NO pre-delivery gate: the model's
    single natural reply stands. Firing the gate here would only force a
    redundant second reply — the robotic '已完成…' report we removed."""
    events: list[dict[str, Any]] = []
    client = _TextOnlyClient()
    loop = _make_loop(tmp_path, client, events)
    asyncio.run(loop.run_turn("你是谁"))

    # Exactly one model call — no gate round.
    assert client.calls == 1
    assert _completion_events(events) == []
    assert any(e.get("kind") == "turn_complete" for e in events)


def test_gate_degrades_when_thumbnails_fail(tmp_path, monkeypatch):
    png = tmp_path / "asset.png"
    png.write_bytes(_PNG_1X1)

    async def fake_gen(args: dict[str, Any], ctx) -> dict[str, Any]:
        asset_id = ctx.registry.allocate_id("image")
        ctx.registry.register_output(
            asset_id, kind="image", path=png, summary="image with broken thumbnailer"
        )
        return {"asset_id": asset_id, "status": "ok"}

    monkeypatch.setitem(loop_mod.DISPATCHER, "fake_gen_image", fake_gen)

    events: list[dict[str, Any]] = []
    client = _ScriptedClient("fake_gen_image")
    loop = _make_loop(tmp_path, client, events)
    # The asset file itself is fine — the THUMBNAILER is down. The gate must
    # degrade to a text-only visual section, not break the turn.
    import gemia.tools.analyze_media as am

    def _boom(kind, src, dst, duration):
        raise RuntimeError("thumbnailer down")

    monkeypatch.setattr(am, "_make_thumbnail", _boom)

    asyncio.run(loop.run_turn("做张图"))

    # Gate must survive: text-only visual section, turn completes.
    assert client.calls == 3
    checks = _completion_events(events)
    assert len(checks) == 1
    assert "visual_selfcheck" in checks[0]["sections"]
    content = _gate_message(client)
    assert isinstance(content, str), "thumbnail failure → text-only gate"
    assert "视觉自检" in content
    assert "analyze_media" in content, "no previews → tell model to self-inspect"
    assert any(e.get("kind") == "turn_complete" for e in events)


def test_job_failure_recorded_for_disclosure(tmp_path, monkeypatch):
    """Async jobs fail via a NORMAL check_job result (status='failed'), not an
    exception — the flagship silent-fallback scenario must reach the gate."""

    async def fake_check_job(args: dict[str, Any], ctx) -> dict[str, Any]:
        return {
            "job_id": "job_7",
            "status": "failed",
            "stderr_tail": "Veo quota exceeded",
        }

    monkeypatch.setitem(loop_mod.DISPATCHER, "check_job", fake_check_job)

    events: list[dict[str, Any]] = []
    client = _ScriptedClient("check_job", '{"job_id": "job_7"}')
    loop = _make_loop(tmp_path, client, events)
    asyncio.run(loop.run_turn("生成一段视频"))

    checks = _completion_events(events)
    assert len(checks) == 1
    assert "failure_disclosure" in checks[0]["sections"]
    content = _gate_message(client)
    assert "`job:job_7`(E_JOB_FAILED)×1" in _gate_text(content)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_gate_images_reclaimed_after_gate_round(tmp_path, monkeypatch):
    """Thumbnails ride exactly ONE model call; afterwards the message is
    rewritten to text so base64 never lingers in the rolling window."""
    png = tmp_path / "gen.png"
    png.write_bytes(_PNG_1X1)

    async def fake_gen(args: dict[str, Any], ctx) -> dict[str, Any]:
        asset_id = ctx.registry.allocate_id("image")
        ctx.registry.register_output(
            asset_id, kind="image", path=png, summary="fake generated image"
        )
        return {"asset_id": asset_id, "status": "ok"}

    monkeypatch.setitem(loop_mod.DISPATCHER, "fake_gen_image", fake_gen)

    events: list[dict[str, Any]] = []
    client = _ScriptedClient("fake_gen_image")
    loop = _make_loop(tmp_path, client, events)
    asyncio.run(loop.run_turn("做张图"))

    # The model DID see the images on its final call...
    assert isinstance(_gate_message(client), list)
    # ...but the persisted history has them replaced with a placeholder.
    gate_msgs = [
        m
        for m in loop._messages
        if m.get("role") == "user" and "视觉自检" in _gate_text(m.get("content"))
    ]
    assert len(gate_msgs) == 1
    persisted = gate_msgs[0]["content"]
    assert isinstance(persisted, str), "images must be reclaimed after the gate round"
    assert "回收" in persisted


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_gate_text_only_for_claude_provider(tmp_path, monkeypatch):
    png = tmp_path / "gen.png"
    png.write_bytes(_PNG_1X1)

    async def fake_gen(args: dict[str, Any], ctx) -> dict[str, Any]:
        asset_id = ctx.registry.allocate_id("image")
        ctx.registry.register_output(
            asset_id, kind="image", path=png, summary="fake generated image"
        )
        return {"asset_id": asset_id, "status": "ok"}

    monkeypatch.setitem(loop_mod.DISPATCHER, "fake_gen_image", fake_gen)

    events: list[dict[str, Any]] = []
    client = _ScriptedClient("fake_gen_image")
    client.provider = "claude"  # Anthropic API rejects OpenAI image_url parts
    loop = _make_loop(tmp_path, client, events)
    asyncio.run(loop.run_turn("做张图"))

    checks = _completion_events(events)
    assert "visual_selfcheck" in checks[0]["sections"]
    content = _gate_message(client)
    assert isinstance(content, str), "claude provider → text-only gate"
    assert "视觉自检" in content


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_visual_list_truncated_and_coverage_noted(tmp_path, monkeypatch):
    png = tmp_path / "gen.png"
    png.write_bytes(_PNG_1X1)

    async def fake_gen_many(args: dict[str, Any], ctx) -> dict[str, Any]:
        for _ in range(10):
            asset_id = ctx.registry.allocate_id("image")
            ctx.registry.register_output(
                asset_id, kind="image", path=png, summary="batch image"
            )
        return {"status": "ok", "count": 10}

    monkeypatch.setitem(loop_mod.DISPATCHER, "fake_gen_many", fake_gen_many)

    events: list[dict[str, Any]] = []
    client = _ScriptedClient("fake_gen_many")
    loop = _make_loop(tmp_path, client, events)
    asyncio.run(loop.run_turn("批量出图"))

    content = _gate_message(client)
    assert isinstance(content, list)
    images = [p for p in content if p.get("type") == "image_url"]
    assert len(images) == 3, "thumbnail cap must hold for batch turns"
    text = _gate_text(content)
    assert "等共 10 个" in text, "asset id list must be truncated in prose"
    assert "3/10" in text, "coverage note must state how many previews attached"
    assert "预览 img_" in text, "each image must be labeled with its asset id"


def test_plan_mode_goal_check_avoids_tool_push(tmp_path):
    events: list[dict[str, Any]] = []
    client = _TextOnlyClient()
    loop = _make_loop(tmp_path, client, events)
    loop.plan_mode = True
    asyncio.run(loop.run_turn("帮我规划一支宣传片"))

    content = _gate_message(client)
    assert isinstance(content, str)
    assert "目标核对" in content
    assert "计划模式" in content
    assert "立刻继续调用下一个工具" not in content, (
        "plan mode must not push the model into plan-gated tool calls"
    )
