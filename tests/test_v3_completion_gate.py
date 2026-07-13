"""RC4 one-shot completion-check gate for AgentLoopV3.

When the model stops emitting tool_calls and the gate is enabled
(COMPLETION_CHECK_ENABLED=True), the loop injects ONE user message
prompting goal completion verification, then re-runs the model once more.
On the second no-tool-call, the loop respects honest stop and emits turn_complete.
The gate prevents infinite loops while allowing the model a final chance to
course-correct.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator

import gemia.agent_loop_v3 as loop_mod
from gemia.agent_loop_v3 import AgentLoopV3, COMPLETION_CHECK_ENABLED


class _ModelStopsImmediately:
    """Model that emits no tool_calls — just text, then stop."""

    model = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def stream_turn(
        self, messages: list[dict[str, Any]], *, tools=None, temperature: float = 0.7
    ) -> AsyncIterator[dict[str, Any]]:
        del messages, tools, temperature
        self.calls += 1
        yield {"kind": "text_delta", "text": "Done processing."}
        yield {"kind": "finish", "reason": "stop"}


class _WorksThenActsAfterGate:
    """Does real work first (a tool call), stops → the gate fires because work
    was done → then course-corrects with a SECOND tool call after seeing the
    gate → stops. Exercises both 'gate fires after work' and 'post-gate tool
    dispatch still works'."""

    model = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def stream_turn(
        self, messages: list[dict[str, Any]], *, tools=None, temperature: float = 0.7
    ) -> AsyncIterator[dict[str, Any]]:
        del tools, temperature
        self.calls += 1
        if self.calls == 1:
            # Real work — a tool call marks the turn as having done something.
            yield {"kind": "tool_call_start", "index": 0, "id": "c1", "name": "fake_ok"}
            yield {"kind": "tool_call_args_delta", "index": 0, "delta": "{}"}
            yield {"kind": "finish", "reason": "tool_calls"}
            return
        if self.calls == 2:
            # Stop after work → the gate should fire (work was done).
            yield {"kind": "text_delta", "text": "第一步做完了。"}
            yield {"kind": "finish", "reason": "stop"}
            return
        if self.calls == 3:
            # Post-gate: only act if the goal-check nudge was actually injected.
            has_nudge = any(
                msg.get("role") == "user"
                and isinstance(msg.get("content"), str)
                and "目标核对" in msg["content"]
                for msg in messages
            )
            if has_nudge:
                yield {"kind": "tool_call_start", "index": 0, "id": "c2", "name": "fake_ok"}
                yield {"kind": "tool_call_args_delta", "index": 0, "delta": "{}"}
                yield {"kind": "finish", "reason": "tool_calls"}
                return
            yield {"kind": "text_delta", "text": "No nudge found; error."}
            yield {"kind": "finish", "reason": "stop"}
            return
        # After the post-gate tool result: stop for good (gate is one-shot).
        yield {"kind": "text_delta", "text": "都做完了。"}
        yield {"kind": "finish", "reason": "stop"}


def test_no_gate_for_pure_conversation(tmp_path: Path) -> None:
    """A pure conversational turn — the model answers in plain text and does no
    work (no tool calls, no assets, no failures) — must NOT trigger the
    pre-delivery gate. The gate reviews work; with nothing done, firing it only
    forces a redundant second reply (the "已完成…" report / rule meta-commentary
    we want gone). So a hello/identity/thanks turn is a single natural reply."""
    if not COMPLETION_CHECK_ENABLED:
        return

    client = _ModelStopsImmediately()
    events: list[dict[str, Any]] = []
    loop = AgentLoopV3(
        session_id="pure_conversation_no_gate",
        output_dir=tmp_path,
        gemini_client=client,  # type: ignore[arg-type]
        emit_event=events.append,
    )

    asyncio.run(loop.run_turn("你是谁"))

    # Exactly ONE model call — no gate round, no forced second reply.
    assert client.calls == 1, f"Expected 1 call, got {client.calls}"

    # No completion_check event: the gate never fired.
    completion_checks = [e for e in events if e.get("kind") == "completion_check"]
    assert (
        len(completion_checks) == 0
    ), f"Expected 0 completion_check, got {len(completion_checks)}"

    # Turn completes cleanly on the first stop.
    turn_completes = [e for e in events if e.get("kind") == "turn_complete"]
    assert len(turn_completes) == 1, f"Expected 1 turn_complete, got {len(turn_completes)}"


def test_completion_gate_disabled_ends_immediately(tmp_path: Path) -> None:
    """When gate is disabled, model should end immediately on first no-tool-call."""
    # Temporarily disable the gate.
    original_enabled = loop_mod.COMPLETION_CHECK_ENABLED
    loop_mod.COMPLETION_CHECK_ENABLED = False

    try:
        client = _ModelStopsImmediately()
        events: list[dict[str, Any]] = []
        loop = AgentLoopV3(
            session_id="completion_gate_disabled",
            output_dir=tmp_path,
            gemini_client=client,  # type: ignore[arg-type]
            emit_event=events.append,
        )

        asyncio.run(loop.run_turn("Do something"))

        # Should be called exactly once (no nudge, no retry).
        assert client.calls == 1, f"Expected 1 call, got {client.calls}"

        # No completion_check event should be emitted.
        completion_checks = [e for e in events if e.get("kind") == "completion_check"]
        assert (
            len(completion_checks) == 0
        ), f"Expected 0 completion_check events, got {len(completion_checks)}"

        # Should end immediately with turn_complete.
        turn_completes = [e for e in events if e.get("kind") == "turn_complete"]
        assert len(turn_completes) == 1, f"Expected 1 turn_complete, got {len(turn_completes)}"
    finally:
        loop_mod.COMPLETION_CHECK_ENABLED = original_enabled


def test_gate_fires_after_work_and_allows_post_gate_tool(
    tmp_path: Path, monkeypatch
) -> None:
    """When the turn actually did work (a tool ran), the gate DOES fire once —
    and the model may course-correct with another tool call after it, which is
    dispatched normally. Proves the gate still guards real work turns and that
    post-gate tool dispatch survives the new conversational carve-out."""
    if not COMPLETION_CHECK_ENABLED:
        return

    async def fake_ok(args: dict[str, Any], ctx) -> dict[str, Any]:
        del args, ctx
        return {"status": "ok"}

    monkeypatch.setitem(loop_mod.DISPATCHER, "fake_ok", fake_ok)

    client = _WorksThenActsAfterGate()
    events: list[dict[str, Any]] = []
    loop = AgentLoopV3(
        session_id="gate_after_work",
        output_dir=tmp_path,
        gemini_client=client,  # type: ignore[arg-type]
        emit_event=events.append,
    )

    asyncio.run(loop.run_turn("做点活儿"))

    # call1 tool → call2 stop → gate → call3 tool → call4 stop.
    assert client.calls == 4, f"Expected 4 calls, got {client.calls}"

    # Exactly one completion_check event (one-shot, even with post-gate work).
    completion_checks = [e for e in events if e.get("kind") == "completion_check"]
    assert len(completion_checks) == 1, f"Expected 1 completion_check, got {len(completion_checks)}"

    # Both the pre-gate and post-gate tool calls dispatched.
    readys = [e for e in events if e.get("kind") == "model_tool_call_ready"]
    assert len(readys) == 2, f"Expected 2 dispatched tool calls, got {len(readys)}"
    assert all(e.get("tool_name") == "fake_ok" for e in readys)


def test_completion_gate_no_infinite_loop_without_tools(tmp_path: Path) -> None:
    """Regression test: ensure the one-shot guard prevents infinite loops.
    A model that always stops should result in exactly 2 model calls
    and then turn_complete."""
    if not COMPLETION_CHECK_ENABLED:
        return

    client = _ModelStopsImmediately()
    events: list[dict[str, Any]] = []
    loop = AgentLoopV3(
        session_id="completion_gate_no_loop",
        output_dir=tmp_path,
        gemini_client=client,  # type: ignore[arg-type]
        emit_event=events.append,
    )

    # This should not hang or loop forever.
    asyncio.run(loop.run_turn("Stop immediately"))

    # _ModelStopsImmediately does no work, so the conversational carve-out
    # applies: no gate round, exactly ONE model call, then honest stop.
    assert client.calls == 1, f"Expected 1 call, got {client.calls}"

    # No completion_check (gate skipped for a zero-work turn).
    completion_checks = [e for e in events if e.get("kind") == "completion_check"]
    assert len(completion_checks) == 0, f"Expected 0 completion_check, got {len(completion_checks)}"

    # Verify we end with turn_complete (no error or infinite loop).
    turn_errors = [e for e in events if e.get("kind") == "turn_error"]
    assert len(turn_errors) == 0, f"Should have no errors, got {len(turn_errors)}"

    turn_completes = [e for e in events if e.get("kind") == "turn_complete"]
    assert len(turn_completes) == 1, f"Expected 1 turn_complete, got {len(turn_completes)}"
