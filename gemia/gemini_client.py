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


_OPEN_ATTEMPTS = 3
_OPEN_TIMEOUT = 20.0  # per attempt; a hung proxy/TLS handshake fails fast, then retries
_RETRY_BACKOFF = 1.2


def _open_with_retry(opener, req, *, timeout=_OPEN_TIMEOUT, attempts=_OPEN_ATTEMPTS, proxy: str | None = None):
    """Open a request, retrying transient transport failures.

    The proxy hop to Google occasionally drops a TLS handshake
    (UNEXPECTED_EOF / handshake timeout). Without this, one blip fails the
    whole turn. HTTP 4xx (other than 429) is surfaced immediately; connection
    errors and 429/5xx are retried with a short backoff.

    Args:
        opener: urllib opener with proxy configuration.
        req: urllib request.
        timeout: per-attempt timeout in seconds.
        attempts: number of retry attempts.
        proxy: optional proxy URL for diagnostic error messages.
    """
    import time
    import errno

    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                last_exc = exc
                time.sleep(_RETRY_BACKOFF * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, ssl.SSLError, TimeoutError, OSError) as exc:
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(_RETRY_BACKOFF * (attempt + 1))
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("unreachable")


_DEFAULT_VERTEX_MODEL = "google/gemini-3.5-flash"
_ADC_PATH = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
_vertex_token_cache: dict[str, Any] = {"access": None, "exp": 0.0}


def _vertex_access_token(proxy: str | None) -> str:
    """Mint/refresh a GCP access token from authorized_user ADC.

    Stdlib only (no google-auth). Caches the token until ~5 min before
    expiry, so a long-running sidecar keeps a valid bearer without holding
    a static credential. Goes through the same proxy as the model calls.
    """
    import time
    from urllib.parse import urlencode

    tok = _vertex_token_cache
    if tok["access"] and time.time() < tok["exp"] - 300:
        return tok["access"]
    adc = json.loads(_ADC_PATH.read_text())
    data = urlencode({
        "grant_type": "refresh_token",
        "client_id": adc["client_id"],
        "client_secret": adc["client_secret"],
        "refresh_token": adc["refresh_token"],
    }).encode("utf-8")
    ctx = ssl.create_default_context(cafile=certifi.where())
    proxy_handler = (
        urllib.request.ProxyHandler({"https": proxy, "http": proxy})
        if proxy else urllib.request.ProxyHandler({})
    )
    opener = urllib.request.build_opener(
        proxy_handler, urllib.request.HTTPSHandler(context=ctx)
    )
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=data, method="POST"
    )
    payload = json.loads(_open_with_retry(opener, req, timeout=30, proxy=proxy).read())
    tok["access"] = payload["access_token"]
    tok["exp"] = time.time() + float(payload.get("expires_in", 3600))
    return tok["access"]


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
        proxy_value = (
            proxy
            if proxy is not None
            else os.environ.get("OPENROUTER_PROXY")
            or _read_config_key("proxy")
            or ""
        ).strip()
        self.proxy = proxy_value or None
        self.timeout = float(timeout)

        self.provider = (
            os.environ.get("LUMERI_V3_PROVIDER")
            or _read_config_key("lumeri_v3_provider")
            or "openrouter"
        ).strip().lower()

        if self.provider == "vertex":
            project = (
                os.environ.get("VERTEX_PROJECT")
                or _read_config_key("vertex_project")
            ).strip()
            if not project:
                raise RuntimeError(
                    "VERTEX_PROJECT is required when LUMERI_V3_PROVIDER=vertex."
                )
            location = (
                os.environ.get("VERTEX_LOCATION")
                or _read_config_key("vertex_location")
                or "global"
            ).strip()
            host = (
                "aiplatform.googleapis.com"
                if location == "global"
                else f"{location}-aiplatform.googleapis.com"
            )
            self.api_url = (
                f"https://{host}/v1beta1/projects/{project}"
                f"/locations/{location}/endpoints/openapi/chat/completions"
            )
            self.model = (
                model
                or os.environ.get("LUMERI_V3_MODEL")
                or _read_config_key("lumeri_v3_model")
                or _DEFAULT_VERTEX_MODEL
            )
            self.api_key = ""  # Vertex uses a per-call minted OAuth bearer
            return

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
        bearer = (
            _vertex_access_token(self.proxy)
            if self.provider == "vertex"
            else self.api_key
        )
        headers = {
            "Authorization": f"Bearer {bearer}",
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
            resp = _open_with_retry(opener, req, timeout=_OPEN_TIMEOUT, proxy=self.proxy)
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="ignore")
            yield {"kind": "error", "error": f"HTTP {exc.code}: {error_body[:600]}"}
            return
        except (urllib.error.URLError, ssl.SSLError, TimeoutError, OSError) as exc:
            # ECONNREFUSED to a localhost proxy (mihomo/clash down or restarting) is
            # the common cause; surface an actionable message instead of a bare
            # URLError. The errno lives on URLError.reason (the wrapped OSError), not
            # exc.__cause__ (which is None here), and ECONNREFUSED is platform-specific
            # (61 macOS/BSD, 111 Linux) — so key off errno.ECONNREFUSED, with a string
            # match as a belt-and-suspenders fallback.
            import errno as _errno

            underlying = getattr(exc, "reason", exc)
            refused = (
                getattr(underlying, "errno", None) == _errno.ECONNREFUSED
                or "Connection refused" in str(exc)
            )
            if self.proxy and refused:
                error_msg = (
                    f"local proxy {self.proxy} refused connection — is the proxy "
                    f"(mihomo/clash) running? ({exc})"
                )
            else:
                error_msg = str(exc)
            yield {
                "kind": "error",
                "error": (
                    f"{type(exc).__name__}: {error_msg} "
                    f"(after {_OPEN_ATTEMPTS} transport attempts)"
                ),
            }
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
                start_event = {
                    "kind": "tool_call_start",
                    "index": idx,
                    "id": str(tc.get("id") or ""),
                    "name": str(name),
                }
                # Vertex/Gemini returns thought_signature here; pass it through
                # so the agent loop can echo it back on the next turn.
                extra = tc.get("extra_content")
                if extra is not None:
                    start_event["extra_content"] = extra
                yield start_event
            if isinstance(args_delta, str) and args_delta:
                yield {"kind": "tool_call_args_delta", "index": idx, "delta": args_delta}
    finish_reason = choice.get("finish_reason")
    if finish_reason:
        yield {"kind": "finish", "reason": str(finish_reason)}


__all__ = ["GeminiClientV3"]
