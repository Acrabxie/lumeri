from __future__ import annotations

import asyncio
from typing import Any

from gemia.gemini_client import GeminiClientV3, _parse_optional_bool


def _capture_body(
    *,
    provider: str = "openai",
    effort: str = "medium",
    parallel: bool | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    client = object.__new__(GeminiClientV3)
    client.provider = provider
    client.model = "test-model"
    client.reasoning_effort = effort
    client.parallel_tool_calls = parallel
    client.orchestration_temperature = 0.2
    captured: dict[str, Any] = {}

    def fake_stream(body: dict[str, Any]):
        captured.update(body)
        yield {"kind": "finish", "reason": "stop"}

    client._stream_blocking = fake_stream
    client._stream_blocking_claude = fake_stream

    async def consume() -> None:
        async for _ in client.stream_turn(
            [{"role": "user", "content": "hello"}], tools=tools
        ):
            pass

    asyncio.run(consume())
    return captured


def test_reasoning_effort_and_parallel_are_sent_exactly() -> None:
    body = _capture_body(
        effort="max",
        parallel=True,
        tools=[{"type": "function", "function": {"name": "inspect"}}],
    )
    assert body["reasoning"] == {"effort": "high"}
    assert body["parallel_tool_calls"] is True


def test_parallel_false_is_not_lost() -> None:
    body = _capture_body(
        parallel=False,
        tools=[{"type": "function", "function": {"name": "inspect"}}],
    )
    assert body["parallel_tool_calls"] is False


def test_parallel_unset_no_tools_and_claude_are_omitted() -> None:
    assert "parallel_tool_calls" not in _capture_body(parallel=None, tools=[])
    assert "parallel_tool_calls" not in _capture_body(
        provider="claude",
        parallel=True,
        tools=[{"type": "function", "function": {"name": "inspect"}}],
    )


def test_tri_state_parser_rejects_invalid_without_truthiness_bug() -> None:
    assert _parse_optional_bool("true", source="test") is True
    assert _parse_optional_bool("false", source="test") is False
    assert _parse_optional_bool(0, source="test") is False
    assert _parse_optional_bool("maybe", source="test") is None

