"""BYOK pluggable search-engine framework for web_search.

All API-provider tests MOCK the HTTP layer — no real network, no real keys.
We monkeypatch:
  - ``_web_search._read_config`` so config lookups (keys / search_provider /
    proxy) are deterministic and never touch the user's real ~/.gemia/config.json.
  - ``_web_search.urllib.request.build_opener`` so ``_build_opener()`` returns a
    fake opener that captures the outgoing ``Request`` and replies with that
    provider's canned JSON. This is the same seam the existing
    tests/test_v4_verbs_functional.py web tests use.

The DuckDuckGo no-key fallback is exercised by feeding canned DDG-Lite HTML
through the same fake opener (mocked, not live).

Each test states clearly that the HTTP layer is mocked.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from gemia.tools._context import AssetRegistry, ToolContext
from gemia.tools import web_search as _web_search


# ── fixtures / fakes ─────────────────────────────────────────────────────────


@pytest.fixture
def tool_context() -> ToolContext:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield ToolContext(
            session_id="test_byok",
            output_dir=Path(tmpdir),
            registry=AssetRegistry(),
            emit_progress=lambda _: None,
        )


class _Headers:
    def get(self, key: str, default: Any = None) -> Any:
        if key == "Content-Type":
            return "application/json; charset=utf-8"
        return default


class _Resp:
    def __init__(self, payload: bytes, content_type: str = "application/json") -> None:
        self._payload = payload
        self.headers = _Headers()
        self._content_type = content_type

    def read(self, *_a: Any, **_k: Any) -> bytes:
        return self._payload

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *_a: Any) -> bool:
        return False


class _CapturingOpener:
    """Captures the outgoing Request; replies with canned bytes.

    Optionally raises (to simulate provider failure for fallback tests). When a
    list of payloads is supplied, successive open() calls consume them in order
    (used for the fallback test: provider call raises, ddg call returns HTML).
    """

    def __init__(
        self,
        payload: bytes | None = None,
        *,
        raise_exc: BaseException | None = None,
    ) -> None:
        self.payload = payload
        self.raise_exc = raise_exc
        self.requests: list[Any] = []

    def open(self, req: Any, timeout: float | None = None) -> _Resp:
        self.requests.append(req)
        if self.raise_exc is not None:
            raise self.raise_exc
        assert self.payload is not None
        return _Resp(self.payload)


def _patch_config(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, str]) -> None:
    """Replace _read_config so only the given keys exist; everything else None."""
    monkeypatch.setattr(_web_search, "_read_config", lambda key: mapping.get(key))


def _patch_opener(monkeypatch: pytest.MonkeyPatch, opener: Any) -> None:
    monkeypatch.setattr(
        _web_search.urllib.request, "build_opener", lambda *a, **k: opener
    )


def _req_body(req: Any) -> dict[str, Any]:
    return json.loads(req.data.decode("utf-8"))


def _req_header(req: Any, name: str) -> str | None:
    # urllib stores headers capitalized (e.g. "X-api-key"); compare case-insensitively.
    for k, v in req.header_items():
        if k.lower() == name.lower():
            return v
    return None


# ============================================================================
# 1. provider resolution (config mocked; no network)
# ============================================================================


class TestResolveProvider:
    def test_config_search_provider_with_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_config(
            monkeypatch,
            {"search_provider": "tavily", "tavily_api_key": "tv-key"},
        )
        provider, creds = _web_search._resolve_provider({})
        assert provider == "tavily"
        assert creds == {"key": "tv-key"}

    def test_auto_detect_only_brave_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # No search_provider configured; only brave key present -> auto picks brave.
        _patch_config(monkeypatch, {"brave_api_key": "br-key"})
        provider, creds = _web_search._resolve_provider({})
        assert provider == "brave"
        assert creds == {"key": "br-key"}

    def test_auto_detect_order_tavily_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Both tavily and bing present; auto order puts tavily first.
        _patch_config(
            monkeypatch,
            {"tavily_api_key": "tv", "bing_api_key": "bg"},
        )
        provider, _ = _web_search._resolve_provider({})
        assert provider == "tavily"

    def test_nothing_configured_falls_to_duckduckgo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, {})
        provider, creds = _web_search._resolve_provider({})
        assert provider == "duckduckgo"
        assert creds == {}

    def test_per_call_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # config says tavily, but per-call provider="serper" wins.
        _patch_config(
            monkeypatch,
            {
                "search_provider": "tavily",
                "tavily_api_key": "tv",
                "serper_api_key": "sp",
            },
        )
        provider, creds = _web_search._resolve_provider({"provider": "serper"})
        assert provider == "serper"
        assert creds == {"key": "sp"}

    def test_per_call_auto_defers_to_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_config(
            monkeypatch, {"search_provider": "brave", "brave_api_key": "br"}
        )
        provider, _ = _web_search._resolve_provider({"provider": "auto"})
        assert provider == "brave"

    def test_google_cse_needs_both_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Only the key without the cx id => not configured for auto-detect.
        _patch_config(monkeypatch, {"google_cse_key": "k"})
        provider, _ = _web_search._resolve_provider({})
        assert provider == "duckduckgo"
        # With both present, google_cse resolves.
        _patch_config(monkeypatch, {"google_cse_key": "k", "google_cse_id": "cx"})
        provider, creds = _web_search._resolve_provider({"provider": "google_cse"})
        assert provider == "google_cse"
        assert creds == {"key": "k", "cx": "cx"}

    def test_unknown_provider_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_config(monkeypatch, {})
        with pytest.raises(ValueError, match="unknown search provider"):
            _web_search._resolve_provider({"provider": "bogus"})


# ============================================================================
# 2. per-provider adapters (HTTP MOCKED): request construction + normalization
# ============================================================================


def _run(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return asyncio.run(_web_search.dispatch(args, ctx))


class TestTavilyAdapter:
    def test_request_and_normalization(
        self, tool_context: ToolContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, {"tavily_api_key": "TV-KEY"})
        canned = json.dumps(
            {
                "results": [
                    {"title": "T1", "url": "https://a.example", "content": "snip-a"},
                    {"title": "T2", "url": "https://b.example", "content": "snip-b"},
                ]
            }
        ).encode("utf-8")
        opener = _CapturingOpener(canned)
        _patch_opener(monkeypatch, opener)

        result = _run({"query": "q", "limit": 5, "provider": "tavily"}, tool_context)

        # response normalized correctly
        assert result["provider"] == "tavily"
        assert result["results"][0] == {
            "title": "T1",
            "url": "https://a.example",
            "snippet": "snip-a",
        }
        # request built right: POST to tavily /search with api_key+query+max_results
        req = opener.requests[0]
        assert req.full_url == "https://api.tavily.com/search"
        assert req.get_method() == "POST"
        assert _req_body(req) == {"api_key": "TV-KEY", "query": "q", "max_results": 5}


class TestBraveAdapter:
    def test_request_and_normalization(
        self, tool_context: ToolContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, {"brave_api_key": "BR-KEY"})
        canned = json.dumps(
            {
                "web": {
                    "results": [
                        {
                            "title": "B1",
                            "url": "https://brave.example/1",
                            "description": "desc-1",
                        }
                    ]
                }
            }
        ).encode("utf-8")
        opener = _CapturingOpener(canned)
        _patch_opener(monkeypatch, opener)

        result = _run({"query": "cats", "limit": 3, "provider": "brave"}, tool_context)

        assert result["provider"] == "brave"
        assert result["results"][0] == {
            "title": "B1",
            "url": "https://brave.example/1",
            "snippet": "desc-1",
        }
        req = opener.requests[0]
        assert req.get_method() == "GET"
        assert req.full_url.startswith(
            "https://api.search.brave.com/res/v1/web/search?"
        )
        assert "q=cats" in req.full_url and "count=3" in req.full_url
        assert _req_header(req, "X-Subscription-Token") == "BR-KEY"
        assert _req_header(req, "Accept") == "application/json"


class TestSerperAdapter:
    def test_request_and_normalization(
        self, tool_context: ToolContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, {"serper_api_key": "SP-KEY"})
        canned = json.dumps(
            {
                "organic": [
                    {"title": "S1", "link": "https://s.example/1", "snippet": "ss-1"}
                ]
            }
        ).encode("utf-8")
        opener = _CapturingOpener(canned)
        _patch_opener(monkeypatch, opener)

        result = _run({"query": "dogs", "limit": 4, "provider": "serper"}, tool_context)

        assert result["provider"] == "serper"
        assert result["results"][0] == {
            "title": "S1",
            "url": "https://s.example/1",
            "snippet": "ss-1",
        }
        req = opener.requests[0]
        assert req.full_url == "https://google.serper.dev/search"
        assert req.get_method() == "POST"
        assert _req_header(req, "X-API-KEY") == "SP-KEY"
        assert _req_header(req, "Content-Type") == "application/json"
        assert _req_body(req) == {"q": "dogs", "num": 4}


class TestGoogleCseAdapter:
    def test_request_and_normalization(
        self, tool_context: ToolContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(
            monkeypatch, {"google_cse_key": "G-KEY", "google_cse_id": "CX-ID"}
        )
        canned = json.dumps(
            {
                "items": [
                    {"title": "G1", "link": "https://g.example/1", "snippet": "gs-1"}
                ]
            }
        ).encode("utf-8")
        opener = _CapturingOpener(canned)
        _patch_opener(monkeypatch, opener)

        # limit 99 should clamp num to max 10 in the request.
        result = _run(
            {"query": "birds", "limit": 99, "provider": "google_cse"}, tool_context
        )

        assert result["provider"] == "google_cse"
        assert result["results"][0] == {
            "title": "G1",
            "url": "https://g.example/1",
            "snippet": "gs-1",
        }
        req = opener.requests[0]
        assert req.full_url.startswith("https://www.googleapis.com/customsearch/v1?")
        assert req.get_method() == "GET"
        assert "key=G-KEY" in req.full_url
        assert "cx=CX-ID" in req.full_url
        assert "q=birds" in req.full_url
        assert "num=10" in req.full_url  # clamped from 99


class TestBingAdapter:
    def test_request_and_normalization(
        self, tool_context: ToolContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, {"bing_api_key": "BG-KEY"})
        canned = json.dumps(
            {
                "webPages": {
                    "value": [
                        {
                            "name": "Bing1",
                            "url": "https://bing.example/1",
                            "snippet": "bs-1",
                        }
                    ]
                }
            }
        ).encode("utf-8")
        opener = _CapturingOpener(canned)
        _patch_opener(monkeypatch, opener)

        result = _run({"query": "fish", "limit": 6, "provider": "bing"}, tool_context)

        assert result["provider"] == "bing"
        assert result["results"][0] == {
            "title": "Bing1",
            "url": "https://bing.example/1",
            "snippet": "bs-1",
        }
        req = opener.requests[0]
        assert req.full_url.startswith("https://api.bing.microsoft.com/v7.0/search?")
        assert req.get_method() == "GET"
        assert "q=fish" in req.full_url and "count=6" in req.full_url
        assert _req_header(req, "Ocp-Apim-Subscription-Key") == "BG-KEY"


class TestExaAdapter:
    def test_request_and_normalization(
        self, tool_context: ToolContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, {"exa_api_key": "EX-KEY"})
        # text present -> used as snippet; second item only has snippet field.
        canned = json.dumps(
            {
                "results": [
                    {"title": "E1", "url": "https://e.example/1", "text": "ex-text"},
                    {"title": "E2", "url": "https://e.example/2", "snippet": "ex-snip"},
                    {"title": "E3", "url": "https://e.example/3"},
                ]
            }
        ).encode("utf-8")
        opener = _CapturingOpener(canned)
        _patch_opener(monkeypatch, opener)

        result = _run({"query": "stars", "limit": 9, "provider": "exa"}, tool_context)

        assert result["provider"] == "exa"
        assert result["results"][0]["snippet"] == "ex-text"
        assert result["results"][1]["snippet"] == "ex-snip"
        assert result["results"][2]["snippet"] == ""  # missing -> ""
        req = opener.requests[0]
        assert req.full_url == "https://api.exa.ai/search"
        assert req.get_method() == "POST"
        assert _req_header(req, "x-api-key") == "EX-KEY"
        assert _req_body(req) == {"query": "stars", "numResults": 9}


class TestMissingFieldsRobustness:
    def test_missing_fields_become_empty_strings(
        self, tool_context: ToolContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, {"tavily_api_key": "k"})
        canned = json.dumps({"results": [{"url": "https://x.example"}]}).encode("utf-8")
        _patch_opener(monkeypatch, _CapturingOpener(canned))
        result = _run({"query": "q", "provider": "tavily"}, tool_context)
        assert result["results"][0] == {
            "title": "",
            "url": "https://x.example",
            "snippet": "",
        }


# ============================================================================
# 3. fallback: chosen BYOK provider raises -> duckduckgo serves (HTTP MOCKED)
# ============================================================================


_DDG_HTML = b"""
<html><body>
  <a class="result__a" href="https://ddg.example/page">DDG Result</a>
  <div class="result__snippet">From the fallback scraper.</div>
</body></html>
"""


class _FallbackOpener:
    """First open() (provider) raises; subsequent open() (ddg) returns HTML."""

    def __init__(self, ddg_html: bytes) -> None:
        self.ddg_html = ddg_html
        self.calls = 0
        self.requests: list[Any] = []

    def open(self, req: Any, timeout: float | None = None) -> _Resp:
        self.requests.append(req)
        self.calls += 1
        if self.calls == 1:
            raise _web_search.urllib.error.URLError("simulated provider outage")
        return _Resp(self.ddg_html, content_type="text/html")


class TestFallback:
    def test_byok_failure_falls_back_to_duckduckgo(
        self, tool_context: ToolContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, {"tavily_api_key": "k"})
        opener = _FallbackOpener(_DDG_HTML)
        _patch_opener(monkeypatch, opener)

        result = _run({"query": "outage", "provider": "tavily"}, tool_context)

        assert result["provider"] == "duckduckgo"
        assert result["engine"] == "duckduckgo_lite"
        assert "fallback" in result
        assert "tavily" in result["fallback"]
        assert result["results"][0]["url"] == "https://ddg.example/page"
        assert result["results"][0]["title"] == "DDG Result"
        # both the failed provider call and the ddg call were issued
        assert opener.calls == 2


# ============================================================================
# 4. existing no-key path still uses duckduckgo (HTTP MOCKED)
# ============================================================================


class TestDefaultDuckDuckGoPath:
    def test_no_keys_uses_duckduckgo(
        self, tool_context: ToolContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, {})  # no BYOK keys, no search_provider
        opener = _CapturingOpener(_DDG_HTML)
        # ddg returns html
        monkeypatch.setattr(
            _web_search.urllib.request,
            "build_opener",
            lambda *a, **k: opener,
        )
        # the canned opener returns application/json content-type by default but
        # the DDG path ignores content-type and parses bytes as HTML, so reuse it
        # by swapping the payload to HTML.
        opener.payload = _DDG_HTML

        result = _run({"query": "nokey"}, tool_context)

        assert result["provider"] == "duckduckgo"
        assert result["engine"] == "duckduckgo_lite"
        assert "fallback" not in result
        assert result["results"][0]["url"] == "https://ddg.example/page"
        # request went to DuckDuckGo Lite
        assert opener.requests[0].full_url.startswith("https://lite.duckduckgo.com/lite/")
