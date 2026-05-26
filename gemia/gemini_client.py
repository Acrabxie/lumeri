"""OpenRouter streaming client for Lumeri v3.

Real SSE streaming, not request-then-parse. Yields delta events as Gemini
emits them, so the agent loop can forward ``model_text_delta`` to the
SSE transport immediately.

Handles OpenAI-compatible function calling on OpenRouter. Tool call args
arrive fragmented across chunks; the agent loop reassembles them.

Reads credentials from env or ``~/.gemia/config.json`` (same pattern as
the existing GeminiAdapter, so no new config surface).
"""
from __future__ import annotations

import asyncio
import json
import os
import ssl
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

import certifi


_DEFAULT_URL = "https://openrouter.ai/api/v1/chat/completions"
_DEFAULT_MODEL = "google/gemini-3.1-pro-preview"


def _read_config_key(field: str) -> str:
    try:
        path = Path.home() / ".gemia" / "config.json"
        if path.exists():
            data = json.loads(path.read_text())
            return data.get(field, "") or ""
    except Exception:
        return ""
    return ""


class GeminiClientV3:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        api_url: str = _DEFAULT_URL,
        proxy: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        resolved_key = (
            api_key
            or os.environ.get("OPENROUTER_API_KEY")
            or _read_config_key("openrouter_api_key")
        ).strip()
        if not resolved_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is required for Lumeri v3 "
                "(env or ~/.gemia/config.json:openrouter_api_key)."
            )
        self.api_key = resolved_key
        self.model = (
            model
            or os.environ.get("LUMERI_V3_MODEL")
            or _read_config_key("lumeri_v3_model")
            or _read_config_key("openrouter_model")
            or _DEFAULT_MODEL
        )
        self.api_url = api_url
        proxy_value = (
            proxy
            if proxy is not None
            else os.environ.get("OPENROUTER_PROXY")
            or _read_config_key("proxy")
            or ""
        ).strip()
        self.proxy = proxy_value or None
        self.timeout = float(timeout)

    async def stream_turn(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream one turn. Yields delta dicts:

        - ``{"kind": "text_delta", "text": str}``
        - ``{"kind": "tool_call_start", "index": int, "id": str, "name": str}``
        - ``{"kind": "tool_call_args_delta", "index": int, "delta": str}``
        - ``{"kind": "finish", "reason": str}``
        - ``{"kind": "error", "error": str}``
        """
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools

        loop = asyncio.get_running_loop()
        q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        def producer() -> None:
            try:
                for ev in self._stream_blocking(body):
                    loop.call_soon_threadsafe(q.put_nowait, ev)
            except Exception as exc:
                loop.call_soon_threadsafe(
                    q.put_nowait,
                    {"kind": "error", "error": f"{type(exc).__name__}: {exc}"},
                )
            finally:
                loop.call_soon_threadsafe(q.put_nowait, None)

        threading.Thread(target=producer, daemon=True).start()

        while True:
            item = await q.get()
            if item is None:
                return
            yield item

    def _stream_blocking(self, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "HTTP-Referer": "https://local-lumeri-desktop",
            "X-Title": "lumeri-v3",
        }
        req = urllib.request.Request(
            self.api_url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        https_handler = urllib.request.HTTPSHandler(context=ssl_context)
        if self.proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"https": self.proxy, "http": self.proxy}),
                https_handler,
            )
        else:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                https_handler,
            )

        try:
            resp = opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="ignore")
            yield {"kind": "error", "error": f"HTTP {exc.code}: {error_body[:600]}"}
            return

        with resp:
            while True:
                line = resp.readline()
                if not line:
                    break
                line = line.rstrip(b"\r\n")
                if not line:
                    continue
                if line.startswith(b":"):
                    continue
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].lstrip()
                if payload == b"[DONE]":
                    return
                try:
                    chunk = json.loads(payload.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                for event in _parse_chunk(chunk):
                    yield event


def _parse_chunk(chunk: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Parse one OpenAI-compatible streaming chunk into delta events."""
    choices = chunk.get("choices") or []
    if not choices:
        return
    choice = choices[0]
    delta = choice.get("delta") or {}
    content = delta.get("content")
    if isinstance(content, str) and content:
        yield {"kind": "text_delta", "text": content}
    tool_calls = delta.get("tool_calls")
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            idx = int(tc.get("index", 0))
            func = tc.get("function") or {}
            name = func.get("name")
            args_delta = func.get("arguments")
            if name:
                yield {
                    "kind": "tool_call_start",
                    "index": idx,
                    "id": str(tc.get("id") or ""),
                    "name": str(name),
                }
            if isinstance(args_delta, str) and args_delta:
                yield {"kind": "tool_call_args_delta", "index": idx, "delta": args_delta}
    finish_reason = choice.get("finish_reason")
    if finish_reason:
        yield {"kind": "finish", "reason": str(finish_reason)}


__all__ = ["GeminiClientV3"]
