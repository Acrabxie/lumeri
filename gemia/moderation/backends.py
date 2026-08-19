"""Detectors that judge generated media, and how one is chosen.

Deliberately not routed through :class:`~gemia.ai.gemini_adapter.GeminiAdapter`.
That adapter is built for the conversation loop — model failover, plan JSON,
transcript logging — and its media path accepts video only. A moderation call is
the opposite shape: one file, one question, a verdict that must fail closed. A
thin self-contained client keeps those failure modes separate, so a provider
outage in the creative path cannot quietly change what gets screened, and the
detector can be swapped for a hosted safety API without touching the loop.

Selection is by ``GEMIA_OUTPUT_MODERATION_BACKEND``. An unset value yields no
detector, which :func:`gemia.moderation.guard_tool_output` turns into either a
refusal or an explicit *unscreened* ledger line depending on
``GEMIA_OUTPUT_MODERATION_REQUIRED``. An *unrecognised* value raises instead of
falling back to "no detector": a typo in a deployment's configuration must not
be the thing that silently disables screening.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from gemia.moderation.output import (
    OutputBackend,
    OutputModerationUnavailable,
    OutputVerdict,
)
from gemia.moderation.policy import Category

_BACKEND_ENV = "GEMIA_OUTPUT_MODERATION_BACKEND"
_MODEL_ENV = "GEMIA_OUTPUT_MODERATION_MODEL"
_DEFAULT_MODEL = "gemini-3.1-flash"

# Gemini's inline payload ceiling in practice. Larger videos are sampled into
# frames rather than refused: a long render is the normal case, not an attack.
_INLINE_MAX_BYTES = 18 * 1024 * 1024
_SAMPLE_FRAMES = 3

_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}

# The categories a pixel detector is asked about, and the wording of the ask.
# Two things are load-bearing here. Serious subject matter is not the same as
# prohibited content — a war documentary, a history lesson, a medical
# illustration all contain imagery that a naive classifier flags, and refusing
# them is a product failure, not a safety win. And the child-safety category is
# not weighed against artistic intent at all: it is the one category where an
# uncertain answer must resolve to a refusal.
_SYSTEM_PROMPT = """You screen media a video-editing platform just generated, \
before it is shown to the person who asked for it.

Answer ONLY with a JSON object, no prose, no code fence:
{"verdict": "allow" | "block", "category": "<one of sexual, violence, minors, \
csam, or empty>", "confidence": <0.0-1.0>, "note": "<short reason if blocked>"}

Block when the media depicts:
- sexual: explicit sexual activity or pornographic nudity
- violence: graphic gore, or realistic depiction of severe injury presented for \
its own sake
- minors: a minor in a sexualised context, or otherwise unsafe depiction of a child
- csam: any sexual depiction involving a minor

Do NOT block for:
- serious subject matter treated seriously (war, history, illness, injustice, \
grief, protest, medical or educational imagery)
- artistic nudity that is not pornographic
- stylised, cartoon, or game violence
- anything you are merely unsure about in the sexual or violence categories

For the minors and csam categories only, an uncertain judgement must resolve to \
"block"."""


def _mime_for(path: Path) -> str:
    mime = _MIME_BY_SUFFIX.get(path.suffix.lower())
    if not mime:
        raise OutputModerationUnavailable(f"unsupported media type: {path.suffix}")
    return mime


def _sample_frames(path: Path, count: int = _SAMPLE_FRAMES) -> list[tuple[str, bytes]]:
    """Pull a few evenly spaced frames out of a video that is too large to inline."""
    import subprocess
    import tempfile

    from gemia.tools._ffmpeg import ffprobe_duration

    try:
        duration = float(ffprobe_duration(path) or 0.0)
    except Exception as exc:  # ffprobe missing or unreadable container
        raise OutputModerationUnavailable(f"cannot probe video for sampling: {exc}") from exc
    if duration <= 0:
        raise OutputModerationUnavailable("video reports no duration")

    frames: list[tuple[str, bytes]] = []
    with tempfile.TemporaryDirectory(prefix="lumeri-mod-") as tmp:
        for index in range(count):
            at = duration * (index + 0.5) / count
            out = Path(tmp) / f"frame{index}.jpg"
            proc = subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", f"{at:.3f}", "-i", str(path),
                 "-frames:v", "1", "-q:v", "3", "-y", str(out)],
                capture_output=True,
            )
            if proc.returncode == 0 and out.exists():
                frames.append(("image/jpeg", out.read_bytes()))
    if not frames:
        raise OutputModerationUnavailable("could not sample any frame from video")
    return frames


def _media_parts(kind: str, path: Path) -> list[dict[str, Any]]:
    """Build the inline parts for one asset, sampling video that is too large."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise OutputModerationUnavailable(f"cannot read generated asset: {exc}") from exc

    if size <= _INLINE_MAX_BYTES:
        payloads = [(_mime_for(path), path.read_bytes())]
    elif kind == "video":
        payloads = _sample_frames(path)
    else:
        raise OutputModerationUnavailable(f"image too large to screen: {size} bytes")

    return [
        {"inline_data": {"mime_type": mime, "data": base64.b64encode(raw).decode("ascii")}}
        for mime, raw in payloads
    ]


def _parse_verdict(text: str, *, detector: str) -> OutputVerdict:
    """Turn the model's JSON into a verdict, failing closed on anything unclear."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1] if "```" in cleaned[3:] else cleaned[3:]
        cleaned = cleaned.removeprefix("json").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise OutputModerationUnavailable(f"detector returned no JSON: {text[:200]!r}")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise OutputModerationUnavailable(f"detector JSON unparseable: {exc}") from exc

    raw_verdict = str(data.get("verdict", "")).strip().lower()
    if raw_verdict not in {"allow", "block"}:
        # An answer that is neither is not an implicit pass.
        raise OutputModerationUnavailable(f"detector gave no usable verdict: {raw_verdict!r}")
    if raw_verdict == "allow":
        return OutputVerdict(allowed=True, detector=detector)

    raw_category = str(data.get("category", "")).strip().lower()
    # The prompt asks for csam separately so the model states the more serious
    # finding plainly, but the published AUP has one child-safety category and
    # the ledger keys its no-fingerprint rule on that name.
    if raw_category == "csam":
        raw_category = Category.MINORS.value
    category = next((c for c in Category if c.value == raw_category), None)
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return OutputVerdict(
        allowed=False,
        category=category,
        # Deliberately does not repeat the detector's description of what it
        # saw: the person asked for something and is being refused, and reciting
        # the refused imagery back at them serves no one.
        reason=(
            "This result was not shown because it does not meet the content "
            "policy. You can try a different direction."
        ),
        detector=detector,
        scores={raw_category or "unspecified": confidence} if confidence else {},
    )


class GeminiOutputBackend:
    """Judges generated media with a single Gemini ``generateContent`` call."""

    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "").strip()
        self._model = model or os.environ.get(_MODEL_ENV, "").strip() or _DEFAULT_MODEL

    async def inspect(self, kind: str, path: Path) -> OutputVerdict:
        if not self._api_key:
            raise OutputModerationUnavailable("GEMINI_API_KEY is not set")
        parts = _media_parts(kind, path)
        body = {
            "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [*parts, {"text": "Screen this."}]}],
            "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"},
        }
        text = await asyncio.get_running_loop().run_in_executor(None, self._post, body)
        return _parse_verdict(text, detector=f"{self.name}:{self._model}")

    def _post(self, body: dict[str, Any]) -> str:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self._model}:generateContent"
        )
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": self._api_key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise OutputModerationUnavailable(f"detector request failed: {exc}") from exc

        candidates = payload.get("candidates") or []
        if not candidates:
            # A provider-side safety stop leaves no candidate. That is a signal
            # about the media, but not one this code should read as a verdict —
            # it fails closed like any other unanswered call.
            raise OutputModerationUnavailable(f"detector returned no candidate: {payload}")
        for part in candidates[0].get("content", {}).get("parts", []):
            if isinstance(part.get("text"), str) and part["text"].strip():
                return part["text"]
        raise OutputModerationUnavailable("detector returned no text part")


def resolve_backend() -> OutputBackend | None:
    """Pick the configured detector.

    Returns:
        A backend, or ``None`` when none is configured.

    Raises:
        OutputModerationUnavailable: When the configured name is unknown — a
            misconfiguration must fail closed rather than read as "off".
    """
    name = os.environ.get(_BACKEND_ENV, "").strip().lower()
    if not name or name == "none":
        return None
    if name == "gemini":
        return GeminiOutputBackend()
    raise OutputModerationUnavailable(f"unknown moderation backend: {name!r}")


__all__ = ["GeminiOutputBackend", "resolve_backend"]
