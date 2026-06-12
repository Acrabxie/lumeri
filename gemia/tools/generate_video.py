"""generate_video — Veo via Vertex AI.

Host-side slow tool. It submits ``:predictLongRunning``, polls
``:fetchPredictOperation`` until completion, decodes the resulting video bytes,
writes them into the session workspace, registers a video asset, and returns
metadata only. Raw base64/video bytes never enter the tool result/SSE.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from pathlib import Path
from typing import Any

from gemia.ai.google_genai_client import GoogleGenAIClient, VertexAPIError
from gemia.budget_guard import tool_cost_usd
from gemia.tools._context import ToolContext, ProgressUpdate


_DEFAULT_MODEL = "veo-3.1-fast-generate-preview"
_DEFAULT_LOCATION = "us-central1"
_DEFAULT_MAX_WAIT_SEC = 300.0
_DEFAULT_POLL_INTERVAL_SEC = 10.0


async def dispatch(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("generate_video requires a non-empty prompt")

    camera = str(args.get("camera") or "").strip()
    if camera:
        prompt = f"{prompt}. Camera motion: {camera}"
    aspect_ratio = str(args.get("aspect_ratio") or "16:9").strip() or "16:9"
    duration_sec = _clamp_duration(args.get("duration_sec"))

    instance: dict[str, Any] = {"prompt": prompt}
    reference_asset_id = str(args.get("reference_asset_id") or "").strip()
    if reference_asset_id:
        ref = ctx.registry.get(reference_asset_id)
        if ref.kind != "image":
            raise ValueError(
                f"reference_asset_id {reference_asset_id!r} is {ref.kind!r}, expected image"
            )
        ref_bytes = Path(ref.path).read_bytes()
        instance["image"] = {
            "bytesBase64Encoded": base64.b64encode(ref_bytes).decode("ascii"),
            "mimeType": _sniff_image_mime(ref_bytes),
        }

    client = _client_from_ctx(ctx)
    model = _model()
    new_id = ctx.registry.allocate_id("video")
    ctx.emit_progress(ProgressUpdate(percent=2, message="submitting Veo job", eta_sec=120))
    submit = await client.predict_long_running(
        model=model,
        instances=[instance],
        parameters={
            "aspectRatio": aspect_ratio,
            "durationSeconds": duration_sec,
            "sampleCount": 1,
            "personGeneration": "allow_adult",
            "addWatermark": True,
        },
        verb="generate_video",
        estimated_cost_usd=tool_cost_usd("generate_video"),
    )
    operation_name = str(submit.get("name") or "").strip()
    if not operation_name:
        raise VertexAPIError(
            f"Vertex Veo submission returned no operation name (model={model})",
            status=200,
            body_tail=json.dumps(_scrub_bytes(submit), ensure_ascii=False)[:1200],
        )

    response = await _poll_until_done(
        client,
        model=model,
        operation_name=operation_name,
        max_wait_sec=_max_wait_sec(args),
        poll_interval_sec=_poll_interval_sec(args),
        ctx=ctx,
    )
    video_bytes, mime_type = _extract_video_payload(response, model=model)
    ext = _extension_for_mime(mime_type)
    out_path = ctx.child_path(new_id, ext)
    out_path.write_bytes(video_bytes)

    lineage = [reference_asset_id] if reference_asset_id else []
    record = ctx.registry.register_output(
        new_id,
        kind="video",
        path=out_path,
        summary=f"generated video via {model}: {_short(prompt)!r}",
        lineage=lineage,
    )
    return {
        "asset_id": new_id,
        "summary": record.summary,
        "metadata": {
            "model": model,
            "provider": "vertex",
            "operation_name": operation_name,
            "mime_type": mime_type,
            "size_bytes": len(video_bytes),
            "duration_sec": duration_sec,
            "aspect_ratio": aspect_ratio,
            "location": client.location,
            "request_id": submit.get("_lumeri_request_id"),
            "reference_asset_id": reference_asset_id or None,
        },
    }


async def _poll_until_done(
    client: GoogleGenAIClient,
    *,
    model: str,
    operation_name: str,
    max_wait_sec: float,
    poll_interval_sec: float,
    ctx: ToolContext,
) -> dict[str, Any]:
    started = time.monotonic()
    attempt = 0
    last_response: dict[str, Any] = {}
    while True:
        attempt += 1
        response = await client.fetch_predict_operation(
            model=model,
            operation_name=operation_name,
        )
        last_response = response
        if response.get("done") is True:
            error = response.get("error")
            if error:
                raise VertexAPIError(
                    f"Vertex Veo operation failed: {error}",
                    status=200,
                    body_tail=json.dumps(_scrub_bytes(response), ensure_ascii=False)[:1200],
                )
            return response

        elapsed = time.monotonic() - started
        if elapsed >= max_wait_sec:
            raise TimeoutError(
                f"Veo operation still running after {elapsed:.0f}s "
                f"(operation_name={operation_name!r}). Last response: "
                f"{json.dumps(_scrub_bytes(last_response), ensure_ascii=False)[:800]}"
            )
        percent = min(95.0, 8.0 + elapsed / max(max_wait_sec, 1.0) * 80.0)
        ctx.emit_progress(
            ProgressUpdate(
                percent=percent,
                message=f"waiting for Veo operation ({attempt})",
                eta_sec=max(0.0, max_wait_sec - elapsed),
            )
        )
        await asyncio.sleep(poll_interval_sec)


def _extract_video_payload(response_json: dict[str, Any], *, model: str) -> tuple[bytes, str]:
    response = response_json.get("response") if isinstance(response_json, dict) else None
    root = response if isinstance(response, dict) else response_json
    found = _find_base64_payload(root, preferred_mime_prefix="video/")
    if found is None:
        raise VertexAPIError(
            f"Vertex Veo response had no video payload (model={model})",
            status=200,
            body_tail=json.dumps(_scrub_bytes(response_json), ensure_ascii=False)[:1200],
        )
    data, mime_type = found
    try:
        return base64.b64decode(data), mime_type or "video/mp4"
    except (TypeError, ValueError) as exc:
        raise VertexAPIError(f"Vertex Veo base64 decode failed: {exc}", status=200) from exc


def _find_base64_payload(
    value: Any,
    *,
    preferred_mime_prefix: str,
) -> tuple[str, str] | None:
    if isinstance(value, dict):
        mime_type = str(value.get("mimeType") or value.get("mime_type") or "")
        for key in ("bytesBase64Encoded", "bytes_base64_encoded", "data"):
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
            k: ("<base64 omitted>" if k in {"bytesBase64Encoded", "data"} else _scrub_bytes(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub_bytes(v) for v in value]
    return value


def _client_from_ctx(ctx: ToolContext) -> GoogleGenAIClient:
    cache_key = "_google_genai_client_video"
    cached = ctx.extra.get(cache_key) if isinstance(ctx.extra, dict) else None
    if isinstance(cached, GoogleGenAIClient):
        return cached
    client = GoogleGenAIClient(location=_location())
    if isinstance(ctx.extra, dict):
        ctx.extra[cache_key] = client
    return client


def _model() -> str:
    return (
        os.environ.get("VERTEX_VIDEO_MODEL")
        or _read_config("vertex_video_model")
        or _read_config("video_model")
        or _read_config("veo_model")
        or _DEFAULT_MODEL
    ).strip()


def _location() -> str:
    return (
        os.environ.get("VERTEX_VIDEO_LOCATION")
        or _read_config("vertex_video_location")
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


def _max_wait_sec(args: dict[str, Any]) -> float:
    try:
        value = float(args.get("max_wait_sec") or _DEFAULT_MAX_WAIT_SEC)
    except (TypeError, ValueError):
        value = _DEFAULT_MAX_WAIT_SEC
    return min(max(value, 30.0), 900.0)


def _poll_interval_sec(args: dict[str, Any]) -> float:
    try:
        value = float(args.get("poll_interval_sec") or _DEFAULT_POLL_INTERVAL_SEC)
    except (TypeError, ValueError):
        value = _DEFAULT_POLL_INTERVAL_SEC
    return min(max(value, 0.1), 30.0)


def _clamp_duration(value: Any) -> int:
    try:
        duration = int(round(float(value)))
    except (TypeError, ValueError):
        duration = 8
    return min(max(duration, 1), 8)


def _extension_for_mime(mime_type: str) -> str:
    mt = (mime_type or "").lower().strip()
    if mt in {"video/mp4", ""}:
        return ".mp4"
    if mt in {"video/quicktime", "video/mov"}:
        return ".mov"
    if mt == "video/webm":
        return ".webm"
    return ".bin"


def _sniff_image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _short(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "..."


__all__ = ["dispatch"]
