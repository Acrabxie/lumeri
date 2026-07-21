"""Memory-aware starter prompts for the empty Lumeri Video rail.

The browser never receives durable memory itself.  It only polls this module
for four short, privacy-filtered creative suggestions while generation runs in
a daemon thread.  A static fallback is always returned immediately.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
from typing import Any, Callable

from gemia.memory import format_memory_for_prompt


DEFAULT_SUGGESTIONS: tuple[dict[str, str], ...] = (
    {
        "label": "30 秒产品宣传片",
        "prompt": "做一支 30 秒的产品宣传片，冰蓝色调，节奏干净利落",
    },
    {
        "label": "剪一支 15 秒竖版",
        "prompt": "把素材库里的视频剪成 15 秒竖版短片",
    },
    {
        "label": "给成片配中文字幕",
        "prompt": "给当前成片配上中文字幕",
    },
    {
        "label": "挑出最好的镜头",
        "prompt": "从素材里找出最好的三个镜头，拼成一段预览",
    },
)

_SYSTEM_PROMPT = """你是 Lumeri Video 的创作起点推荐器。
根据提供的长期记忆，生成 4 个此刻最值得开始的视频创作或剪辑任务。

要求：
- 只采用与视频创作有关的稳定偏好、视觉风格、内容方向、发布规格或常用工作流；忽略软件开发、系统架构、配置、安全审查和 Agent 运维信息。
- 推荐要体现这些创作信号或正在推进的作品方向，但不要复述记忆。创作信号不足时，生成四个不同阶段的通用高价值视频任务。
- 每条包含 label 和 prompt。label 是 6-14 个中文字符的按钮短句；prompt 是用户点击后可直接发送给视频助手的完整中文指令。
- 四条要覆盖不同意图，例如构思、剪辑、检查、交付；不要只改时长或画幅。
- 不得出现姓名、邮箱、账号、URL、文件路径、设备名、项目私密标识、密钥或任何凭据。
- 不要提到“记忆”“根据你的偏好”或解释推荐原因。
- 只输出 JSON：{"suggestions":[{"label":"…","prompt":"…"}, ...]}。
"""

_PRIVATE_PATTERNS = (
    re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b"),
    re.compile(r"https?://|www\.", re.IGNORECASE),
    re.compile(r"(?:^|\s)(?:/Users/|/Volumes/|~/|[A-Za-z]:\\\\)"),
    re.compile(r"\b(?:sk-|gh[pousr]_|xox[baprs]-)[A-Za-z0-9_-]{8,}", re.IGNORECASE),
    re.compile(r"\b(?:password|api[_-]?key|access[_-]?token|client[_-]?secret)\b", re.IGNORECASE),
)

_cache_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}
_failures: dict[str, int] = {}
_MAX_GENERATION_ATTEMPTS = 2


def _fallback(*, status: str = "ready") -> dict[str, Any]:
    return {
        "status": status,
        "personalized": False,
        "suggestions": [dict(item) for item in DEFAULT_SUGGESTIONS],
    }


def _contains_private_data(text: str) -> bool:
    return any(pattern.search(text) for pattern in _PRIVATE_PATTERNS)


def _extract_json(raw: str) -> Any:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    decoder = json.JSONDecoder()
    for marker in ("{", "["):
        start = text.find(marker)
        if start >= 0:
            try:
                value, _ = decoder.raw_decode(text[start:])
                return value
            except json.JSONDecodeError:
                continue
    raise ValueError("recommendation model did not return JSON")


def normalize_suggestions(raw: str | dict[str, Any] | list[Any]) -> list[dict[str, str]]:
    """Validate model output and return exactly four safe suggestions."""
    payload = _extract_json(raw) if isinstance(raw, str) else raw
    items = payload.get("suggestions") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ValueError("recommendations must be a list")

    suggestions: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        label = re.sub(r"\s+", " ", str(item.get("label") or "")).strip()
        prompt = re.sub(r"\s+", " ", str(item.get("prompt") or "")).strip()
        if not label or not prompt or len(label) > 22 or len(prompt) > 120:
            continue
        if _contains_private_data(label) or _contains_private_data(prompt):
            continue
        key = f"{label}\n{prompt}".casefold()
        if key in seen:
            continue
        seen.add(key)
        suggestions.append({"label": label, "prompt": prompt})

    if len(suggestions) != 4:
        raise ValueError("recommendation model must return exactly four safe suggestions")
    return suggestions


async def _stream_model(memory_text: str) -> str:
    from gemia.gemini_client import GeminiClientV3

    client = GeminiClientV3(timeout=25)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "以下是内部长期记忆。仅抽象出创作方向，不要引用原文：\n\n" + memory_text,
        },
    ]
    chunks: list[str] = []
    async for event in client.stream_turn(messages, temperature=0.35):
        kind = event.get("kind")
        if kind == "text_delta":
            chunks.append(str(event.get("text") or ""))
        elif kind == "error":
            raise RuntimeError(str(event.get("error") or "recommendation generation failed"))
    return "".join(chunks)


def generate_recommendations(memory_text: str) -> list[dict[str, str]]:
    return normalize_suggestions(asyncio.run(_stream_model(memory_text)))


def _finish_generation(
    cache_key: str,
    memory_text: str,
    generator: Callable[[str], list[dict[str, str]]],
) -> None:
    try:
        suggestions = normalize_suggestions(generator(memory_text))
        result = {"status": "ready", "personalized": True, "suggestions": suggestions}
        with _cache_lock:
            _cache[cache_key] = result
            _failures.pop(cache_key, None)
    except Exception:
        with _cache_lock:
            failures = _failures.get(cache_key, 0) + 1
            _failures[cache_key] = failures
            # One malformed/transient model response should not permanently
            # pin this memory version to defaults. The next browser poll gets
            # one clean retry; after that we settle on the safe fallback.
            _cache[cache_key] = _fallback(
                status="retry" if failures < _MAX_GENERATION_ATTEMPTS else "ready"
            )


def get_starter_recommendations(
    *,
    allow_personalized: bool,
    generator: Callable[[str], list[dict[str, str]]] = generate_recommendations,
) -> dict[str, Any]:
    """Return immediately; start one background generation per memory version."""
    if not allow_personalized:
        return _fallback()

    memory_text = format_memory_for_prompt(max_chars=3000).strip()
    if not memory_text or memory_text == "(no durable memory recorded yet)":
        return _fallback()

    cache_key = hashlib.sha256(memory_text.encode("utf-8")).hexdigest()
    should_start = False
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached is not None and cached.get("status") != "retry":
            return {
                **cached,
                "suggestions": [dict(item) for item in cached["suggestions"]],
            }
        _cache[cache_key] = _fallback(status="generating")
        should_start = True

    if should_start:
        threading.Thread(
            target=_finish_generation,
            args=(cache_key, memory_text, generator),
            name="lumeri-starter-recommendations",
            daemon=True,
        ).start()
    return _fallback(status="generating")


def clear_recommendation_cache() -> None:
    """Test seam; the live cache naturally refreshes when memory changes."""
    with _cache_lock:
        _cache.clear()
        _failures.clear()


__all__ = [
    "DEFAULT_SUGGESTIONS",
    "clear_recommendation_cache",
    "generate_recommendations",
    "get_starter_recommendations",
    "normalize_suggestions",
]
