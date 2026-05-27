"""HTTP routes for Lumeri v3 sessions.

    POST   /sessions                            create session
    GET    /sessions/{id}                       info (assets, latest_event_id)
    POST   /sessions/{id}/turn                  submit user message (202)
    POST   /sessions/{id}/assets                upload asset (raw body + X-Filename)
    GET    /sessions/{id}/assets                list session assets
    GET    /sessions/{id}/assets/{asset_id}     serve asset file (Range supported)
    POST   /sessions/{id}/close                 close session
    GET    /sessions/{id}/stream                SSE event stream (Last-Event-ID)

``try_handle(handler, method=...)`` is the single entrypoint server.py
calls. Returns True if the request was handled, False to let the host
server continue routing.

Uploads: raw body POST. ``X-Filename`` header carries the original
filename (URL-encoded; Unicode safe). Size capped by
``LUMERI_V3_UPLOAD_MAX_BYTES`` (default 500 MiB).

Asset URLs: per-session, e.g. ``/sessions/v3-abc/assets/v_002``. The
frontend constructs these from ``asset_id`` returned in SSE
``tool_exec_result.result.asset_id`` events; no separate global asset
table.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from gemia.session_manager import SessionRunner, get_manager
from gemia.transport.sse import REGISTRY as SSE_REGISTRY
from gemia.transport.sse import iter_events


_DEFAULT_MAX_UPLOAD_BYTES = 500 * 1024 * 1024
_CHUNK = 64 * 1024


def _max_upload_bytes() -> int:
    try:
        return int(os.environ.get("LUMERI_V3_UPLOAD_MAX_BYTES") or _DEFAULT_MAX_UPLOAD_BYTES)
    except ValueError:
        return _DEFAULT_MAX_UPLOAD_BYTES


def try_handle(handler, *, method: str) -> bool:
    parsed = urlparse(handler.path)
    path = unquote(parsed.path).rstrip("/") or "/"
    query = parse_qs(parsed.query)

    if path != "/sessions" and not path.startswith("/sessions/"):
        return False

    try:
        if method == "POST":
            return _route_post(handler, path, query)
        if method in {"GET", "HEAD"}:
            return _route_get(handler, path, query, body=(method == "GET"))
    except Exception as exc:
        _json_error(handler, 500, f"{type(exc).__name__}: {exc}")
        return True

    _json_error(handler, 405, f"method {method} not allowed on {path}")
    return True


# ── routing tables ────────────────────────────────────────────────────


def _route_post(handler, path: str, query: dict) -> bool:
    if path == "/sessions":
        return _create_session(handler)

    m = re.match(r"^/sessions/([^/]+)/(turn|assets|close)$", path)
    if not m:
        return False
    session_id, action = m.group(1), m.group(2)
    runner = get_manager().get(session_id)
    if runner is None:
        _json_error(handler, 404, f"unknown session: {session_id}")
        return True

    if action == "turn":
        return _submit_turn(handler, runner)
    if action == "assets":
        return _upload_asset(handler, runner)
    if action == "close":
        return _close_session(handler, runner)
    return False


def _route_get(handler, path: str, query: dict, *, body: bool) -> bool:
    m = re.match(r"^/sessions/([^/]+)/stream$", path)
    if m:
        return _sse_stream(handler, m.group(1), query, body=body)

    m = re.match(r"^/sessions/([^/]+)/assets/([^/]+)$", path)
    if m:
        return _serve_asset(handler, m.group(1), m.group(2), body=body)

    m = re.match(r"^/sessions/([^/]+)/assets$", path)
    if m:
        return _list_assets(handler, m.group(1))

    m = re.match(r"^/sessions/([^/]+)$", path)
    if m:
        return _session_info(handler, m.group(1))

    return False


# ── POST handlers ─────────────────────────────────────────────────────


def _create_session(handler) -> bool:
    runner = get_manager().create_session()
    sid = runner.session_id
    _json_response(handler, 201, {
        "session_id": sid,
        "stream_url": f"/sessions/{sid}/stream",
        "turn_url":   f"/sessions/{sid}/turn",
        "assets_url": f"/sessions/{sid}/assets",
        "close_url":  f"/sessions/{sid}/close",
    })
    return True


def _submit_turn(handler, runner: SessionRunner) -> bool:
    body = _read_json_body(handler)
    if body is None:
        return True
    message = body.get("message")
    if not isinstance(message, str) or not message.strip():
        _json_error(handler, 400, "request body must include non-empty 'message' string")
        return True
    runner.submit_turn(message)
    _json_response(handler, 202, {"session_id": runner.session_id, "accepted": True})
    return True


def _upload_asset(handler, runner: SessionRunner) -> bool:
    length = int(handler.headers.get("Content-Length") or "0")
    if length <= 0:
        _json_error(handler, 400, "Content-Length required and must be > 0")
        return True
    cap = _max_upload_bytes()
    if length > cap:
        _json_error(handler, 413, f"upload too large: {length} > {cap} bytes")
        return True

    filename_raw = handler.headers.get("X-Filename") or "upload.bin"
    filename = Path(unquote(filename_raw)).name or "upload.bin"

    uploads_dir = runner.output_dir / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    temp_path = uploads_dir / f"upload-{uuid.uuid4().hex[:12]}{Path(filename).suffix}"

    bytes_read = 0
    with temp_path.open("wb") as f:
        while bytes_read < length:
            chunk = handler.rfile.read(min(_CHUNK, length - bytes_read))
            if not chunk:
                break
            f.write(chunk)
            bytes_read += len(chunk)

    if bytes_read != length:
        temp_path.unlink(missing_ok=True)
        _json_error(handler, 400, f"upload truncated: got {bytes_read} of {length} bytes")
        return True

    try:
        asset_id = runner.add_external_asset(temp_path, summary=f"user-uploaded {filename}")
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        _json_error(handler, 400, f"failed to register asset: {exc}")
        return True

    _json_response(handler, 201, {
        "asset_id": asset_id,
        "filename": filename,
        "size_bytes": bytes_read,
        "preview_url": f"/sessions/{runner.session_id}/assets/{asset_id}",
    })
    return True


def _close_session(handler, runner: SessionRunner) -> bool:
    sid = runner.session_id
    get_manager().close_session(sid)
    _json_response(handler, 200, {"session_id": sid, "closed": True})
    return True


# ── GET handlers ──────────────────────────────────────────────────────


def _session_info(handler, session_id: str) -> bool:
    runner = get_manager().get(session_id)
    if runner is None:
        _json_error(handler, 404, f"unknown session: {session_id}")
        return True
    _json_response(handler, 200, {
        "session_id": session_id,
        "assets": runner.list_assets(),
        "latest_event_id": SSE_REGISTRY.latest_event_id(session_id),
    })
    return True


def _list_assets(handler, session_id: str) -> bool:
    runner = get_manager().get(session_id)
    if runner is None:
        _json_error(handler, 404, f"unknown session: {session_id}")
        return True
    _json_response(handler, 200, {"assets": runner.list_assets()})
    return True


def _serve_asset(handler, session_id: str, asset_id: str, *, body: bool) -> bool:
    runner = get_manager().get(session_id)
    if runner is None:
        _json_error(handler, 404, f"unknown session: {session_id}")
        return True
    path = runner.asset_path(asset_id)
    if path is None or not Path(path).exists():
        _json_error(handler, 404, f"unknown asset: {asset_id}")
        return True
    _serve_file_with_range(handler, Path(path), body=body)
    return True


def _sse_stream(handler, session_id: str, query: dict, *, body: bool) -> bool:
    last_id_raw = handler.headers.get("Last-Event-ID")
    if last_id_raw is None:
        q_last = query.get("last_event_id")
        last_id_raw = q_last[0] if q_last else None
    try:
        last_id = int(last_id_raw) if last_id_raw is not None else None
    except (TypeError, ValueError):
        last_id = None
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Accel-Buffering", "no")
    handler.end_headers()
    if not body:
        return True
    try:
        for chunk in iter_events(session_id, last_event_id=last_id):
            handler.wfile.write(chunk)
            handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError):
        pass
    return True


# ── helpers ───────────────────────────────────────────────────────────


def _serve_file_with_range(handler, path: Path, *, body: bool) -> None:
    file_size = path.stat().st_size
    mime, _ = mimetypes.guess_type(str(path))
    mime = mime or "application/octet-stream"

    range_header = handler.headers.get("Range")
    start: int
    end: int
    use_range = False
    if range_header and range_header.startswith("bytes="):
        spec = range_header[len("bytes="):]
        if "," in spec:
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{file_size}")
            handler.end_headers()
            return
        try:
            start_s, end_s = spec.split("-", 1)
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else file_size - 1
        except ValueError:
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{file_size}")
            handler.end_headers()
            return
        if start >= file_size or end >= file_size or start > end:
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{file_size}")
            handler.end_headers()
            return
        use_range = True
    else:
        start = 0
        end = file_size - 1

    content_length = end - start + 1
    handler.send_response(206 if use_range else 200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(content_length))
    handler.send_header("Accept-Ranges", "bytes")
    if use_range:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
    handler.end_headers()
    if not body:
        return
    with path.open("rb") as f:
        if start:
            f.seek(start)
        remaining = content_length
        while remaining > 0:
            chunk = f.read(min(_CHUNK, remaining))
            if not chunk:
                break
            handler.wfile.write(chunk)
            remaining -= len(chunk)


def _read_json_body(handler) -> dict[str, Any] | None:
    length = int(handler.headers.get("Content-Length") or "0")
    if length <= 0:
        _json_error(handler, 400, "missing JSON body")
        return None
    raw = handler.rfile.read(length)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _json_error(handler, 400, f"invalid JSON: {exc}")
        return None
    if not isinstance(data, dict):
        _json_error(handler, 400, "request body must be a JSON object")
        return None
    return data


def _json_response(handler, status: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _json_error(handler, status: int, message: str) -> None:
    _json_response(handler, status, {"error": message})


__all__ = ["try_handle"]
