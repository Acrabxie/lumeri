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


class _ModelStopsAfterNudge:
    """Model that emits no tool_calls on first call, but after nudge (second call),
    it checks messages and sees the completion nudge, emits text confirmation."""

    model = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def stream_turn(
        self, messages: list[dict[str, Any]], *, tools=None, temperature: float = 0.7
    ) -> AsyncIterator[dict[str, Any]]:
        del messages, tools, temperature
        self.calls += 1
        if self.calls == 1:
            # First response: no tools
            yield {"kind": "text_delta", "text": "Processing..."}
            yield {"kind": "finish", "reason": "stop"}
            return
        # Second call after nudge: confirm completion
        yield {"kind": "text_delta", "text": "Confirmed: work is complete."}
        yield {"kind": "finish", "reason": "stop"}


class _ModelCallsToolAfterNudge:
    """Model that emits no tool_calls on first call, but after nudge (second call),
    it realizes more work is needed and calls a tool. On third call (after tool result),
    it stops."""

    model = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def stream_turn(
        self, messages: list[dict[str, Any]], *, tools=None, temperature: float = 0.7
    ) -> AsyncIterator[dict[str, Any]]:
        del tools, temperature
        self.calls += 1
        if self.calls == 1:
            # First response: no tools
            yield {"kind": "text_delta", "text": "Checking..."}
            yield {"kind": "finish", "reason": "stop"}
            return
        if self.calls == 2:
            # Second call after nudge: decide to call a tool
            # Check if nudge is in messages (to verify it was injected)
            has_nudge = any(
                "目标核对" in msg.get("content", "") and msg.get("role") == "user"
                for msg in messages
            )
            if has_nudge:
                yield {
                    "kind": "tool_call_start",
                    "index": 0,
                    "id": "call_after_nudge",
                    "name": "search_library",
                }
                # Include a proper query arg so the tool doesn't fail
                yield {
                    "kind": "tool_call_args_delta",
                    "index": 0,
                    "delta": '{"query":"test search","kind":"any"}',
                }
                yield {"kind": "finish", "reason": "tool_calls"}
                return
            # Fallback (should not happen in this test)
            yield {"kind": "text_delta", "text": "No nudge found; error."}
            yield {"kind": "finish", "reason": "stop"}
            return
        # Third+ calls (after tool execution): just stop
        yield {"kind": "text_delta", "text": "Done after tool."}
        yield {"kind": "finish", "reason": "stop"}


def test_completion_gate_enabled_injects_one_nudge(tmp_path: Path) -> None:
    """When gate is enabled and model stops initially,
    exactly one nudge should be injected before the second model call.
    On second no-tool-call, turn ends."""
    # Skip if gate is disabled globally
    if not COMPLETION_CHECK_ENABLED:
        return

    client = _ModelStopsAfterNudge()
    events: list[dict[str, Any]] = []
    loop = AgentLoopV3(
        session_id="completion_gate_enabled",
        output_dir=tmp_path,
        gemini_client=client,  # type: ignore[arg-type]
        emit_event=events.append,
    )

    asyncio.run(loop.run_turn("Do something"))

    # Should be called twice: first (no tools) → nudge injected → second (no tools).
    assert client.calls == 2, f"Expected 2 calls, got {client.calls}"

    # There should be exactly one completion_check event.
    completion_checks = [e for e in events if e.get("kind") == "completion_check"]
    assert len(completion_checks) == 1, f"Expected 1 completion_check, got {len(completion_checks)}"

    # Should end with turn_complete (not loop forever).
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


def test_completion_gate_nudge_allows_tool_call(tmp_path: Path) -> None:
    """After nudge, model can call a tool. The tool should be dispatched normally."""
    if not COMPLETION_CHECK_ENABLED:
        return

    client = _ModelCallsToolAfterNudge()
    events: list[dict[str, Any]] = []
    loop = AgentLoopV3(
        session_id="completion_gate_tool_after_nudge",
        output_dir=tmp_path,
        gemini_client=client,  # type: ignore[arg-type]
        emit_event=events.append,
    )

    asyncio.run(loop.run_turn("Do something"))

    # Should be called at least twice: first (no tools) → nudge → second (tool call).
    # After the tool, the model is called again and stops, so 3+ calls.
    assert client.calls >= 2, f"Expected at least 2 calls, got {client.calls}"

    # Exactly one completion_check event.
    completion_checks = [e for e in events if e.get("kind") == "completion_check"]
    assert len(completion_checks) == 1, f"Expected 1 completion_check, got {len(completion_checks)}"

    # The tool call from the second model response should be dispatched.
    model_tool_call_readys = [
        e for e in events if e.get("kind") == "model_tool_call_ready"
    ]
    assert len(model_tool_call_readys) >= 1, "Tool call should be ready after nudge"

    # Tool name should be search_library (what the model calls after nudge).
    if model_tool_call_readys:
        assert (
            model_tool_call_readys[0].get("tool_name") == "search_library"
        ), f"Expected search_library, got {model_tool_call_readys[0].get('tool_name')}"


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

    # Exactly 1 call (gate is enabled, but model never stops on no-tools before...)
    # Actually, _ModelStopsImmediately always stops, so:
    # First call: no tools → nudge injected → continue
    # Second call: no tools again → emit turn_complete → return
    assert client.calls == 2, f"Expected 2 calls, got {client.calls}"

    # Verify we only have one completion_check.
    completion_checks = [e for e in events if e.get("kind") == "completion_check"]
    assert len(completion_checks) == 1, f"Expected 1 completion_check, got {len(completion_checks)}"

    # Verify we end with turn_complete (no error or infinite loop).
    turn_errors = [e for e in events if e.get("kind") == "turn_error"]
    assert len(turn_errors) == 0, f"Should have no errors, got {len(turn_errors)}"

    turn_completes = [e for e in events if e.get("kind") == "turn_complete"]
    assert len(turn_completes) == 1, f"Expected 1 turn_complete, got {len(turn_completes)}"
