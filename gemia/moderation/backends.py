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
# Vertex serves the 3.x text/multimodal models on the global endpoint under
# an OpenAI-style name; the direct API uses the bare identifier.
_DEFAULT_VERTEX_MODEL = "google/gemini-3.5-flash"
_DEFAULT_API_MODEL = "gemini-3.1-flash"

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


def _media_payloads(kind: str, path: Path) -> list[tuple[str, bytes]]:
    """The bytes to screen for one asset, as (mime, data) pairs.

    Video is always sampled into frames rather than sent whole. The transport
    below is Vertex's OpenAI-compatible endpoint, which takes images; sampling
    also keeps a long render from turning one screening call into a multi-hundred
    megabyte upload. Three frames is a floor, not a thorough reading of the cut —
    it catches what a still would catch, which is what the visual categories are.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise OutputModerationUnavailable(f"cannot read generated asset: {exc}") from exc

    if kind == "video":
        return _sample_frames(path)
    if size > _INLINE_MAX_BYTES:
        raise OutputModerationUnavailable(f"image too large to screen: {size} bytes")
    return [(_mime_for(path), path.read_bytes())]


def _data_uri(mime: str, raw: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


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
    """Judges generated media with one Gemini call.

    Two transports, picked by what the deployment actually has. Vertex is first
    because it is what this platform runs on: media generation is configured
    through ``vertex_project`` and a GCP ADC refresh token, and no static Gemini
    API key exists anywhere in the config. A detector that demanded one would be
    permanently unavailable in production — which, with screening required, means
    refusing every image and video rather than screening them.
    """

    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY", "").strip()
        self._model = model or os.environ.get(_MODEL_ENV, "").strip()

    # -- transport selection -------------------------------------------------

    def _vertex_target(self) -> tuple[str, str, str] | None:
        """(url, model, proxy) for the Vertex route, or None when unconfigured."""
        from gemia.gemini_client import _read_config_key  # local: heavy module

        project = (os.environ.get("VERTEX_PROJECT") or _read_config_key("vertex_project") or "").strip()
        if not project:
            return None
        location = (
            os.environ.get("LUMERI_V3_LOCATION")
            or _read_config_key("lumeri_v3_location")
            or os.environ.get("VERTEX_LOCATION")
            or _read_config_key("vertex_location")
            or "global"
        ).strip()
        host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
        url = (
            f"https://{host}/v1beta1/projects/{project}"
            f"/locations/{location}/endpoints/openapi/chat/completions"
        )
        proxy = (os.environ.get("OPENROUTER_PROXY") or _read_config_key("proxy") or "").strip()
        return url, self._model or _DEFAULT_VERTEX_MODEL, proxy or ""

    async def inspect(self, kind: str, path: Path) -> OutputVerdict:
        payloads = _media_payloads(kind, path)
        vertex = self._vertex_target()
        if vertex is not None:
            url, model, proxy = vertex
            text = await asyncio.get_running_loop().run_in_executor(
                None, self._post_vertex, url, model, proxy, payloads
            )
            return _parse_verdict(text, detector=f"{self.name}:vertex:{model}")
        if self._api_key:
            model = self._model or _DEFAULT_API_MODEL
            text = await asyncio.get_running_loop().run_in_executor(
                None, self._post_api_key, model, payloads
            )
            return _parse_verdict(text, detector=f"{self.name}:{model}")
        raise OutputModerationUnavailable(
            "no detector credentials: set vertex_project (with GCP ADC) or GEMINI_API_KEY"
        )

    # -- transports ----------------------------------------------------------

    def _post_vertex(self, url: str, model: str, proxy: str, payloads: list[tuple[str, bytes]]) -> str:
        from gemia.gemini_client import _vertex_access_token  # local: heavy module

        content: list[dict[str, Any]] = [
            {"type": "image_url", "image_url": {"url": _data_uri(mime, raw)}}
            for mime, raw in payloads
        ]
        content.append({"type": "text", "text": "Screen this."})
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "temperature": 0,
        }
        try:
            token = _vertex_access_token(proxy or None)
        except Exception as exc:  # ADC missing, refresh refused, network down
            raise OutputModerationUnavailable(f"cannot mint Vertex token: {exc}") from exc

        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            method="POST",
        )
        opener = (
            urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy, "http": proxy}))
            if proxy
            else urllib.request.build_opener(urllib.request.ProxyHandler({}))
        )
        try:
            with opener.open(request, timeout=90) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise OutputModerationUnavailable(f"detector request failed: {exc}") from exc

        choices = payload.get("choices") or []
        if not choices:
            raise OutputModerationUnavailable(f"detector returned no choice: {str(payload)[:200]}")
        text = (choices[0].get("message") or {}).get("content")
        if not isinstance(text, str) or not text.strip():
            raise OutputModerationUnavailable("detector returned empty content")
        return text

    def _post_api_key(self, model: str, payloads: list[tuple[str, bytes]]) -> str:
        parts: list[dict[str, Any]] = [
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(raw).decode("ascii")}}
            for mime, raw in payloads
        ]
        parts.append({"text": "Screen this."})
        body = {
            "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"},
        }
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        )
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": self._api_key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise OutputModerationUnavailable(f"detector request failed: {exc}") from exc

        candidates = payload.get("candidates") or []
        if not candidates:
            # A provider-side safety stop leaves no candidate. That is a signal
            # about the media, but not one this code should read as a verdict —
            # it fails closed like any other unanswered call.
            raise OutputModerationUnavailable(f"detector returned no candidate: {str(payload)[:200]}")
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
