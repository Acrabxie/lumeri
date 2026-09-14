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
import random
import time
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
# A verdict blocks a generation the user is waiting on, so the whole attempt
# chain is capped rather than each socket: one 90s timeout used to cost more
# wall clock than every retry here combined.
_ATTEMPT_BUDGET_SECONDS = 30.0
_MAX_ATTEMPTS = 3
# Measured: a healthy verdict returns in about 2.3s, the slowest under 4s. A
# call still open at 8s is stuck rather than slow, and waiting longer only eats
# the budget a second attempt needs — at 8s three full attempts fit inside 30s,
# at 15s only two do.
_SINGLE_ATTEMPT_TIMEOUT = 8.0
# Only what a retry can actually fix. A 400 or a 403 means the request itself is
# wrong, and repeating it spends quota the next screening needs.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_RETRY_AFTER_CAP_SECONDS = 10.0


def _retry_delay(attempt: int, error: urllib.error.HTTPError | None) -> float:
    """How long to wait before attempt ``attempt + 1``.

    The detector runs on a fixed quota, so a retry is never free — it spends
    what the next generation needs. Backoff grows exponentially and carries
    jitter so that several screenings rejected in the same second do not
    resynchronise into a second burst against the same quota. A server-sent
    ``Retry-After`` always wins over the local schedule, because it is the only
    party that knows when capacity returns.
    """
    if error is not None and error.headers is not None:
        header = error.headers.get("Retry-After")
        if header:
            try:
                return max(0.0, min(float(header), _RETRY_AFTER_CAP_SECONDS))
            except (TypeError, ValueError):
                pass
    base = 0.5 * (2**attempt)
    return base + random.uniform(0.0, base * 0.5)


def _post_json(opener: urllib.request.OpenerDirector, request: urllib.request.Request) -> dict:
    """POST and decode JSON, retrying only transient refusals.

    Raises:
        OutputModerationUnavailable: When no attempt produced a response. Never
            returns a partial or guessed result — an unanswered screening has to
            reach the caller as "unavailable" so that required mode can refuse
            rather than let the asset through.
    """
    deadline = time.monotonic() + _ATTEMPT_BUDGET_SECONDS
    last: BaseException | None = None
    attempts = 0

    for attempt in range(_MAX_ATTEMPTS):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        attempts = attempt + 1
        try:
            with opener.open(request, timeout=min(remaining, _SINGLE_ATTEMPT_TIMEOUT)) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # a subclass of URLError — catch first
            last = exc
            if exc.code not in _RETRYABLE_STATUS:
                raise OutputModerationUnavailable(f"detector request failed: {exc}") from exc
            delay = _retry_delay(attempt, exc)
        except json.JSONDecodeError as exc:
            # A body that is not JSON will not become JSON on a second ask.
            raise OutputModerationUnavailable(f"detector returned non-JSON: {exc}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            delay = _retry_delay(attempt, None)

        if attempt == _MAX_ATTEMPTS - 1 or time.monotonic() + delay >= deadline:
            break
        time.sleep(delay)

    raise OutputModerationUnavailable(
        f"detector request failed after {attempts} attempt(s): {last}"
    )


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

        project = (
            os.environ.get("VERTEX_PROJECT")
            or _read_config_key("vertex_project")
            or _vertex_profile_key("vertex_project")
            or ""
        ).strip()
        if not project:
            return None
        location = (
            os.environ.get("LUMERI_V3_LOCATION")
            or _read_config_key("lumeri_v3_location")
            or os.environ.get("VERTEX_LOCATION")
            or _read_config_key("vertex_location")
            or _vertex_profile_key("vertex_location")
            or _vertex_profile_key("location")
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
        payload = _post_json(opener, request)

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
        payload = _post_json(urllib.request.build_opener(), request)

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


def _vertex_profile_key(field: str) -> str:
    """Read a Vertex field from the configured provider profile.

    This deployment keeps its Vertex project inside the ``vertex`` entry of
    ``brain_provider_profiles`` rather than at the top level of the config. A
    detector that reads only top-level keys therefore finds nothing on a fully
    credentialed machine and reports itself as "no detector configured", which
    is the one failure mode /health exists to make visible.
    """
    from gemia.gemini_client import _read_config_value  # local: heavy module

    profiles = _read_config_value("brain_provider_profiles")
    if not isinstance(profiles, dict):
        return ""
    vertex = profiles.get("vertex")
    if not isinstance(vertex, dict):
        return ""
    value = vertex.get(field)
    return value.strip() if isinstance(value, str) else ""


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
