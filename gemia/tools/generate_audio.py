"""generate_audio — Lyria via Vertex AI.

Host-side synchronous tool. The model supplies only prompt-like arguments; the
host holds Vertex auth, calls the code-ranked strongest Lyria model, decodes the returned audio
bytes, writes them into the session workspace, registers an audio asset, and
returns metadata only. Raw base64/audio bytes never enter the tool result/SSE.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from gemia.ai.google_genai_client import GoogleGenAIClient, VertexAPIError
from gemia.budget_guard import tool_cost_usd
from gemia.model_strength import is_model_unavailable_error, media_model_failover_chain, strongest_media_model
from gemia.tools._context import ToolContext, ProgressUpdate


_DEFAULT_MODEL = strongest_media_model("audio", "vertex")
_DEFAULT_LOCATION = "us-central1"


async def dispatch(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("generate_audio requires a non-empty prompt")

    mood = str(args.get("mood") or "").strip()
    bpm = args.get("bpm")
    prompt = _augment_prompt(prompt, mood=mood, bpm=bpm)

    client = _client_from_ctx(ctx)
    new_id = ctx.registry.allocate_id("audio")
    ctx.emit_progress(ProgressUpdate(percent=5, message="calling Lyria API", eta_sec=30))
    response: dict[str, Any] | None = None
    chain = media_model_failover_chain("audio", "vertex", (_model(),))
    for index, model in enumerate(chain):
        try:
            response = await client.predict(
                model=model,
                instances=[{"prompt": prompt}],
                parameters={"sampleCount": 1},
                verb="generate_audio",
                estimated_cost_usd=tool_cost_usd("generate_audio"),
            )
            break
        except VertexAPIError as exc:
            if index + 1 >= len(chain) or not is_model_unavailable_error(exc):
                raise
    assert response is not None
    ctx.emit_progress(ProgressUpdate(percent=80, message="decoding Lyria audio", eta_sec=2))
    audio_bytes, mime_type = _extract_audio_payload(response, model=model)
    ext = _extension_for_mime(mime_type)
    out_path = ctx.child_path(new_id, ext)
    out_path.write_bytes(audio_bytes)

    record = ctx.registry.register_output(
        new_id,
        kind="audio",
        path=out_path,
        summary=f"generated audio via {model}: {_short(prompt)!r}",
        lineage=(),
    )
    return {
        "asset_id": new_id,
        "summary": record.summary,
        "metadata": {
            "model": model,
            "provider": "vertex",
            "mime_type": mime_type,
            "size_bytes": len(audio_bytes),
            "location": client.location,
            "request_id": response.get("_lumeri_request_id"),
            "duration_sec": 30,
        },
    }


def _client_from_ctx(ctx: ToolContext) -> GoogleGenAIClient:
    cache_key = "_google_genai_client_audio"
    cached = ctx.extra.get(cache_key) if isinstance(ctx.extra, dict) else None
    if isinstance(cached, GoogleGenAIClient):
        return cached
    client = GoogleGenAIClient(location=_location())
    if isinstance(ctx.extra, dict):
        ctx.extra[cache_key] = client
    return client


def _augment_prompt(prompt: str, *, mood: str, bpm: Any) -> str:
    hints: list[str] = []
    if mood:
        hints.append(f"mood: {mood}")
    try:
        bpm_num = float(bpm) if bpm is not None else 0.0
    except (TypeError, ValueError):
        bpm_num = 0.0
    if bpm_num > 0:
        hints.append(f"tempo: {bpm_num:g} BPM")
    if not hints:
        return prompt
    return f"{prompt}. " + ". ".join(hints)


def _extract_audio_payload(response_json: dict[str, Any], *, model: str) -> tuple[bytes, str]:
    candidates = response_json.get("predictions") or response_json.get("candidates") or []
    for candidate in candidates:
        found = _find_base64_payload(candidate, preferred_mime_prefix="audio/")
        if found is None:
            continue
        data, mime_type = found
        try:
            return base64.b64decode(data), mime_type or "audio/wav"
        except (TypeError, ValueError) as exc:
            raise VertexAPIError(f"Vertex Lyria base64 decode failed: {exc}", status=200) from exc
    raise VertexAPIError(
        f"Vertex Lyria response had no audio payload (model={model})",
        status=200,
        body_tail=json.dumps(_scrub_bytes(response_json), ensure_ascii=False)[:1200],
    )


def _find_base64_payload(
    value: Any,
    *,
    preferred_mime_prefix: str,
) -> tuple[str, str] | None:
    if isinstance(value, dict):
        mime_type = str(value.get("mimeType") or value.get("mime_type") or "")
        for key in ("bytesBase64Encoded", "bytes_base64_encoded", "audioContent", "data"):
            data = value.get(key)
            if isinstance(data, str) and data.strip():
                if not mime_type or mime_type.startswith(preferred_mime_prefix):
                    return data, mime_type
        for child in value.values():
            found = _find_base64_payload(child, preferred_mime_prefix=preferred_mime_prefix)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_base64_payload(child, preferred_mime_prefix=preferred_mime_prefix)
            if found is not None:
                return found
    return None


def _scrub_bytes(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("<base64 omitted>" if k in {"bytesBase64Encoded", "audioContent", "data"} else _scrub_bytes(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub_bytes(v) for v in value]
    return value


def _model() -> str:
    return strongest_media_model(
        "audio",
        "vertex",
        (
            os.environ.get("VERTEX_AUDIO_MODEL"),
            _read_config("vertex_audio_model"),
            _read_config("audio_model"),
            _read_config("lyria_model"),
            _DEFAULT_MODEL,
        ),
    )


def _location() -> str:
    return (
        os.environ.get("VERTEX_AUDIO_LOCATION")
        or _read_config("vertex_audio_location")
        or _DEFAULT_LOCATION
    ).strip()


def _read_config(field: str) -> str:
    try:
        path = Path.home() / ".gemia" / "config.json"
        if not path.exists():
            return ""
        data = json.loads(path.read_text(encoding="utf-8"))
        return str(data.get(field) or "").strip()
    except Exception:
        return ""


def _extension_for_mime(mime_type: str) -> str:
    mt = (mime_type or "").lower().strip()
    if mt in {"audio/wav", "audio/wave", "audio/x-wav", ""}:
        return ".wav"
    if mt in {"audio/mpeg", "audio/mp3"}:
        return ".mp3"
    if mt == "audio/ogg":
        return ".ogg"
    if mt == "audio/aac":
        return ".aac"
    return ".bin"


def _short(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "..."


__all__ = ["dispatch"]
