"""Per-tool consecutive-failure circuit breaker for AgentLoopV3.

There is no longer a fixed cap on total tool steps per turn. The only
runaway guard is: if the SAME tool fails to dispatch
``_MAX_CONSECUTIVE_TOOL_FAILURES`` (=5) times in a row, the turn stops; a
successful dispatch of that tool resets its streak.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator

import gemia.agent_loop_v3 as loop_mod
from gemia.agent_loop_v3 import AgentLoopV3, _MAX_CONSECUTIVE_TOOL_FAILURES
from gemia.errors import ToolError


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
    # With RC4 completion gate, one extra model call happens after call #10
    # when the model stops, so we expect 11 total.
    assert client.calls == 11
    assert not [e for e in events if e.get("kind") == "turn_error"]
    assert [e for e in events if e.get("kind") == "turn_complete"]


class _RaisesToolError:
    """Dispatcher that raises a typed ToolError with rich, actionable fields —
    the fuel the model needs to self-correct precisely."""

    async def __call__(self, args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        raise ToolError(
            "'black and white' is not an available look.",
            code="E_UNSUPPORTED",
            recovery="fix_args",
            valid_options=["warm", "cool", "neutral"],
            hint="Pick a named look.",
        )


class _CallsToolThenStops:
    """Fake model: calls ``tool_name`` ``call_times`` times (reacting to each
    failure), then ends with text — the model explaining / moving on."""

    model = "fake"

    def __init__(self, tool_name: str, call_times: int) -> None:
        self.calls = 0
        self._tool = tool_name
        self._call_times = call_times

    async def stream_turn(
        self, messages: list[dict[str, Any]], *, tools=None, temperature: float = 0.7
    ) -> AsyncIterator[dict[str, Any]]:
        del messages, tools, temperature
        self.calls += 1
        if self.calls <= self._call_times:
            yield {"kind": "tool_call_start", "index": 0, "id": f"c{self.calls}", "name": self._tool}
            yield {"kind": "tool_call_args_delta", "index": 0, "delta": "{}"}
            yield {"kind": "finish", "reason": "tool_calls"}
            return
        yield {"kind": "text_delta", "text": "no such look — telling the user."}
        yield {"kind": "finish", "reason": "stop"}


def test_tool_error_surfaces_structured_fields(tmp_path: Path, monkeypatch) -> None:
    """A raised ToolError must reach BOTH the SSE stream and the model with its
    structure intact — not flattened to a bare string."""
    monkeypatch.setitem(loop_mod.DISPATCHER, "demo_tool", _RaisesToolError())
    client = _CallsToolThenStops("demo_tool", call_times=1)
    events: list[dict[str, Any]] = []
    loop = AgentLoopV3(
        session_id="typed_err",
        output_dir=tmp_path,
        gemini_client=client,  # type: ignore[arg-type]
        emit_event=events.append,
    )

    asyncio.run(loop.run_turn("make it black and white"))

    # (a) the SSE tool_exec_error event carries the typed fields.
    errs = [e for e in events if e.get("kind") == "tool_exec_error"]
    assert len(errs) == 1
    ev = errs[0]
    assert ev["error_code"] == "E_UNSUPPORTED"
    assert ev["recovery"] == "fix_args"
    assert ev["valid_options"] == ["warm", "cool", "neutral"]
    assert ev["hint"]

    # (b) the model-facing tool_result message carries the same structure.
    tool_msgs = [m for m in loop._messages if m.get("role") == "tool"]
    assert tool_msgs, "expected a tool_result fed back to the model"
    payload = json.loads(tool_msgs[-1]["content"])
    assert payload["error_code"] == "E_UNSUPPORTED"
    assert payload["recovery"] == "fix_args"
    assert payload["valid_options"] == ["warm", "cool", "neutral"]


class _RaisesAlternatingCodes:
    """Always raises, but alternates error_code each call. A model 'adapting'
    (different failure class) must not look like runaway."""

    _codes = ["E_BAD_ARG", "E_UNSUPPORTED"]

    def __init__(self) -> None:
        self.n = 0

    async def __call__(self, args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        code = self._codes[self.n % len(self._codes)]
        self.n += 1
        raise ToolError(f"failure #{self.n}", code=code, recovery="fix_args")


def test_breaker_soft_resets_when_error_code_changes(tmp_path: Path, monkeypatch) -> None:
    """Strictly alternating error codes never form a 5-long same-(tool,code)
    streak, so the breaker does not trip even across 9 failures."""
    monkeypatch.setitem(loop_mod.DISPATCHER, "adapt_tool", _RaisesAlternatingCodes())
    client = _CallsToolThenStops("adapt_tool", call_times=9)
    events: list[dict[str, Any]] = []
    loop = AgentLoopV3(
        session_id="soft_reset",
        output_dir=tmp_path,
        gemini_client=client,  # type: ignore[arg-type]
        emit_event=events.append,
    )

    asyncio.run(loop.run_turn("keep adapting"))

    # 9 tool turns + 1 closing text turn + 1 RC4 completion gate nudge = 11
    assert client.calls == 11
    assert not [e for e in events if e.get("kind") == "turn_error"]
    assert [e for e in events if e.get("kind") == "turn_complete"]
    assert sum(1 for e in events if e.get("kind") == "tool_exec_error") == 9
