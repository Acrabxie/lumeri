from __future__ import annotations

import asyncio
from typing import Any

import gemia.gemini_client as gemini_client
from gemia.gemini_client import (
    GeminiClientV3,
    _claude_request_headers,
    _normalize_anthropic_url,
    _parse_optional_bool,
)


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


def test_anthropic_compatible_endpoint_normalization_and_headers() -> None:
    assert _normalize_anthropic_url("https://anyrouter.top") == "https://anyrouter.top/v1/messages"
    assert _normalize_anthropic_url("https://anyrouter.top/v1") == "https://anyrouter.top/v1/messages"
    assert _normalize_anthropic_url("https://anyrouter.top/v1/messages") == "https://anyrouter.top/v1/messages"

    custom = _claude_request_headers(
        "https://anyrouter.top/v1/messages",
        "secret-value",
        "context-1m-2025-08-07",
    )
    assert custom["Authorization"] == "Bearer secret-value"
    assert custom["x-api-key"] == "secret-value"
    assert custom["anthropic-beta"] == "context-1m-2025-08-07"

    official = _claude_request_headers(
        "https://api.anthropic.com/v1/messages",
        "secret-value",
    )
    assert "Authorization" not in official
    assert "anthropic-beta" not in official


def test_claude_client_resolves_custom_messages_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(gemini_client, "strongest_model_lock", lambda _slot: {"enabled": False})
    monkeypatch.setenv("LUMERI_V3_PROVIDER", "claude")
    monkeypatch.setenv("LUMERI_V3_MODEL", "claude-fable-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("LUMERI_ANTHROPIC_BASE_URL", "https://anyrouter.top")
    monkeypatch.setenv("LUMERI_ANTHROPIC_BETAS", "context-1m-2025-08-07")

    client = GeminiClientV3(proxy="")

    assert client.provider == "claude"
    assert client.model == "claude-fable-5"
    assert client.api_url == "https://anyrouter.top/v1/messages"
    assert client.anthropic_betas == "context-1m-2025-08-07"
