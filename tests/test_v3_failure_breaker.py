"""Per-tool consecutive-failure circuit breaker for AgentLoopV3.

There is no longer a fixed cap on total tool steps per turn. The only
runaway guard is: if the SAME tool fails to dispatch
``_MAX_CONSECUTIVE_TOOL_FAILURES`` (=5) times in a row, the turn stops; a
successful dispatch of that tool resets its streak.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator

import gemia.agent_loop_v3 as loop_mod
from gemia.agent_loop_v3 import AgentLoopV3, _MAX_CONSECUTIVE_TOOL_FAILURES


class _AlwaysCallsBuild:
    """Fake model that keeps calling ``build`` with empty code (which raises),
    up to a hard safety ceiling so a broken breaker fails loudly instead of
    hanging the test."""

    model = "fake"

    def __init__(self, ceiling: int = 30) -> None:
        self.calls = 0
        self._ceiling = ceiling

    async def stream_turn(
        self, messages: list[dict[str, Any]], *, tools=None, temperature: float = 0.7
    ) -> AsyncIterator[dict[str, Any]]:
        del messages, tools, temperature
        self.calls += 1
        if self.calls > self._ceiling:  # safety net: breaker should fire first
            yield {"kind": "text_delta", "text": "ceiling hit"}
            yield {"kind": "finish", "reason": "stop"}
            return
        yield {"kind": "tool_call_start", "index": 0, "id": f"c{self.calls}", "name": "build"}
        yield {"kind": "tool_call_args_delta", "index": 0, "delta": "{}"}
        yield {"kind": "finish", "reason": "tool_calls"}


def test_breaker_trips_after_five_consecutive_failures(tmp_path: Path) -> None:
    client = _AlwaysCallsBuild()
    events: list[dict[str, Any]] = []
    loop = AgentLoopV3(
        session_id="breaker",
        output_dir=tmp_path,
        gemini_client=client,  # type: ignore[arg-type]
        emit_event=events.append,
    )

    asyncio.run(loop.run_turn("build something broken"))

    # 5 consecutive build failures → breaker trips on the 5th; the model is
    # called exactly 5 times (never reaches the safety ceiling).
    assert client.calls == _MAX_CONSECUTIVE_TOOL_FAILURES == 5
    turn_errors = [e for e in events if e.get("kind") == "turn_error"]
    assert len(turn_errors) == 1
    assert "build" in turn_errors[0]["error"]
    assert "5 times" in turn_errors[0]["error"]
    # Every attempt surfaced an error to the model — none silently dropped.
    assert sum(1 for e in events if e.get("kind") == "tool_exec_error") == 5


class _Flaky:
    """Stateful dispatcher: raises on calls 1-4, succeeds on call 5, raises on
    6-9. A working breaker (with success-reset) never reaches 5-in-a-row."""

    def __init__(self) -> None:
        self.n = 0

    async def __call__(self, args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        self.n += 1
        if self.n == 5:
            return {"ok": True}
        raise RuntimeError(f"flaky failure #{self.n}")


class _CallsFlaky:
    """Fake model: calls ``flaky`` for the first 9 turns, then ends with text."""

    model = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def stream_turn(
        self, messages: list[dict[str, Any]], *, tools=None, temperature: float = 0.7
    ) -> AsyncIterator[dict[str, Any]]:
        del messages, tools, temperature
        self.calls += 1
        if self.calls <= 9:
            yield {"kind": "tool_call_start", "index": 0, "id": f"c{self.calls}", "name": "flaky"}
            yield {"kind": "tool_call_args_delta", "index": 0, "delta": "{}"}
            yield {"kind": "finish", "reason": "tool_calls"}
            return
        yield {"kind": "text_delta", "text": "done"}
        yield {"kind": "finish", "reason": "stop"}


def test_breaker_resets_streak_on_success(tmp_path: Path, monkeypatch) -> None:
    flaky = _Flaky()
    monkeypatch.setitem(loop_mod.DISPATCHER, "flaky", flaky)

    client = _CallsFlaky()
    events: list[dict[str, Any]] = []
    loop = AgentLoopV3(
        session_id="breaker_reset",
        output_dir=tmp_path,
        gemini_client=client,  # type: ignore[arg-type]
        emit_event=events.append,
    )

    asyncio.run(loop.run_turn("exercise flaky"))

    # 4 fails, 1 success (resets streak), 4 fails — never 5 in a row. The turn
    # runs to natural completion (call #10 with no tool calls). Without the
    # reset, the streak would hit 5 on the 6th flaky call and trip early.
    assert client.calls == 10
    assert not [e for e in events if e.get("kind") == "turn_error"]
    assert [e for e in events if e.get("kind") == "turn_complete"]
