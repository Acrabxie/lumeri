"""Minimal local HTTP server for Gemia MVP.

Endpoints:
  GET  /                        → web UI (tauri-app/dist/index.html, fallback static/index.html)
  GET  /assets/<rel-path>       → web UI bundle assets (tauri-app/dist/assets)
  GET  /file/<rel-path>         → serve project files from approved media roots
  GET  /config                  → {"has_key": bool}
  GET  /session-history         → active account's current UI session
  GET  /session-history/list    → active account's recent UI sessions
  GET  /session-history/<id>    → open one previous UI session snapshot
  POST /session-history         → save active account's current UI session
  POST /upload-media            → import video/image/audio assets
  GET  /media-library/list      → account-scoped media assets
  GET  /media-library/<id>      → one media asset
  GET  /media-library/file/<id>/<area>/<file?> → media original/cache file
  POST /media-library/import    → import a server-local media path
  POST /media-library/<id>/add-to-project → make a default timeline clip
  DELETE /media-library/<id>    → soft-delete one media asset
  GET  /agent-links/status      → codex-lumeri/gemini-lumeri link status and recent relay messages
  GET  /agent-links/messages    → recent local relay messages
  POST /agent-links/link        → mark codex-lumeri or gemini-lumeri linked in the top bar
  POST /agent-links/message     → send one local relay message, optionally invoking target CLI
  POST /agent-links/relay       → run one codex-lumeri ↔ gemini-lumeri relay round through Lumeri
  POST /config                  → save API keys to ~/.gemia/config.json
  POST /run-skill               body: {"skill_id": str, "inputs": {...}}
  GET  /task/<task_id>
  GET  /task/<task_id>/assets
  GET  /next                    → primary web UI alias for staged rollouts
  POST /runtime/session         → gated Runtime Kernel session
  POST /runtime/message         → gated Runtime Kernel message/script/render
  GET  /runtime/task/<id>       → gated Runtime Kernel background task status
  POST /runtime/dev/workspace   → gated Creative Dev Sandbox workspace
  POST /runtime/dev/workspace/<id>/run → gated Creative Dev Sandbox command runner
  GET  /runtime/events/<id>     → gated Runtime Kernel event log
  GET  /runtime/project/<id>    → gated Runtime Kernel project state
  GET  /skills
"""

from __future__ import annotations

import json
import hashlib
import mimetypes
import os
import re
import shlex
import socket
import subprocess
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

_CONFIG_PATH = Path.home() / ".gemia" / "config.json"
_DEFAULT_IMAGE_MODEL = "google/gemini-2.5-flash-image"
_DEFAULT_IMAGE_BASE_URL = "https://openrouter.ai/api/v1"


def _legacy_image_model(value: object) -> bool:
    lowered = str(value or "").strip().lower()
    return lowered in {"gpt-image-2", "gpt_image2", "gpt image2"}


def _configured_image_model() -> str:
    value = os.environ.get("GEMIA_IMAGE_MODEL") or ""
    return _DEFAULT_IMAGE_MODEL if _legacy_image_model(value) else (value or _DEFAULT_IMAGE_MODEL)


def _configured_image_base_url() -> str:
    value = os.environ.get("GEMIA_IMAGE_BASE_URL") or os.environ.get("OPENROUTER_IMAGE_URL") or ""
    if value and "sisyphusx.com" not in value:
        return value
    return _DEFAULT_IMAGE_BASE_URL


def _configured_server_host(default: str = "0.0.0.0") -> str:
    return os.environ.get("LUMERI_HOST") or os.environ.get("GEMIA_HOST") or default


def _lan_addresses() -> list[str]:
    addresses: set[str] = set()

    def add_candidate(value: str) -> None:
        value = str(value or "").strip()
        try:
            import ipaddress

            address = ipaddress.ip_address(value)
            benchmark_net = ipaddress.ip_network("198.18.0.0/15")
            if (
                address.version == 4
                and not address.is_loopback
                and not address.is_link_local
                and not address.is_multicast
                and not address.is_unspecified
                and address not in benchmark_net
            ):
                addresses.add(value)
        except Exception:
            return

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            add_candidate(sock.getsockname()[0])
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add_candidate(str(info[4][0]))
    except Exception:
        pass
    try:
        output = subprocess.check_output(["/sbin/ifconfig"], text=True, stderr=subprocess.DEVNULL, timeout=1.5)
        for line in output.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0] == "inet":
                add_candidate(parts[1])
    except Exception:
        pass
    return sorted(addresses)


def _server_urls(host: str, port: int) -> list[str]:
    if host in {"0.0.0.0", "::", ""}:
        urls = [f"http://127.0.0.1:{port}"]
        urls.extend(f"http://{address}:{port}" for address in _lan_addresses())
        return urls
    return [f"http://{host}:{port}"]


# ── Security gate helpers ────────────────────────────────────────────────
# Defends 7788 against (a) DNS rebinding from a browser tab pointing at a
# malicious page that resolves to 127.0.0.1, and (b) cross-origin POSTs from
# arbitrary local apps. Local CLIs/Tauri/native mobile callers don't send
# Origin/Referer, so they are still allowed; only browser callers with a
# foreign Origin are blocked.

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
# Tauri / native shells use these schemes; allow them when Origin is present.
_NATIVE_ORIGIN_SCHEMES = {"tauri", "lumeri", "app"}
_LAN_ADDRESS_CACHE: tuple[float, list[str]] | None = None
_LAN_ADDRESS_TTL_SEC = 30.0


def _cached_lan_addresses() -> list[str]:
    """Cheap LAN-address lookup; the real call shells out to ifconfig."""
    global _LAN_ADDRESS_CACHE
    import time as _time
    now = _time.time()
    if _LAN_ADDRESS_CACHE is not None and now - _LAN_ADDRESS_CACHE[0] < _LAN_ADDRESS_TTL_SEC:
        return list(_LAN_ADDRESS_CACHE[1])
    addrs = _lan_addresses()
    _LAN_ADDRESS_CACHE = (now, list(addrs))
    return list(addrs)


def _host_allowed(host_header: str) -> bool:
    """Return True if the Host header points at this server."""
    raw = (host_header or "").strip().lower()
    if not raw:
        # No Host header: most CLIs and Python urllib still send one, so an
        # empty Host is unusual and easier to block than to defend against.
        return False
    host_only = raw.split("]")[-1].split(":")[0] if raw.startswith("[") else raw.split(":")[0]
    if host_only in _LOOPBACK_HOSTS:
        return True
    return host_only in _cached_lan_addresses()


def _require_account(handler: BaseHTTPRequestHandler) -> str | None:
    """Return the active account_id or send 401 and return None."""
    account_id = accounts.current_account_id()
    if not account_id:
        _json_response(handler, 401, {"error": "not signed in"})
        return None
    return account_id


def _video_path_allowed(account_id: str | None, video: str) -> bool:
    """Reject media paths that don't live in this account's library or in
    the project-local input/output staging dirs. Used by /run-prompt,
    /run-skill, /quick-action and /video-summary to keep unauthenticated
    callers (or swapped accounts) from coercing ffmpeg into reading another
    user's media originals.
    """
    if not video:
        return False
    try:
        resolved = Path(video).expanduser().resolve()
    except Exception:
        return False
    base = _BASE_DIR.resolve()
    candidates: list[Path] = [
        (base / name).resolve()
        for name in ("inputs", "outputs", "frames", "styled", "demo", "uploads", "temp")
    ]
    if account_id:
        try:
            from gemia.media_library import cache_root, originals_root
            candidates.append(originals_root(account_id).resolve())
            candidates.append(cache_root(account_id).resolve())
        except Exception:
            pass
    for root in candidates:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _origin_allowed(origin_or_referer: str) -> bool:
    """Return True if Origin/Referer points at this loopback or our LAN host."""
    value = (origin_or_referer or "").strip()
    if not value:
        return True  # absent header → not a browser cross-origin call
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    scheme = (parsed.scheme or "").lower()
    if scheme in _NATIVE_ORIGIN_SCHEMES:
        return True
    if scheme not in {"http", "https"}:
        return False
    netloc = (parsed.netloc or "").lower()
    if not netloc:
        return False
    host_only = netloc.split("]")[-1].split(":")[0] if netloc.startswith("[") else netloc.split(":")[0]
    if host_only in _LOOPBACK_HOSTS:
        return True
    return host_only in _cached_lan_addresses()


def _load_config_keys() -> None:
    """Load API keys from ~/.gemia/config.json into env vars (if not already set)."""
    if _CONFIG_PATH.exists():
        try:
            cfg = json.loads(_CONFIG_PATH.read_text())
            if key := cfg.get("openrouter_api_key"):
                os.environ.setdefault("OPENROUTER_API_KEY", key)
            if key := cfg.get("gemini_api_key"):
                os.environ.setdefault("GEMINI_API_KEY", key)
            if key := cfg.get("laozhang_api_key"):
                os.environ.setdefault("LAOZHANG_API_KEY", key)
            if value := cfg.get("image_base_url"):
                os.environ.setdefault("GEMIA_IMAGE_BASE_URL", value)
            if value := cfg.get("openrouter_image_url"):
                os.environ.setdefault("OPENROUTER_IMAGE_URL", value)
            if value := cfg.get("image_model"):
                if not _legacy_image_model(value):
                    os.environ.setdefault("GEMIA_IMAGE_MODEL", value)
        except Exception:
            pass


def _has_valid_key() -> bool:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    return bool(key) and key not in ("test", "sk-or-...") and len(key) > 10


def _has_valid_image_key() -> bool:
    # OPENAI_API_KEY is intentionally NOT consulted; see GenerativeClient docstring.
    try:
        cfg = json.loads(_CONFIG_PATH.read_text()) if _CONFIG_PATH.exists() else {}
    except Exception:
        cfg = {}
    key = (
        os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("GEMIA_OPENROUTER_API_KEY")
        or os.environ.get("GEMIA_IMAGE_API_KEY")
        or str(cfg.get("openrouter_api_key") or cfg.get("image_api_key") or cfg.get("nano_banana_api_key") or "")
    )
    return bool(key) and key not in ("test", "sk-...") and len(key) > 10


def _model_profile_payload() -> dict:
    from gemia.memory import public_model_profile

    return public_model_profile()

from gemia import accounts
from gemia.artifacts import artifact_outputs as _artifact_outputs
from gemia.artifacts import is_document_artifact_output as _is_document_artifact_output
from gemia.artifacts import is_media_output as _is_media_output
from gemia.artifacts import is_video_output as _is_video_output
from gemia.artifacts import media_outputs as _media_outputs
from gemia.artifacts import output_paths as _output_paths
from gemia.agent_workflow import (
    run_agent_workflow,
    run_timeline_kept_clip_merge,
    _prompt_requests_hard_cut,
    _requested_total_duration_sec,
    _render_timeline_broll_preview,
    _render_timeline_kept_clip_merge,
)
from gemia.stability import (
    TASK_STATUSES,
    error_envelope as _stability_error_envelope,
    error_event as _stability_error_event,
    normalize_task_status as _normalize_task_status,
    stability_gate_enabled as _stability_gate_enabled,
)
from gemia.orchestrator import GemiaOrchestrator, get_assets, get_task, run_skill, plan_from_primitives
from gemia.ai.sub_agents import SubAgentRegistry

# In-memory store for pending ask sessions. Each entry MUST carry account_id
# and created_at so that account-switch cannot let user B answer user A's ask.
_pending_asks: dict[str, dict] = {}
_PENDING_ASK_TTL_SEC = 30 * 60  # 30 minutes
# In-memory store for task execution progress {task_id: {current_step, total_steps, current_function}}
_task_progress: dict[str, dict] = {}
# In-memory store for vNext Runtime Kernel background message tasks.
_runtime_tasks: dict[str, dict] = {}
_runtime_tasks_lock = threading.Lock()
_task_write_lock = threading.Lock()
_LIVE_LOG_SECRET_RE = re.compile(
    r"(?i)(authorization|api[_-]?key|token|secret|password)(['\"\s:=]+)([A-Za-z0-9._\-+/=]{8,})"
)


def _prune_pending_asks() -> None:
    import time as _time
    now = _time.time()
    stale = [
        ask_id for ask_id, session in list(_pending_asks.items())
        if now - float(session.get("created_at_ts") or 0) > _PENDING_ASK_TTL_SEC
    ]
    for ask_id in stale:
        _pending_asks.pop(ask_id, None)


def _resolve_pending_ask(handler: BaseHTTPRequestHandler, ask_id: str) -> dict | None:
    """Return the pending ask session if it belongs to the current account.

    Sends a 401/404 and returns None otherwise.
    """
    _prune_pending_asks()
    session = _pending_asks.get(ask_id)
    if not session:
        _json_response(handler, 404, {"error": f"ask session not found: {ask_id}"})
        return None
    account_id = accounts.current_account_id()
    if not account_id:
        _json_response(handler, 401, {"error": "not signed in"})
        return None
    if session.get("account_id") and session["account_id"] != account_id:
        # Treat foreign-account access as if the session does not exist.
        _json_response(handler, 404, {"error": f"ask session not found: {ask_id}"})
        return None
    return session

_BASE_DIR = Path(__file__).resolve().parent
_SKILLS_DIR = _BASE_DIR / "skills"
_SKILLS_V2_DIR = _BASE_DIR / "skills_v2"
_STATIC_DIR = _BASE_DIR / "static"
_WEB_DIST_DIR = _BASE_DIR / "tauri-app" / "dist"
_WEB_ASSETS_DIR = _WEB_DIST_DIR / "assets"
_INPUTS_DIR = _BASE_DIR / "inputs"
# Directories that may be served via /file/. Keep this in sync with the
# frontend's project-relative output path resolver.
_ALLOWED_ROOTS = {"outputs", "frames", "styled", "demo", "inputs", "uploads", "temp", "timeline"}
_TASKS_DIR = _BASE_DIR / "tasks"
_PLANS_DIR = _BASE_DIR / "plans"


def _json_response(handler: BaseHTTPRequestHandler, status: int, body: object) -> None:
    data = json.dumps(body, ensure_ascii=False).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _html_response(handler: BaseHTTPRequestHandler, status: int, html: str, *, body: bool = True) -> None:
    data = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    if body:
        handler.wfile.write(data)


def _empty_response(handler: BaseHTTPRequestHandler, status: int = 204) -> None:
    handler.send_response(status)
    handler.send_header("Content-Length", "0")
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()


def _error_payload(exc: Exception, *, context: str = "") -> dict[str, object]:
    return _stability_error_envelope(exc, context=context)


def _human_error_message(exc: Exception) -> str:
    return str(_error_payload(exc).get("user_message") or str(exc))


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", 0))
    raw = handler.rfile.read(length) if length else b"{}"
    payload = json.loads(raw or b"{}")
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object")
    return payload


def _auth_callback_html(*, ok: bool, message: str) -> str:
    color = "#7dd3c7" if ok else "#ff6b82"
    title = "Lumeri 登录完成" if ok else "Lumeri 登录失败"
    safe_message = (
        message.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #080b0f;
      color: #edf3f7;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      width: min(420px, calc(100vw - 40px));
      border-radius: 14px;
      background: #11161d;
      padding: 24px;
      box-shadow: 0 24px 80px rgba(0, 0, 0, 0.45);
    }}
    h1 {{ margin: 0 0 10px; font-size: 18px; color: {color}; }}
    p {{ margin: 0; color: #9aa7b4; font-size: 14px; line-height: 1.7; }}
  </style>
</head>
<body>
  <main>
    <h1>{title}</h1>
    <p>{safe_message}</p>
  </main>
  <script>
    try {{ window.opener && window.opener.postMessage({{ type: "lumeri-auth-complete", ok: {str(ok).lower()} }}, "*"); }} catch (_) {{}}
    setTimeout(() => {{ try {{ window.close(); }} catch (_) {{}} }}, 1400);
  </script>
</body>
</html>"""


def _file_response(handler: BaseHTTPRequestHandler, path: Path, *, body: bool = True) -> None:
    if not path.exists() or not path.is_file():
        _json_response(handler, 404, {"error": "file not found"})
        return
    content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    size = path.stat().st_size

    range_header = handler.headers.get("Range", "").strip()
    start = 0
    end = size - 1
    partial = False
    if range_header.startswith("bytes="):
        requested = range_header.removeprefix("bytes=").split(",", 1)[0].strip()
        try:
            raw_start, _, raw_end = requested.partition("-")
            if raw_start:
                start = int(raw_start)
                end = int(raw_end) if raw_end else size - 1
            elif raw_end:
                suffix = int(raw_end)
                start = max(0, size - suffix)
                end = size - 1
            if size <= 0 or start < 0 or end < start or start >= size:
                raise ValueError
            end = min(end, size - 1)
            partial = True
        except ValueError:
            handler.send_response(416)
            handler.send_header("Content-Type", content_type)
            handler.send_header("Content-Range", f"bytes */{size}")
            handler.send_header("Accept-Ranges", "bytes")
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("Connection", "close")
            handler.end_headers()
            handler.close_connection = True
            return

    content_length = max(0, end - start + 1)
    handler.send_response(206 if partial else 200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(content_length))
    handler.send_header("Accept-Ranges", "bytes")
    if partial:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Connection", "close")
    handler.end_headers()
    if body:
        try:
            with path.open("rb") as file_obj:
                file_obj.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk = file_obj.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    handler.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
    handler.close_connection = True


def _web_index_path() -> Path:
    dist_index = _WEB_DIST_DIR / "index.html"
    if dist_index.exists():
        return dist_index
    return _STATIC_DIR / "index.html"


def _vnext_enabled() -> bool:
    return os.environ.get("LUMERAI_VNEXT", "0") == "1"


def _vnext_index_path() -> Path:
    return _web_index_path()


def _runtime_service():
    from gemia.runtime_vnext import RuntimeService

    return RuntimeService(_BASE_DIR)


def _creative_sandbox_service():
    from gemia.creative_sandbox import CreativeSandboxService

    return CreativeSandboxService(_BASE_DIR)


def _runtime_error_response(handler: BaseHTTPRequestHandler, exc: Exception) -> None:
    from gemia.runtime_vnext import runtime_error_payload

    status, payload = runtime_error_payload(exc)
    _json_response(handler, status, payload)


def _runtime_message_sync_requested(payload: dict, query: dict[str, list[str]]) -> bool:
    raw_query = str((query.get("sync") or [""])[0]).strip().lower()
    raw_payload = payload.get("sync")
    return raw_query in {"1", "true", "yes"} or raw_payload is True or str(raw_payload).lower() in {"1", "true", "yes"}


def _runtime_task_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_task_public(task: dict) -> dict:
    payload = dict(task)
    payload.pop("thread", None)
    return payload


def _runtime_task_payload(task_id: str) -> dict:
    with _runtime_tasks_lock:
        task = _runtime_tasks.get(task_id)
        if not task:
            return {"status": "failed", "error": {"code": "task_not_found", "message": f"找不到运行任务：{task_id}"}}
        return _runtime_task_public(task)


def _update_runtime_task(task_id: str, **updates: object) -> dict:
    with _runtime_tasks_lock:
        task = _runtime_tasks.get(task_id)
        if not task:
            task = {"task_id": task_id, "created_at": _runtime_task_now()}
            _runtime_tasks[task_id] = task
        task.update(updates)
        task["updated_at"] = _runtime_task_now()
        return _runtime_task_public(task)


def _validate_runtime_message_for_async(service: object, payload: dict) -> tuple[str, str]:
    from gemia.runtime_vnext import RuntimeApiError

    session_id = str(payload.get("session_id") or payload.get("sessionId") or "").strip()
    if not session_id:
        raise RuntimeApiError("missing_session_id", "缺少 session_id")
    if not service.sessions.exists(session_id):  # type: ignore[attr-defined]
        raise RuntimeApiError("session_not_found", f"找不到会话：{session_id}", status=404)
    meta = service.sessions.load_meta(session_id)  # type: ignore[attr-defined]
    project_id = str(payload.get("project_id") or meta.get("project_id") or "")
    if not project_id or not service.orchestrator.project_store.exists(project_id):  # type: ignore[attr-defined]
        raise RuntimeApiError("project_not_found", f"找不到项目：{project_id}", status=404)
    message = str(payload.get("message") or payload.get("prompt") or "").strip()
    if not message:
        raise RuntimeApiError("empty_message", "请输入要执行的内容")
    return session_id, project_id


def _start_runtime_message_task(service: object, payload: dict) -> dict:
    session_id, project_id = _validate_runtime_message_for_async(service, payload)
    task_id = f"rtask_{uuid.uuid4().hex[:12]}"
    now = _runtime_task_now()
    task = {
        "status": "running",
        "task_id": task_id,
        "session_id": session_id,
        "project_id": project_id,
        "created_at": now,
        "updated_at": now,
        "result": None,
        "error": None,
    }
    with _runtime_tasks_lock:
        _runtime_tasks[task_id] = task

    try:
        service.sessions.append_event(  # type: ignore[attr-defined]
            session_id,
            "runtime_task_started",
            {"task_id": task_id, "project_id": project_id, "message": str(payload.get("message") or "")[:240]},
        )
    except Exception:
        pass

    task_payload = deepcopy(payload)

    def run() -> None:
        worker_service = _runtime_service()
        try:
            result = worker_service.post_message(task_payload)
            status = str(result.get("status") or "succeeded")
            existing_events = []
            try:
                existing_events = worker_service.sessions.read_events(session_id)
            except Exception:
                existing_events = []
            if status == "succeeded" and not any(event.get("type") == "succeeded" for event in existing_events):
                worker_service.sessions.append_event(
                    session_id,
                    "succeeded",
                    {"task_id": task_id, "project_id": project_id},
                )
                result = {**result, "events": worker_service.sessions.read_events(session_id)}
            _update_runtime_task(task_id, status=status, result=result, error=result.get("error"))
        except Exception as exc:
            from gemia.runtime_vnext import runtime_error_payload

            _, error_payload = runtime_error_payload(exc)
            try:
                worker_service.sessions.append_event(
                    session_id,
                    "failed",
                    {
                        "task_id": task_id,
                        "project_id": project_id,
                        "error_code": (error_payload.get("error") or {}).get("code"),
                        "user_message": (error_payload.get("error") or {}).get("message"),
                    },
                )
            except Exception:
                pass
            error_payload = {**error_payload, "session_id": session_id, "project_id": project_id}
            try:
                error_payload["events"] = worker_service.sessions.read_events(session_id)
            except Exception:
                pass
            _update_runtime_task(task_id, status="failed", result=error_payload, error=error_payload.get("error"))

    thread = threading.Thread(target=run, name=f"lumeri-runtime-{task_id}", daemon=True)
    _update_runtime_task(task_id, thread=thread)
    thread.start()
    return {
        "status": "accepted",
        "session_id": session_id,
        "project_id": project_id,
        "task_id": task_id,
        "events": service.sessions.read_events(session_id),  # type: ignore[attr-defined]
    }


def _creative_sandbox_error_response(handler: BaseHTTPRequestHandler, exc: Exception) -> None:
    from gemia.creative_sandbox import creative_sandbox_error_payload

    status, payload = creative_sandbox_error_payload(exc)
    _json_response(handler, status, payload)


def _opencode_compat_service():
    from gemia.opencode_compat import OpenCodeCompatService

    return OpenCodeCompatService(_BASE_DIR)


def _opencode_compat_path(path: str) -> str | None:
    if path.startswith("/runtime/opencode"):
        trimmed = path.removeprefix("/runtime/opencode") or "/"
        return trimmed if trimmed.startswith("/") else f"/{trimmed}"
    if path in {"/event", "/global/event", "/session", "/project", "/project/current", "/file", "/file/content", "/file/status", "/find", "/find/file"}:
        return path
    if path.startswith("/session/"):
        return path
    return None


def _opencode_compat_error_response(handler: BaseHTTPRequestHandler, exc: Exception) -> None:
    from gemia.opencode_compat import opencode_error_payload

    status, payload = opencode_error_payload(exc)
    _json_response(handler, status, payload)


def _sse_response(handler: BaseHTTPRequestHandler, status: int, body: str) -> None:
    data = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Connection", "close")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _opencode_create_session(payload: dict, *, account_id: str | None) -> dict:
    service = _runtime_service()
    result = service.create_session(payload, account_id=account_id)
    return _opencode_compat_service().session_payload(str(result.get("session_id") or payload.get("session_id") or ""))


def _opencode_shell(session_id: str, payload: dict) -> dict:
    command = str(payload.get("command") or payload.get("message") or "").strip()
    if not command:
        from gemia.opencode_compat import OpenCodeCompatError

        raise OpenCodeCompatError("empty_command", "Shell command is required")
    try:
        args = shlex.split(command)
    except ValueError as exc:
        from gemia.opencode_compat import OpenCodeCompatError

        raise OpenCodeCompatError("invalid_command", str(exc)) from exc
    if not args:
        from gemia.opencode_compat import OpenCodeCompatError

        raise OpenCodeCompatError("empty_command", "Shell command is required")

    service = _creative_sandbox_service()
    service.create_workspace({"session_id": session_id, "goal": "opencode shell"}, account_id=accounts.current_account_id())
    command_id = f"cmd_{uuid.uuid4().hex[:12]}"
    service.append_event(
        session_id,
        "dev_command_started",
        {"command_id": command_id, "command": command[:240], "label": "opencode.shell", "executed": True},
    )
    from gemia.creative_sandbox_runner import CreativeSandboxRunner

    runner = CreativeSandboxRunner(_BASE_DIR, session_id=session_id)
    result = runner.run(
        args,
        cwd=payload.get("cwd"),
        timeout_sec=float(payload.get("timeout_sec") or payload.get("timeoutSec") or 30),
        declared_artifact_paths=payload.get("declared_artifact_paths") or (),
        command_id=command_id,
    ).to_dict()
    service.append_event(
        session_id,
        "dev_command_finished",
        {
            "command_id": result.get("command_id"),
            "command": command[:240],
            "status": result.get("status"),
            "exit_code": result.get("exit_code"),
            "duration_ms": result.get("duration_ms"),
            "stdout_tail": result.get("stdout_tail"),
            "stderr_tail": result.get("stderr_tail"),
            "artifact_count": len(result.get("artifacts") or []),
            "executed": True,
        },
    )
    for artifact in result.get("artifacts") or []:
        service.append_event(
            session_id,
            "dev_artifact_ready",
            {
                "path": artifact.get("rel_path") or artifact.get("path"),
                "size": artifact.get("size"),
                "declared": artifact.get("declared"),
                "command_id": result.get("command_id"),
            },
        )
    compat = _opencode_compat_service()
    message = compat.shell_message(session_id, command, result)
    preview = _creative_sandbox_preview_payload(service, session_id)
    return {
        **message,
        "status": "succeeded" if result.get("status") == "succeeded" else result.get("status"),
        "events": service.read_events(session_id),
        "artifacts": service.list_artifacts(session_id).get("artifacts", []),
        "preview": preview,
        "report": service.report(session_id),
    }


def _creative_sandbox_preview_payload(service, session_id: str) -> dict:
    payload = service.latest_preview(session_id)
    preview = payload.get("preview")
    if isinstance(preview, dict):
        path = str(preview.get("path") or "")
        kind = str(preview.get("kind") or "previews")
        rel_path = path.split("/", 1)[1] if path.startswith(f"{kind}/") else path
        payload["raw_url"] = (
            f"/runtime/dev/workspace/{quote(session_id)}/files"
            f"?raw=1&kind={quote(kind)}&path={quote(rel_path)}"
        )
    return payload


def _web_asset_path(rel: str) -> Path | None:
    root = _WEB_ASSETS_DIR.resolve()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _safe_child_path(root: Path, rel: str) -> Path | None:
    """Return a resolved file below root, or None when rel escapes root."""
    try:
        if "\x00" in rel:
            return None
        resolved_root = Path(root).resolve()
        candidate = (resolved_root / rel.lstrip("/")).resolve()
        candidate.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    return candidate if candidate.exists() and candidate.is_file() else None


def _task_file(task_id: str) -> Path:
    return _TASKS_DIR / f"{task_id}.json"


def _plan_file_for_task(task_id: str) -> Path:
    return _PLANS_DIR / f"{task_id}_plan.json"


def _load_task_payload(task_id: str) -> dict:
    path = _task_file(task_id)
    if not path.exists():
        raise FileNotFoundError(f"task not found: {task_id}")
    return json.loads(path.read_text())


def _load_plan_payload(task_id: str) -> dict:
    path = _plan_file_for_task(task_id)
    if not path.exists():
        raise FileNotFoundError(f"plan not found for task: {task_id}")
    return json.loads(path.read_text())


def _write_task_payload(payload: dict) -> str:
    _TASKS_DIR.mkdir(parents=True, exist_ok=True)
    task_id = str(payload.get("task_id") or f"task_{uuid.uuid4().hex[:12]}")
    payload["task_id"] = task_id
    payload.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    payload = _normalize_task_contract(payload)
    with _task_write_lock:
        _task_file(task_id).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return task_id


def _unique_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for path in paths:
        text = str(path or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique


def _redact_live_log_text(value: object) -> str:
    text = str(value or "")
    if not text:
        return ""
    text = _LIVE_LOG_SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", text)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]{8,}", r"\1[redacted]", text)
    text = re.sub(r"(?i)(sk-[A-Za-z0-9._\-]{8,})", "[redacted]", text)
    text = re.sub(r"(?i)(GOCSPX-[A-Za-z0-9._\-]{8,})", "[redacted]", text)
    return text


def _redact_live_log_value(value):
    if isinstance(value, dict):
        redacted: dict = {}
        for key, item in value.items():
            key_text = str(key)
            if re.search(r"(?i)(authorization|api[_-]?key|token|secret|password)", key_text):
                redacted[key_text] = "[redacted]" if item else ""
            else:
                redacted[key_text] = _redact_live_log_value(item)
        return redacted
    if isinstance(value, list):
        return [_redact_live_log_value(item) for item in value]
    if isinstance(value, str):
        return _redact_live_log_text(value)
    return value


def _execution_log_message(event: dict) -> str:
    for key in ("body", "meta", "detail", "label"):
        text = _redact_live_log_text(event.get(key))
        if text:
            return text
    return ""


def _execution_log_from_event(event: dict, index: int) -> dict:
    raw = _redact_live_log_value(event)
    return {
        "id": str(event.get("id") or f"log_evt_{index:04d}"),
        "index": index,
        "timestamp": str(event.get("created_at") or datetime.now(timezone.utc).isoformat()),
        "source": str(event.get("voice") or "lumeri"),
        "phase": str(event.get("phase") or "event"),
        "status": str(event.get("status") or "running"),
        "label": _redact_live_log_text(event.get("label")),
        "message": _execution_log_message(event),
        "detail": _redact_live_log_text(event.get("detail")),
        "meta": _redact_live_log_text(event.get("meta")),
        "command": _redact_live_log_text(event.get("command")),
        "outputs": _redact_live_log_value(event.get("outputs") or ([] if not event.get("output") else [event.get("output")])),
        "stats": _redact_live_log_value(event.get("stats") or {}),
        "raw": raw,
    }


def _execution_logs_for_task(task: dict, *, progress: dict | None = None) -> list[dict]:
    """Build complete UI-facing execution logs from task events and live progress."""
    events = task.get("agent_events") if isinstance(task.get("agent_events"), list) else []
    logs = [
        _execution_log_from_event(event, index)
        for index, event in enumerate(events, start=1)
        if isinstance(event, dict)
    ]
    task_id = str(task.get("task_id") or "")
    if progress:
        current = progress.get("current_step", 0)
        total = progress.get("total_steps", 0)
        function_name = str(progress.get("current_function") or "")
        logs.append(
            {
                "id": f"log_progress_{task_id or 'task'}",
                "index": len(logs) + 1,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "lumeri",
                "phase": "progress",
                "status": "running",
                "label": "执行进度",
                "message": f"步骤 {current}/{total}: {function_name}".strip(),
                "detail": "",
                "meta": "live progress",
                "command": _redact_live_log_text(function_name),
                "outputs": [],
                "stats": {"current_step": current, "total_steps": total},
                "raw": _redact_live_log_value(progress),
            }
        )
    return logs


def _logs_payload_for_task(task_id: str) -> dict:
    task = _normalize_task_contract(_load_task_payload(task_id))
    logs = _execution_logs_for_task(task, progress=_task_progress.get(task_id))
    return {
        "task_id": task_id,
        "status": task.get("status", "unknown"),
        "logs": logs,
        "execution_logs": logs,
    }


def _task_agent_report(task: dict) -> dict:
    """Build a compact UI-facing run report for the active Creative Runtime panel."""
    status = str(task.get("status") or "unknown")
    outputs = [str(item) for item in task.get("outputs") or [] if str(item or "").strip()]
    artifacts = [str(item) for item in task.get("artifact_outputs") or [] if str(item or "").strip()]
    render_passes = [item for item in task.get("render_passes") or [] if isinstance(item, dict)]
    logs = [item for item in task.get("execution_logs") or [] if isinstance(item, dict)]
    failures = [
        item
        for item in logs
        if str(item.get("status") or "").lower() in {"failed", "error", "blocked"}
        or str(item.get("phase") or "").lower() == "error"
    ]
    primary_path = outputs[0] if outputs else artifacts[0] if artifacts else ""
    if not primary_path:
        for render_pass in reversed(render_passes):
            candidate = str(
                render_pass.get("preview_path")
                or render_pass.get("output_path")
                or render_pass.get("artifact_path")
                or ""
            ).strip()
            if candidate:
                primary_path = candidate
                break

    if status in {"succeeded", "preview_ready"} and primary_path:
        state = "preview_ready" if outputs else "artifact_ready"
        title = "Preview ready" if outputs else "Artifact ready"
        summary = f"Run completed with {len(logs)} audited log entries."
        next_action = "Review the preview and send focused feedback if needed."
    elif status == "artifact_ready":
        state = "artifact_ready"
        title = "Artifact ready"
        summary = f"Run completed with {len(artifacts)} artifact file(s)."
        next_action = "Open the artifact or ask for the next render pass."
    elif status in {"failed", "error"}:
        state = "failed"
        title = "Run failed"
        summary = f"{len(failures) or 1} failure point(s) captured for diagnosis."
        next_action = "Inspect the failed log entry and revise the prompt or script."
    elif status in {"needs_input", "asking"}:
        state = "needs_input"
        title = "Needs input"
        summary = "The agent needs one more decision before it can continue."
        next_action = "Answer the pending question to resume the run."
    elif status in {"running", "planning", "executing"}:
        state = "running"
        title = "Run in progress"
        summary = f"{len(logs)} log entries captured so far."
        next_action = "Wait for the terminal event or inspect live logs."
    else:
        state = "unknown"
        title = "Run report"
        summary = "No preview or artifact has been produced yet."
        next_action = "Run a prompt or script to produce a reviewable artifact."

    return {
        "brief": {
            "state": state,
            "title": title,
            "summary": summary,
            "primary_path": primary_path,
            "next_action": next_action,
        },
        "summary": {
            "status": status,
            "log_count": len(logs),
            "failure_count": len(failures),
            "output_count": len(outputs),
            "artifact_count": len(artifacts),
            "render_pass_count": len(render_passes),
        },
    }


def _normalize_task_outputs(payload: dict) -> dict:
    """Keep task.outputs as playable media only and preserve documents separately."""
    task = dict(payload)
    render_output_paths = _output_paths(task.get("render_passes") or [])
    all_outputs = _unique_paths(
        _output_paths(task.get("all_outputs") or [])
        + _output_paths(task.get("outputs") or [])
        + _output_paths(task.get("artifact_outputs") or [])
        + render_output_paths
    )
    media: list[str] = []
    artifacts: list[str] = []
    qc_rows: list[dict[str, object]] = []
    for path in all_outputs:
        qc = _output_qc(path)
        qc_rows.append(qc)
        if qc.get("is_media") and qc.get("ok"):
            media.append(path)
        elif qc.get("is_artifact"):
            artifacts.append(path)
    media = _unique_paths(media)
    artifacts = _unique_paths(artifacts)
    task["outputs"] = media
    task["artifact_outputs"] = artifacts
    task["all_outputs"] = _unique_paths(media + artifacts)
    task["output_qc"] = qc_rows

    normalized_passes: list[dict] = []
    for item in task.get("render_passes") or []:
        if not isinstance(item, dict):
            continue
        render_pass = dict(item)
        preview_path = str(render_pass.get("preview_path") or "").strip()
        output_path = str(render_pass.get("output_path") or "").strip()
        artifact_path = str(render_pass.get("artifact_path") or "").strip()
        if preview_path and _is_media_output(preview_path) and not _output_qc(preview_path).get("ok"):
            render_pass["preview_path"] = ""
            if str(render_pass.get("status") or "") == "succeeded":
                render_pass["status"] = "missing_output"
        if output_path and _is_media_output(output_path) and not _output_qc(output_path).get("ok"):
            if str(render_pass.get("status") or "") == "succeeded":
                render_pass["status"] = "missing_output"
        if preview_path and not _is_media_output(preview_path):
            artifact_path = artifact_path or preview_path
            render_pass["preview_path"] = ""
            if str(render_pass.get("status") or "") == "succeeded":
                render_pass["status"] = "artifact_ready"
        if output_path and not _is_media_output(output_path):
            artifact_path = artifact_path or output_path
            if str(render_pass.get("status") or "") == "succeeded":
                render_pass["status"] = "artifact_ready"
        if artifact_path:
            render_pass["artifact_path"] = artifact_path
            if not str(render_pass.get("kind") or "").strip():
                render_pass["kind"] = "document_artifact"
        normalized_passes.append(render_pass)
    if normalized_passes:
        task["render_passes"] = normalized_passes
    return task


def _output_qc(path: object) -> dict[str, object]:
    text = str(path or "").strip()
    qc: dict[str, object] = {
        "path": text,
        "ok": False,
        "is_media": _is_media_output(text),
        "is_artifact": _is_document_artifact_output(text),
        "reason": "",
        "strict": False,
    }
    if not text:
        qc["reason"] = "empty_path"
        return qc
    lowered = text.lower()
    if lowered.startswith(("http://", "https://")):
        qc["reason"] = "remote_url_not_local_output"
        return qc
    if qc["is_artifact"]:
        qc["ok"] = True
        qc["reason"] = "artifact"
        return qc
    if not qc["is_media"]:
        qc["reason"] = "unsupported_output_type"
        return qc

    path_obj = Path(text).expanduser()
    strict = _requires_strict_media_qc(path_obj)
    qc["strict"] = strict
    if strict and (not path_obj.exists() or not path_obj.is_file()):
        qc["reason"] = "missing_media_file"
        return qc
    if not strict:
        # Unit tests often use synthetic paths outside the real Lumeri output
        # tree. Product outputs live under _BASE_DIR/outputs and are probed
        # strictly below.
        qc["ok"] = True
        qc["reason"] = "non_product_path_light_check"
        return qc
    if _is_video_output(text):
        ok, reason = _ffprobe_video_ok(path_obj)
        qc["ok"] = ok
        qc["reason"] = reason
        return qc
    qc["ok"] = True
    qc["reason"] = "existing_media_file"
    return qc


def _requires_strict_media_qc(path: Path) -> bool:
    if str(os.environ.get("LUMERI_STRICT_MEDIA_QC") or os.environ.get("GEMIA_STRICT_MEDIA_QC") or "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return True
    try:
        path.resolve().relative_to((_BASE_DIR / "outputs").resolve())
        return True
    except Exception:
        return False


def _ffprobe_video_ok(path: Path) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_type,width,height:format=duration",
                "-of",
                "json",
                str(path),
            ],
            text=True,
            capture_output=True,
            timeout=8,
        )
    except FileNotFoundError:
        return path.exists() and path.stat().st_size > 0, "ffprobe_unavailable_size_check"
    except Exception as exc:
        return False, f"ffprobe_error:{exc.__class__.__name__}"
    if proc.returncode != 0:
        return False, "ffprobe_failed"
    try:
        payload = json.loads(proc.stdout or "{}")
    except Exception:
        return False, "ffprobe_invalid_json"
    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    has_video = any(isinstance(item, dict) and item.get("codec_type") == "video" for item in streams)
    try:
        duration = float((payload.get("format") or {}).get("duration") or 0.0)
    except Exception:
        duration = 0.0
    if not has_video:
        return False, "no_video_stream"
    if duration <= 0:
        return False, "non_positive_duration"
    return True, "ffprobe_video_ok"


def _normalize_task_contract(payload: dict) -> dict:
    """Normalize persisted task payloads to the Stability Gate v1 contract."""
    task = _normalize_task_outputs(payload)
    status = _normalize_task_status(task.get("status"))
    if task.get("pending_ask"):
        status = "needs_input"
    outputs = task.get("outputs") if isinstance(task.get("outputs"), list) else []
    artifacts = task.get("artifact_outputs") if isinstance(task.get("artifact_outputs"), list) else []
    render_passes = task.get("render_passes") if isinstance(task.get("render_passes"), list) else []
    invalid_media_qc = [
        item
        for item in task.get("output_qc") or []
        if isinstance(item, dict) and item.get("is_media") and not item.get("ok")
    ]
    valid_media_set = {str(path) for path in outputs}
    has_media_preview = any(
        str(item.get("preview_path") or item.get("output_path") or "") in valid_media_set
        for item in render_passes
        if isinstance(item, dict)
    )

    if status == "succeeded" and invalid_media_qc:
        events = list(task.get("agent_events") if isinstance(task.get("agent_events"), list) else [])
        if not events or str(events[-1].get("phase") or "") != "error":
            detail = "; ".join(
                f"{Path(str(item.get('path') or '')).name or item.get('path')}: {item.get('reason')}"
                for item in invalid_media_qc[:4]
            )
            events.append(
                _stability_error_event(
                    f"task produced invalid media output: {detail}",
                    label="输出质检没有通过",
                    context="task_contract.output_qc",
                )
            )
        task["agent_events"] = events

    if status == "succeeded" and not outputs:
        if has_media_preview:
            status = "preview_ready"
        elif artifacts:
            status = "artifact_ready"
        else:
            status = "failed"
            events = list(task.get("agent_events") if isinstance(task.get("agent_events"), list) else [])
            if not events or str(events[-1].get("phase") or "") != "error":
                events.append(
                    _stability_error_event(
                        "succeeded task did not produce media or artifact output",
                        label="没有生成可用产物",
                        context="task_contract.no_output",
                    )
                )
            task["agent_events"] = events

    if status not in TASK_STATUSES:
        status = "failed"
    task["status"] = status
    if status == "failed":
        task.setdefault("outputs", [])
    valid_output_set = {str(path) for path in outputs}
    if status in {"succeeded", "preview_ready"} and valid_output_set:
        task["timeline_updates"] = [
            update
            for update in (task.get("timeline_updates") if isinstance(task.get("timeline_updates"), list) else [])
            if isinstance(update, dict)
            and str(update.get("mode") or "") == "replace_clip_media"
            and str(update.get("output_path") or update.get("preview_path") or "") in valid_output_set
        ]
    else:
        task["timeline_updates"] = []
    task["execution_logs"] = _execution_logs_for_task(
        task,
        progress=_task_progress.get(str(task.get("task_id") or "")),
    )
    task["agent_report"] = _task_agent_report(task)
    return task


def _write_agent_workflow_task(
    *,
    prompt: str,
    result: dict,
    events: list[dict],
    status: str | None = None,
    project_state: dict | None = None,
    task_id: str | None = None,
    created_at: str | None = None,
) -> str:
    all_outputs = _unique_paths(
        _output_paths(result.get("all_outputs") or [])
        + _output_paths(result.get("outputs") or [])
        + _output_paths(result.get("artifact_outputs") or [])
    )
    outputs = _unique_paths(_media_outputs(all_outputs))
    artifact_outputs = _unique_paths(_artifact_outputs(all_outputs))
    timeline_updates = _timeline_updates_for_outputs(
        outputs=outputs,
        result=result,
        project_state=project_state,
    )
    task_status = status or str(result.get("status") or "succeeded")
    payload = {
        "task_id": task_id or result.get("task_id"),
        "status": task_status,
        "goal": result.get("goal") or prompt,
        "prompt": prompt,
        "outputs": outputs,
        "artifact_outputs": artifact_outputs,
        "all_outputs": _unique_paths(outputs + artifact_outputs),
        "execution_mode": result.get("execution_mode") or "agent_loop",
        "agent_events": events,
        "materials": result.get("materials") or [],
        "targets": result.get("targets") or [],
        "agent_plan": result.get("agent_plan") or [],
        "child_tasks": result.get("child_tasks") or [],
        "creative_mode": result.get("creative_mode") or "timeline_guided",
        "reference_assets": result.get("reference_assets") or [],
        "layer_plan": result.get("layer_plan") or {},
        "render_passes": result.get("render_passes") or [],
        "review_notes": result.get("review_notes") or [],
        "human_feedback": result.get("human_feedback") or [],
        "timeline_updates": timeline_updates,
    }
    if created_at:
        payload["created_at"] = created_at
    if project_state is not None:
        payload["project_state"] = project_state
    if result.get("pending_ask"):
        payload["pending_ask"] = result.get("pending_ask")
    return _write_task_payload(_normalize_task_contract(payload))


def _timeline_updates_for_outputs(
    *,
    outputs: list[str],
    result: dict,
    project_state: dict | None,
) -> list[dict]:
    """Describe which timeline clips should be replaced by completed media outputs."""
    media_outputs = _unique_paths(_media_outputs(outputs))
    if not media_outputs:
        return []

    targets = result.get("targets") if isinstance(result.get("targets"), list) else []
    usable_targets = [
        target for target in targets
        if isinstance(target, dict) and str(target.get("clip_id") or "").strip()
    ]

    pairs: list[tuple[dict, str]] = []
    if usable_targets and len(usable_targets) == len(media_outputs):
        pairs = list(zip(usable_targets, media_outputs))
    elif len(usable_targets) == 1 and len(media_outputs) == 1:
        pairs = [(usable_targets[0], media_outputs[0])]
    elif not usable_targets and len(media_outputs) == 1 and isinstance(project_state, dict):
        clip_id = str(project_state.get("selectedClipId") or "").strip()
        clips = project_state.get("clips") if isinstance(project_state.get("clips"), list) else []
        selected_clip = next(
            (clip for clip in clips if isinstance(clip, dict) and str(clip.get("id") or "") == clip_id),
            None,
        )
        if selected_clip:
            pairs = [(selected_clip, media_outputs[0])]

    updates: list[dict] = []
    for target, output in pairs:
        output_text = str(output or "").strip()
        clip_id = str(target.get("clip_id") or target.get("id") or "").strip()
        if not output_text or not clip_id:
            continue
        updates.append(
            {
                "mode": "replace_clip_media",
                "clip_id": clip_id,
                "asset_id": str(target.get("asset_id") or target.get("assetId") or "").strip(),
                "material_id": str(target.get("material_id") or "").strip(),
                "output_path": output_text,
                "preview_path": output_text,
                "media_kind": "video",
                "mime_type": "video/mp4",
                "name": Path(output_text).name,
            }
        )
    return updates


def _write_failed_workflow_task(
    *,
    prompt: str,
    exc: Exception | str,
    events: list[dict] | None = None,
    project_state: dict | None = None,
    context: str = "",
    task_id: str | None = None,
    created_at: str | None = None,
) -> str:
    event_list = list(events or [])
    event_list.append(_stability_error_event(exc, label="执行没有完成", context=context))
    return _write_agent_workflow_task(
        prompt=prompt,
        result={"goal": prompt, "outputs": [], "artifact_outputs": [], "all_outputs": []},
        events=event_list,
        status="failed",
        project_state=project_state,
        task_id=task_id,
        created_at=created_at,
    )


def _write_live_agent_task_snapshot(
    *,
    task_id: str,
    created_at: str,
    prompt: str,
    events: list[dict],
    status: str = "running",
    project_state: dict | None = None,
    result: dict | None = None,
    pending_ask: dict | None = None,
) -> None:
    result = result if isinstance(result, dict) else {}
    payload = {
        "task_id": task_id,
        "created_at": created_at,
        "status": status,
        "goal": result.get("goal") or prompt,
        "prompt": prompt,
        "outputs": result.get("outputs") or [],
        "artifact_outputs": result.get("artifact_outputs") or [],
        "all_outputs": result.get("all_outputs") or [],
        "execution_mode": result.get("execution_mode") or "agent_loop",
        "agent_events": events,
        "materials": result.get("materials") or [],
        "targets": result.get("targets") or [],
        "agent_plan": result.get("agent_plan") or [],
        "child_tasks": result.get("child_tasks") or [],
        "creative_mode": result.get("creative_mode") or "timeline_guided",
        "reference_assets": result.get("reference_assets") or [],
        "layer_plan": result.get("layer_plan") or {},
        "render_passes": result.get("render_passes") or [],
        "review_notes": result.get("review_notes") or [],
        "human_feedback": result.get("human_feedback") or [],
    }
    if project_state is not None:
        payload["project_state"] = project_state
    if pending_ask:
        payload["pending_ask"] = pending_ask
    _write_task_payload(_normalize_task_contract(payload))


def _run_agent_workflow_live_task(
    *,
    task_id: str,
    created_at: str,
    prompt: str,
    video: str,
    project_state: dict | None,
    account_id: str,
    execution_scope: str,
    agent: str | None,
) -> None:
    events: list[dict] = []

    def publish(status: str = "running", result: dict | None = None, pending_ask: dict | None = None) -> None:
        _write_live_agent_task_snapshot(
            task_id=task_id,
            created_at=created_at,
            prompt=prompt,
            events=events,
            status=status,
            project_state=project_state,
            result=result,
            pending_ask=pending_ask,
        )

    def on_event(event: dict) -> None:
        events.append(event)
        publish("running")

    publish("planning")
    try:
        result = run_agent_workflow(
            GemiaOrchestrator(),
            prompt=prompt,
            input_path=video or None,
            project_state=project_state,
            account_id=account_id,
            scope=execution_scope,
            agent=agent,
            event_callback=on_event,
        )
    except Exception as exc:
        _write_failed_workflow_task(
            prompt=prompt,
            events=events,
            project_state=project_state,
            exc=exc,
            context="/run-prompt.agent_workflow.live",
            task_id=task_id,
            created_at=created_at,
        )
        _task_progress.pop(task_id, None)
        return

    if result.get("ask"):
        import time as _time
        ask_id = uuid.uuid4().hex[:12]
        session = result.get("_pending_ask_session") if isinstance(result.get("_pending_ask_session"), dict) else {}
        pending_ask = {
            "ask_id": ask_id,
            "questions": result.get("questions") or (result.get("pending_ask") or {}).get("questions", []),
        }
        _pending_asks[ask_id] = {
            **session,
            "prompt": prompt,
            "video": session.get("video") or video,
            "project_state": session.get("project_state") if isinstance(session.get("project_state"), dict) else project_state,
            "agent": session.get("agent") or agent,
            "execution_scope": session.get("execution_scope") or execution_scope,
            "execution_mode": "agent_loop",
            "agent_events": events,
            "stream_logs": True,
            "ask_rounds": 1,
            "account_id": account_id,
            "created_at_ts": _time.time(),
        }
        publish("needs_input", result=result, pending_ask=pending_ask)
        _task_progress.pop(task_id, None)
        return

    _write_agent_workflow_task(
        prompt=prompt,
        result=result,
        events=events,
        project_state=project_state,
        task_id=task_id,
        created_at=created_at,
    )
    _task_progress.pop(task_id, None)


def _stop_repeated_ask_event(questions: list | None = None) -> dict:
    texts: list[str] = []
    for item in questions or []:
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("label") or item.get("id") or "").strip()
        else:
            text = str(item or "").strip()
        if text:
            texts.append(text)
    question_text = " / ".join(texts)
    detail = question_text or "planner requested another clarification"
    envelope = _stability_error_envelope(
        f"repeated ask blocked: {detail}",
        context="answer_ask.repeated",
        recoverable=True,
    )
    envelope["error_code"] = "E_REPEATED_ASK"
    envelope["user_message"] = "我没有继续反复追问。这类请求最多确认一轮，剩下的参数会按默认值推进或给出清晰失败原因。"
    envelope["error"] = envelope["user_message"]
    envelope["next_action"] = "把剩余关键约束写进下一条，或让系统按默认值继续。"
    return {
        "id": f"evt_{uuid.uuid4().hex[:10]}",
        "phase": "error",
        "label": "停止反复确认",
        "detail": envelope["debug_id"],
        "status": "failed",
        "body": envelope["user_message"],
        "voice": "gemini",
        "created_at": datetime.now(timezone.utc).isoformat(),
        **envelope,
    }


def _task_assets_payload(task_id: str) -> dict:
    task = _normalize_task_contract(_load_task_payload(task_id))
    assets = []
    for output in task.get("all_outputs", []):
        p = Path(str(output))
        try:
            rel = p.relative_to(_BASE_DIR)
            serve_path = str(rel)
        except ValueError:
            serve_path = str(p)
        assets.append({
            "path": serve_path,
            "abs_path": str(p),
            "exists": p.exists(),
            "size_bytes": p.stat().st_size if p.exists() else None,
            "kind": p.suffix.lower().lstrip("."),
            "is_media": _is_media_output(str(p)),
        })
    return {"task_id": task_id, "assets": assets}


def _health_payload() -> dict:
    checks: list[dict[str, object]] = []

    def add(name: str, ok: bool, detail: str = "", *, required: bool = True, extra: dict | None = None) -> None:
        item: dict[str, object] = {"name": name, "ok": bool(ok), "required": bool(required)}
        if detail:
            item["detail"] = detail
        if extra:
            item.update(extra)
        checks.append(item)

    add("config.openrouter", _has_valid_key(), "OpenRouter key present" if _has_valid_key() else "OpenRouter key missing", required=False)
    add("config.image", _has_valid_image_key(), "image provider key present" if _has_valid_image_key() else "image provider key missing", required=False)
    for name, path in (("outputs_dir", _BASE_DIR / "outputs"), ("tasks_dir", _TASKS_DIR)):
        add(name, _dir_is_writable(path), str(path))

    contract_sample = _normalize_task_contract({"status": "succeeded", "outputs": ["demo.md"]})
    add(
        "media_artifact_contract",
        contract_sample.get("status") == "artifact_ready"
        and contract_sample.get("outputs") == []
        and contract_sample.get("artifact_outputs") == ["demo.md"],
        "documents stay out of video outputs",
    )

    frontend_ok, frontend_detail = _frontend_dist_health()
    add("frontend_dist", frontend_ok, frontend_detail)

    session_ok, session_detail = _session_health()
    add("session_video_refs", session_ok, session_detail, required=False)

    try:
        from gemia.video.blender_link import blender_link_status

        blender = blender_link_status()
        add(
            "blender_lumerilink",
            bool(blender.get("available")),
            str(blender.get("blender_path") or "Blender not found; local fallback remains available"),
            required=False,
            extra={"available": bool(blender.get("available"))},
        )
    except Exception as exc:
        add("blender_lumerilink", False, _human_error_message(exc), required=False)

    input_log_dir = Path.home() / "Desktop" / "Lumeri Gemini Inputs"
    add(
        "model_input_observability",
        bool(os.environ.get("GEMIA_INPUT_TXT_LOG")) or input_log_dir.exists(),
        str(input_log_dir),
        required=False,
    )
    add("stability_gate", _stability_gate_enabled(), "stable-first capability gate")
    ok = all(item["ok"] for item in checks if item.get("required", True))
    return {
        "ok": ok,
        "status": "ok" if ok else "degraded",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "task_statuses": sorted(TASK_STATUSES),
        "checks": checks,
    }


def _dir_is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".lumeri-health-{uuid.uuid4().hex[:8]}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def _frontend_dist_health() -> tuple[bool, str]:
    index_path = _web_index_path()
    if not index_path.exists():
        return False, f"missing index: {index_path}"
    try:
        dist_js = "\n".join(path.read_text(encoding="utf-8") for path in _WEB_ASSETS_DIR.glob("index-*.js"))
    except Exception as exc:
        return False, str(exc)
    required = ("mp4|mov|m4v|webm", "user_message", "artifact_ready", "preview_ready")
    missing = [item for item in required if item not in dist_js]
    if missing:
        return False, "missing frontend stability markers: " + ", ".join(missing)
    return True, str(index_path)


def _session_health() -> tuple[bool, str]:
    account_id = accounts.current_account_id()
    if not account_id:
        return True, "no active account session"
    try:
        from gemia.session_history import load_current_session

        payload = load_current_session(account_id=account_id)
    except Exception as exc:
        return False, _human_error_message(exc)
    bad_paths: list[str] = []

    def visit(value: object, key: str = "") -> None:
        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                visit(nested_value, str(nested_key))
        elif isinstance(value, list):
            for nested in value:
                visit(nested, key)
        elif key in {"video_src", "server_video_path", "serverVideoPath", "previewSrc", "serverPath"}:
            text = str(value or "")
            if text and not _is_session_playable_media_ref(text):
                bad_paths.append(text)

    visit(payload)
    if bad_paths:
        return False, f"non-media video refs: {bad_paths[:3]}"
    return True, "session video refs are media-only"


def _is_session_playable_media_ref(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    if _is_media_output(text):
        return True
    try:
        parsed = urlparse(text)
    except ValueError:
        parsed = None
    path = unquote(parsed.path if parsed and parsed.scheme in {"http", "https"} else text)
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 4 and parts[0] == "media-library" and parts[1] == "file":
        return parts[3] == "original" or parts[3] == "cache"
    return False


def _is_agent_workflow_request(payload: dict) -> bool:
    raw = payload.get("execution_scope", payload.get("executionScope"))
    if raw is None:
        return False
    return str(raw).strip().lower() not in {"", "0", "false", "off", "legacy"}


def _goal_for_task(task_id: str) -> str | None:
    try:
        plan = _load_plan_payload(task_id)
        return plan.get("goal")
    except Exception:
        return None


def _style_from_goal(goal: str | None) -> str | None:
    if not goal:
        return None
    marker = "with style:"
    lower = goal.lower()
    idx = lower.find(marker)
    if idx == -1:
        return None
    style = goal[idx + len(marker):].strip()
    return style or None


def _append_revision(task_id: str, revision: dict) -> dict:
    path = _task_file(task_id)
    payload = _load_task_payload(task_id)
    revisions = payload.get("revisions", [])
    revisions.append(revision)
    payload["revisions"] = revisions
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def _append_human_feedback(task_id: str, feedback: dict) -> tuple[dict, dict, dict]:
    path = _task_file(task_id)
    payload = _load_task_payload(task_id)
    feedbacks = payload.get("human_feedback")
    if not isinstance(feedbacks, list):
        feedbacks = []
    entry = {
        "feedback_id": str(feedback.get("feedback_id") or f"feedback_{uuid.uuid4().hex[:10]}"),
        "feedback": str(feedback.get("feedback") or "").strip(),
        "render_pass_id": str(feedback.get("render_pass_id") or ""),
        "layer_id": str(feedback.get("layer_id") or ""),
        "time_range": feedback.get("time_range"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    feedbacks.append(entry)
    payload["human_feedback"] = feedbacks
    events = payload.get("agent_events")
    if not isinstance(events, list):
        events = []
    revision_pass = _create_local_feedback_revision(payload, entry)
    revision_layer_ids = [
        str(layer_id).strip()
        for layer_id in (revision_pass or {}).get("layer_ids", [])
        if str(layer_id).strip()
    ]
    event_layer_id = _display_layer_id_label(revision_layer_ids, fallback=entry["layer_id"])
    target_bits = []
    if entry["render_pass_id"]:
        target_bits.append(f"小样 {entry['render_pass_id']}")
    if event_layer_id:
        target_bits.append(f"图层 {event_layer_id}")
    target = " / ".join(target_bits) or "当前结果"
    if revision_pass:
        render_passes = payload.get("render_passes")
        if not isinstance(render_passes, list):
            render_passes = []
        render_passes.append(revision_pass)
        payload["render_passes"] = render_passes
        outputs = _unique_paths(
            _output_paths(payload.get("outputs") or [])
            + _output_paths(payload.get("all_outputs") or [])
            + [str(revision_pass.get("preview_path") or revision_pass.get("output_path") or "")]
        )
        payload["outputs"] = _unique_paths(_media_outputs(outputs))
        payload["all_outputs"] = _unique_paths(outputs)
        reviews = payload.get("review_notes")
        if not isinstance(reviews, list):
            reviews = []
        reviews.append(
            {
                "review_note_id": f"review_{uuid.uuid4().hex[:10]}",
                "render_pass_id": revision_pass["render_pass_id"],
                "verdict": "needs_review",
                "note": "已根据反馈生成本地二次修订小样，请继续检查画面、节奏和局部转场效果。",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        payload["review_notes"] = reviews
    note = {
        "id": f"evt_{uuid.uuid4().hex[:10]}",
        "phase": "revision_plan",
        "label": "局部修改计划",
        "detail": target,
        "status": "succeeded",
        "body": (
            f"我收到这条反馈了：{entry['feedback']}。"
            + (
                f"我已经基于 {target} 生成了一个本地二次修订小样：{Path(str(revision_pass.get('output_path') or revision_pass.get('preview_path'))).name}，请直接检查这个新 render pass。"
                if revision_pass
                else f"下一轮会优先只改 {target} 对应的图层或 render pass，不从头重跑无关部分。"
            )
        ),
        "voice": "gemini",
        "render_pass_id": entry["render_pass_id"],
        "layer_id": event_layer_id,
        "revision_render_pass_id": revision_pass.get("render_pass_id") if revision_pass else "",
        "revision_output_path": revision_pass.get("output_path") if revision_pass else "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    events.append(note)
    payload["agent_events"] = events
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload, entry, note


def _create_local_feedback_revision(task: dict, feedback: dict) -> dict | None:
    """Create a narrow local render-pass revision for common preview feedback."""
    if not _feedback_wants_visual_revision(str(feedback.get("feedback") or "")):
        return None
    render_pass_id = str(feedback.get("render_pass_id") or "")
    render_passes = [item for item in task.get("render_passes") or [] if isinstance(item, dict)]
    source_pass = next((item for item in render_passes if str(item.get("render_pass_id") or "") == render_pass_id), None)
    if source_pass is None and render_passes:
        source_pass = render_passes[-1]
    if source_pass is None:
        return None
    source_output = str(source_pass.get("preview_path") or source_pass.get("output_path") or "").strip()
    if not source_output or not _is_media_output(source_output):
        return None
    if _render_pass_uses_timeline_merge_kept(source_pass):
        return _create_timeline_merge_kept_feedback_revision(task, feedback, source_pass, source_output)
    if _render_pass_uses_timeline_broll(source_pass):
        return _create_timeline_broll_feedback_revision(task, feedback, source_pass, source_output)
    source_manifest = _layer_flow_manifest_path(source_pass, source_output)
    if source_manifest is None:
        return _create_transition_feedback_revision(task, feedback, source_pass, source_output)
    try:
        manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
    except Exception:
        return None
    plan = manifest.get("authored_plan") if isinstance(manifest, dict) else {}
    feedback_text = str(feedback.get("feedback") or "")
    source = Path(source_output).expanduser().resolve()
    suffix = str(feedback.get("feedback_id") or uuid.uuid4().hex[:8]).replace("feedback_", "")[:8]
    output = _compact_revision_output_path(source, "revision", suffix)
    preserved_layer_ids = _render_preserved_layer_flow_feedback_revision(
        output,
        feedback_text=feedback_text,
        plan=plan if isinstance(plan, dict) else {},
    )
    if preserved_layer_ids:
        layer_ids = preserved_layer_ids
    else:
        title = _revision_title_from_feedback_or_plan(feedback_text, plan if isinstance(plan, dict) else {})
        if not title:
            return None
        if _feedback_wants_mg_ball_title_revision(feedback_text, plan if isinstance(plan, dict) else {}):
            layer_ids = _render_mg_ball_title_revision(output, title=title)
        else:
            layer_ids = _render_centered_title_revision(output, title=title)
    manifest_paths = [
        str(path)
        for path in (output.with_suffix(".layer-flow.json"), output.with_suffix(".preview.json"))
        if path.exists()
    ]
    return {
        "render_pass_id": f"pass_{uuid.uuid4().hex[:12]}",
        "kind": "layer_preview_revision",
        "output_path": str(output),
        "preview_path": str(output),
        "status": "succeeded",
        "capabilities": ["layer-render", "local-feedback-revision"],
        "layer_ids": layer_ids,
        "manifest_paths": manifest_paths,
        "source_render_pass_id": str(source_pass.get("render_pass_id") or ""),
        "feedback_id": str(feedback.get("feedback_id") or ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _display_layer_id_label(layer_ids: list[str], *, fallback: str = "") -> str:
    cleaned = [str(layer_id).strip() for layer_id in layer_ids if str(layer_id).strip()]
    if not cleaned:
        return str(fallback or "")
    if len(cleaned) <= 3:
        return ", ".join(cleaned)
    suffix_matches = [re.match(r"^(.+_)(\d+)$", layer_id) for layer_id in cleaned]
    if all(match is not None for match in suffix_matches):
        prefixes = {match.group(1) for match in suffix_matches if match is not None}
        numbers = sorted(int(match.group(2)) for match in suffix_matches if match is not None)
        if len(prefixes) == 1 and numbers == list(range(numbers[0], numbers[-1] + 1)):
            return f"{prefixes.pop()}* ({len(cleaned)} layers)"
    return f"{', '.join(cleaned[:3])}, ... ({len(cleaned)} layers)"


def _explicit_layer_feedback_target(feedback_text: str) -> str:
    lead = re.split(r"[：:。；;\n]", str(feedback_text or ""), maxsplit=1)[0].lower()
    if not lead:
        return ""
    targets = (
        ("baseline_glow", ("baseline_glow", "baseline", "基线光条", "基线", "光条")),
        ("stage_floor", ("stage_floor", "stage floor", "舞台地面", "地面")),
        ("contact_shadow", ("contact_shadow", "contact shadow", "接触阴影")),
        ("text", ("文字", "主文字", "字", "text", "title")),
        ("ball", ("小球", "球", "ball")),
    )
    matches = [target for target, terms in targets if any(term in lead for term in terms)]
    if len(matches) == 1:
        return matches[0]
    return ""


def _render_preserved_layer_flow_feedback_revision(output_path: Path, *, feedback_text: str, plan: dict) -> list[str]:
    """Render a revision by mutating the prior layer-flow plan, preserving canvas specs."""
    if not isinstance(plan, dict):
        return []
    layers = plan.get("layers")
    if not isinstance(layers, list):
        return []
    revised_layers = deepcopy(layers)
    feedback_handlers = {
        "contact_shadow": _apply_contact_shadow_feedback,
        "stage_floor": _apply_stage_floor_feedback,
        "baseline_glow": _apply_baseline_glow_feedback,
        "text": _apply_text_feedback,
        "ball": _apply_ball_endpoint_feedback,
    }
    explicit_target = _explicit_layer_feedback_target(feedback_text)
    if explicit_target:
        changed_ids = feedback_handlers[explicit_target](revised_layers, feedback_text)
    else:
        changed_ids = _apply_contact_shadow_feedback(revised_layers, feedback_text)
        if not changed_ids:
            changed_ids = _apply_stage_floor_feedback(revised_layers, feedback_text)
        if not changed_ids:
            changed_ids = _apply_baseline_glow_feedback(revised_layers, feedback_text)
        if not changed_ids:
            changed_ids = _apply_text_feedback(revised_layers, feedback_text)
        if not changed_ids:
            changed_ids = _apply_ball_endpoint_feedback(revised_layers, feedback_text)
    if not changed_ids:
        return []
    canvas = {
        key: plan.get(key)
        for key in ("width", "height", "fps", "total_frames")
        if plan.get(key) is not None
    }
    if not canvas:
        canvas = {"width": 1280, "height": 720, "fps": 30, "total_frames": 90}
    max_long_edge = max(int(canvas.get("width") or 0), int(canvas.get("height") or 0), 540)
    from gemia.video.layer_flow import render_layer_workflow

    render_layer_workflow(
        "",
        str(output_path),
        canvas=canvas,
        frame_step=1,
        max_long_edge=max_long_edge,
        overlay_layers=revised_layers,
    )
    return changed_ids


def _apply_contact_shadow_feedback(layers: list[dict], feedback_text: str) -> list[str]:
    text = str(feedback_text or "").lower()
    if not any(term in text for term in ("contact_shadow", "contact shadow", "接触阴影")):
        return []
    if not any(
        term in text
        for term in ("颜色", "色", "color", "colour", "#", "black", "黑", "opacity", "不透明度", "透明度")
        + ("柔和", "soft", "soften", "缩放", "scale", "高度", "height", "纵向", "vertical")
        + ("blur", "radius", "高斯模糊", "模糊半径", "模糊", "边缘")
    ):
        return []
    color = _feedback_contact_shadow_color(feedback_text)
    opacity = _feedback_opacity_value(feedback_text)
    height_scale = _feedback_contact_shadow_vertical_scale(feedback_text)
    blur_radius = _feedback_contact_shadow_blur_radius(feedback_text)
    if color is None and opacity is None and height_scale is None and blur_radius is None:
        return []

    candidates = [
        layer
        for layer in layers
        if isinstance(layer, dict) and str(layer.get("id") or "").startswith("contact_shadow_")
    ]
    if not candidates:
        candidates = [
            layer
            for layer in layers
            if isinstance(layer, dict)
            and layer.get("type") == "image"
            and "shadow" in str(layer.get("id") or "").lower()
        ]
    changed: list[str] = []
    for layer in candidates:
        if color is not None:
            rgba = list(color)
            # Image tint alpha would multiply opacity again; keep opacity in the layer/keyframes.
            rgba[3] = 1.0
            layer["color"] = rgba
        if opacity is not None:
            layer["opacity"] = opacity
            _scale_opacity_keyframes_to_peak(layer, opacity)
        if height_scale is not None:
            _scale_contact_shadow_layer_height(layer, height_scale)
        if blur_radius is not None:
            metadata = dict(layer.get("metadata", {}) or {})
            metadata["contact_shadow_blur_radius"] = blur_radius
            metadata["blur_radius"] = blur_radius
            layer["metadata"] = metadata
            layer["blur_radius"] = blur_radius
        changed.append(str(layer.get("id") or "contact_shadow"))
    return changed


def _feedback_contact_shadow_color(feedback_text: str) -> list[float] | None:
    color = _feedback_hex_color(feedback_text)
    if color is not None:
        return color
    lower = str(feedback_text or "").lower()
    if any(term in lower for term in ("柔和黑", "黑色", "black")):
        return [0.0196, 0.0275, 0.0392, 1.0]
    return None


def _scale_opacity_keyframes_to_peak(layer: dict, opacity: float) -> None:
    keyframes = layer.get("keyframes")
    if not isinstance(keyframes, dict):
        return
    opacity_track = keyframes.get("opacity")
    if not isinstance(opacity_track, dict):
        return
    numeric_values = [
        float(value)
        for value in opacity_track.values()
        if isinstance(value, (int, float))
    ]
    peak = max(numeric_values, default=0.0)
    if peak <= 0:
        return
    scale = opacity / peak
    for key, value in list(opacity_track.items()):
        if isinstance(value, (int, float)):
            opacity_track[key] = round(float(value) * scale, 4)


def _feedback_contact_shadow_vertical_scale(feedback_text: str) -> float | None:
    text = str(feedback_text or "")
    patterns = (
        r"(?:纵向高度|高度|vertical\s+height|y[-_\s]?scale|height)[^0-9%]{0,24}(\d+(?:\.\d+)?)(%)?",
        r"(\d+(?:\.\d+)?)(%)?\s*(?:纵向高度|高度|vertical\s+height|y[-_\s]?scale|height)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        raw = float(match.group(1))
        percent_group = match.group(2) if match.lastindex and match.lastindex >= 2 else None
        if percent_group:
            raw /= 100.0
        if raw > 2.0 and raw <= 100.0:
            raw /= 100.0
        return round(float(min(max(raw, 0.1), 3.0)), 4)
    return None


def _feedback_contact_shadow_blur_radius(feedback_text: str) -> float | None:
    text = str(feedback_text or "")
    target_patterns = (
        r"(?:blur\s*radius|blur|高斯模糊|模糊半径|模糊)[^，,。；;\n]{0,80}(?:调到|改到|设为|设置为|变成|到|至|->|=>|to|set(?:\s+to)?|change(?:\s+to)?)[^0-9]{0,16}(\d+(?:\.\d+)?)\s*(?:px|像素)?",
        r"(?:调到|改到|设为|设置为|变成|到|至|->|=>|to|set(?:\s+to)?|change(?:\s+to)?)[^0-9]{0,16}(\d+(?:\.\d+)?)\s*(?:px|像素)?[^，,。；;\n]{0,80}(?:blur\s*radius|blur|高斯模糊|模糊半径|模糊)",
    )
    patterns = (
        r"(?:blur\s*radius|blur|高斯模糊|模糊半径|模糊)[^0-9]{0,32}(\d+(?:\.\d+)?)\s*(?:px|像素)?",
        r"(\d+(?:\.\d+)?)\s*(?:px|像素)\s*(?:的)?\s*(?:blur\s*radius|blur|高斯模糊|模糊半径|模糊)",
    )
    for pattern in target_patterns + patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        try:
            radius = float(match.group(1))
        except (TypeError, ValueError):
            continue
        if radius < 0:
            continue
        return round(float(min(radius, 64.0)), 4)
    return None


def _scale_contact_shadow_layer_height(layer: dict, height_scale: float) -> None:
    raw_size = layer.get("size")
    if not isinstance(raw_size, list) or len(raw_size) < 2:
        return
    try:
        width = int(round(float(raw_size[0])))
        height = int(round(float(raw_size[1])))
    except (TypeError, ValueError):
        return
    layer["size"] = [max(1, width), max(1, int(round(height * height_scale)))]
    metadata = dict(layer.get("metadata", {}) or {})
    metadata["contact_shadow_height_scale"] = height_scale
    layer["metadata"] = metadata


def _apply_text_feedback(layers: list[dict], feedback_text: str) -> list[str]:
    changed_ids = _apply_text_glow_feedback(layers, feedback_text)
    if changed_ids:
        return changed_ids
    return _apply_text_color_feedback(layers, feedback_text)


def _text_feedback_candidates(layers: list[dict]) -> list[dict]:
    candidates = [
        layer
        for layer in layers
        if isinstance(layer, dict)
        and layer.get("type") == "text"
        and (
            str(layer.get("id") or "") == "word_base"
            or str(layer.get("id") or "").startswith("letter_hit_")
        )
    ]
    if candidates:
        return candidates
    return [
        layer
        for layer in layers
        if isinstance(layer, dict) and layer.get("type") == "text"
    ]


def _apply_text_glow_feedback(layers: list[dict], feedback_text: str) -> list[str]:
    text = str(feedback_text or "").lower()
    if not any(term in text for term in ("文字", "字", "text", "title", "主文字", "clean start")):
        return []
    if not any(term in text for term in ("外发光", "发光", "光晕", "aura", "glow", "描边", "outline", "模糊", "blur")):
        return []
    radius = _feedback_text_glow_radius(feedback_text)
    if radius is None:
        return []

    changed: list[str] = []
    for layer in _text_feedback_candidates(layers):
        font_config = layer.get("font_config")
        if not isinstance(font_config, dict):
            font_config = {}
            layer["font_config"] = font_config
        font_config["glow_radius"] = radius
        color = font_config.get("color")
        if isinstance(color, list) and len(color) >= 3:
            glow_color = list(color[:4])
            while len(glow_color) < 4:
                glow_color.append(1.0)
            glow_color[3] = min(float(glow_color[3]), 0.55)
            font_config["glow_color"] = glow_color
        metadata = dict(layer.get("metadata", {}) or {})
        metadata["text_glow_radius"] = radius
        layer["metadata"] = metadata
        changed.append(str(layer.get("id") or layer.get("text") or "text"))
    return changed


def _feedback_text_glow_radius(feedback_text: str) -> float | None:
    text = str(feedback_text or "")
    patterns = (
        r"(?:外发光|发光|光晕|aura|glow|描边模糊|描边|outline|blur|模糊)[^0-9]{0,64}(\d+(?:\.\d+)?)\s*(?:px|像素)?",
        r"(\d+(?:\.\d+)?)\s*(?:px|像素)[^，,。；;\n]{0,64}(?:外发光|发光|光晕|aura|glow|描边模糊|描边|outline|blur|模糊)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        context = text[max(0, match.start() - 16): match.end() + 16].lower()
        if any(term in context for term in ("不变", "unchanged", "same")) and not any(
            term in context for term in ("增强", "加强", "加大", "改到", "设为", "设置为", "到", "to")
        ):
            continue
        try:
            radius = float(match.group(1))
        except (TypeError, ValueError):
            continue
        if radius < 0:
            continue
        return round(float(min(radius, 64.0)), 4)
    return None


def _apply_text_color_feedback(layers: list[dict], feedback_text: str) -> list[str]:
    text = str(feedback_text or "").lower()
    if not any(term in text for term in ("文字", "字", "text", "title", "主文字", "高亮")):
        return []
    if not any(term in text for term in ("颜色", "色", "color", "colour", "#", "洋红", "粉紫", "magenta", "fuchsia", "pink")):
        return []
    color = _feedback_text_color(feedback_text)
    if color is None:
        return []

    changed: list[str] = []
    for layer in _text_feedback_candidates(layers):
        font_config = layer.get("font_config")
        if not isinstance(font_config, dict):
            font_config = {}
            layer["font_config"] = font_config
        font_config["color"] = color
        changed.append(str(layer.get("id") or layer.get("text") or "text"))
    return changed


def _apply_baseline_glow_feedback(layers: list[dict], feedback_text: str) -> list[str]:
    text = str(feedback_text or "").lower()
    if not any(term in text for term in ("baseline_glow", "baseline", "基线光条", "基线", "光条")):
        return []
    if not any(
        term in text
        for term in (
            "颜色",
            "色",
            "color",
            "colour",
            "#",
            "暖橙",
            "橙",
            "orange",
            "opacity",
            "不透明度",
            "透明度",
            "高度",
            "height",
            "宽度",
            "width",
            "移动",
            "下移",
            "上移",
            "向下",
            "向上",
            "position",
            "位置",
            "左侧",
            "左边",
            "left",
            "x=",
            " x ",
        )
    ):
        return []
    color = _feedback_baseline_glow_color(feedback_text)
    opacity = _feedback_opacity_value(feedback_text)
    width_px = _feedback_pixel_value_after_terms(feedback_text, ("宽度", "width"))
    height_px = _feedback_pixel_value_after_terms(feedback_text, ("高度", "height"))
    x_px = _feedback_horizontal_pixel_position(feedback_text)
    y_delta = _feedback_vertical_pixel_delta(feedback_text)
    if color is None and opacity is None and width_px is None and height_px is None and x_px is None and y_delta is None:
        return []

    candidates = [
        layer
        for layer in layers
        if isinstance(layer, dict) and str(layer.get("id") or "") == "baseline_glow"
    ]
    if not candidates:
        candidates = [
            layer
            for layer in layers
            if isinstance(layer, dict)
            and layer.get("type") == "solid"
            and ("baseline" in str(layer.get("id") or "").lower() or "glow" in str(layer.get("id") or "").lower())
        ]
    changed: list[str] = []
    for layer in candidates:
        if color is not None:
            rgba = list(color)
            if opacity is not None:
                rgba[3] = opacity
            elif isinstance(layer.get("color"), list) and len(layer["color"]) >= 4:
                rgba[3] = layer["color"][3]
            layer["color"] = rgba
        elif opacity is not None and isinstance(layer.get("color"), list) and len(layer["color"]) >= 4:
            rgba = list(layer["color"])
            rgba[3] = opacity
            layer["color"] = rgba
        if opacity is not None:
            layer["opacity"] = opacity
        if width_px is not None:
            size = layer.get("size")
            if isinstance(size, list) and len(size) >= 2:
                new_size = list(size)
                new_size[0] = width_px
                layer["size"] = new_size
        if height_px is not None:
            size = layer.get("size")
            if isinstance(size, list) and len(size) >= 2:
                new_size = list(size)
                new_size[1] = height_px
                layer["size"] = new_size
        if y_delta is not None:
            position = layer.get("position")
            if isinstance(position, list) and len(position) >= 2:
                new_position = list(position)
                new_position[1] = int(round(float(new_position[1]) + y_delta))
                layer["position"] = new_position
        if x_px is not None:
            position = layer.get("position")
            if isinstance(position, list) and len(position) >= 2:
                new_position = list(position)
                new_position[0] = x_px
                layer["position"] = new_position
        changed.append(str(layer.get("id") or "baseline_glow"))
    return changed


def _apply_stage_floor_feedback(layers: list[dict], feedback_text: str) -> list[str]:
    text = str(feedback_text or "").lower()
    if not any(term in text for term in ("stage_floor", "stage floor", "舞台地面", "地面")):
        return []
    if not any(term in text for term in ("颜色", "色", "color", "colour", "#", "opacity", "不透明度", "透明度")):
        return []
    color = _feedback_hex_color(feedback_text)
    opacity = _feedback_opacity_value(feedback_text)
    if color is None and opacity is None:
        return []

    candidates = [
        layer
        for layer in layers
        if isinstance(layer, dict) and str(layer.get("id") or "") == "stage_floor"
    ]
    if not candidates:
        candidates = [
            layer
            for layer in layers
            if isinstance(layer, dict)
            and layer.get("type") == "solid"
            and any(term in str(layer.get("id") or "").lower() for term in ("stage", "floor"))
        ]
    changed: list[str] = []
    for layer in candidates:
        if color is not None:
            rgba = list(color)
            if opacity is not None:
                rgba[3] = opacity
            layer["color"] = rgba
        elif opacity is not None and isinstance(layer.get("color"), list) and len(layer["color"]) >= 4:
            rgba = list(layer["color"])
            rgba[3] = opacity
            layer["color"] = rgba
        if opacity is not None:
            layer["opacity"] = opacity
        changed.append(str(layer.get("id") or "stage_floor"))
    return changed


def _feedback_baseline_glow_color(feedback_text: str) -> list[float] | None:
    color = _feedback_hex_color(feedback_text)
    if color is not None:
        return color
    lower = str(feedback_text or "").lower()
    if any(term in lower for term in ("暖橙", "橙", "orange", "amber")):
        return [1.0, 0.6902, 0.0, 1.0]
    return None


def _feedback_pixel_value_after_terms(feedback_text: str, terms: tuple[str, ...]) -> int | None:
    text = str(feedback_text or "")
    lowered = text.lower()
    lowered_terms = tuple(term.lower() for term in terms)
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:px|像素)?", text, re.IGNORECASE):
        prefix = lowered[max(0, match.start() - 24): match.start()]
        suffix = lowered[match.end(): match.end() + 16]
        if not any(term in prefix for term in lowered_terms):
            continue
        context = prefix + suffix
        if any(term in context for term in ("不变", "保持", "unchanged", "same")) and not any(
            term in context for term in ("压到", "改到", "改成", "变成", "设为", "到", "to", "=")
        ):
            continue
        value = int(round(float(match.group(1))))
        return max(value, 1)
    return None


def _feedback_vertical_pixel_delta(feedback_text: str) -> int | None:
    text = str(feedback_text or "")
    patterns: tuple[tuple[str, int], ...] = (
        (r"(?:向下移动|下移|往下移动|往下|move\s+down|down)[^0-9]{0,12}(\d+(?:\.\d+)?)\s*(?:px|像素)?", 1),
        (r"(?:向上移动|上移|往上移动|往上|move\s+up|up)[^0-9]{0,12}(\d+(?:\.\d+)?)\s*(?:px|像素)?", -1),
    )
    for pattern, direction in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(round(float(match.group(1)))) * direction
    return None


def _feedback_horizontal_pixel_position(feedback_text: str) -> int | None:
    text = str(feedback_text or "")
    patterns: tuple[str, ...] = (
        r"(?:左侧|左边|left(?:\s+edge)?)?[^0-9，,。；;\n]{0,20}\bx\s*(?:位置|position)?\s*(?:改到|改成|改为|设为|设置为|变成|到|=|:|to)\s*(\d+(?:\.\d+)?)\s*(?:px|像素)?",
        r"(?:左侧|左边|left(?:\s+edge)?)[^0-9，,。；;\n]{0,20}(?:位置|position)?\s*(?:改到|改成|改为|设为|设置为|变成|到|=|:|to)\s*(\d+(?:\.\d+)?)\s*(?:px|像素)?",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            context = text[max(0, match.start() - 12): match.end() + 16].lower()
            if any(term in context for term in ("不变", "unchanged", "same")):
                continue
            value = int(round(float(match.group(1))))
            return max(value, 0)
    return None


def _feedback_opacity_value(feedback_text: str) -> float | None:
    text = str(feedback_text or "")
    for match in re.finditer(r"(?:不透明度|透明度|opacity|alpha)", text, re.IGNORECASE):
        context = text[max(0, match.start() - 8): match.end()].lower()
        if any(term in context for term in ("不变", "unchanged", "same")):
            continue
        window = text[match.end(): match.end() + 40]
        segment = re.split(r"[，,。；;、\n]", window, maxsplit=1)[0]
        if any(term in segment.lower() for term in ("不变", "unchanged", "same")):
            continue
        value_match = re.search(r"[^0-9%]*(\d+(?:\.\d+)?)(%)?", segment, re.IGNORECASE)
        if not value_match:
            continue
        raw = float(value_match.group(1))
        if value_match.group(2):
            raw /= 100.0
        if raw > 1.0 and raw <= 100.0:
            raw /= 100.0
        return round(float(min(max(raw, 0.0), 1.0)), 4)
    return None


def _feedback_hex_color(feedback_text: str) -> list[float] | None:
    match = re.search(r"#([0-9a-fA-F]{6})(?:[0-9a-fA-F]{2})?", str(feedback_text or ""))
    if not match:
        return None
    raw = match.group(1)
    return [round(int(raw[index:index + 2], 16) / 255, 4) for index in (0, 2, 4)] + [1.0]


def _feedback_text_color(feedback_text: str) -> list[float] | None:
    text = str(feedback_text or "")
    color = _feedback_hex_color(text)
    if color is not None:
        return color
    lower = text.lower()
    if any(term in lower for term in ("洋红", "粉紫", "magenta", "fuchsia", "pink purple", "pink-purple")):
        return [1.0, 0.3098, 0.8471, 1.0]
    return None


def _apply_ball_endpoint_feedback(layers: list[dict], feedback_text: str) -> list[str]:
    text = str(feedback_text or "").lower()
    if not any(term in text for term in ("小球", "球", "ball")):
        return []
    if not any(term in text for term in ("终点", "最后", "停在", "正下方", "endpoint", "final", "under")):
        return []

    ball_layers = [
        layer
        for layer in layers
        if isinstance(layer, dict) and "ball" in str(layer.get("id") or "").lower()
    ]
    if not ball_layers:
        return []
    target = _layer_feedback_ball_target(layers, ball_layers)
    if target is None:
        return []
    target_x, target_y = target
    changed: list[str] = []
    for layer in ball_layers:
        keyframes = layer.get("keyframes")
        if not isinstance(keyframes, dict):
            continue
        position = keyframes.get("position")
        if not isinstance(position, dict):
            continue
        points = position.get("points")
        if not isinstance(points, list):
            continue
        total_frames = _layer_plan_total_frames(layers)
        hold_start = max(0, total_frames - 15)
        kept = [
            deepcopy(point)
            for point in points
            if isinstance(point, dict) and int(point.get("frame") or 0) < hold_start
        ]
        kept.append({"frame": hold_start, "value": [target_x, target_y], "easing": "ease_out"})
        kept.append({"frame": max(total_frames - 1, hold_start), "value": [target_x, target_y], "easing": "linear"})
        position["points"] = kept
        changed.append(str(layer.get("id") or "ball"))
    return changed


def _layer_plan_total_frames(layers: list[dict]) -> int:
    total = 0
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        for key in ("duration", "end_frame"):
            try:
                total = max(total, int(layer.get(key) or 0))
            except Exception:
                pass
        keyframes = layer.get("keyframes")
        if isinstance(keyframes, dict):
            for spec in keyframes.values():
                if isinstance(spec, dict) and isinstance(spec.get("points"), list):
                    for point in spec["points"]:
                        if isinstance(point, dict):
                            try:
                                total = max(total, int(point.get("frame") or 0) + 1)
                            except Exception:
                                pass
    return max(total, 1)


def _layer_feedback_ball_target(layers: list[dict], ball_layers: list[dict]) -> tuple[int, int] | None:
    text_layers = [
        layer
        for layer in layers
        if isinstance(layer, dict)
        and layer.get("type") == "text"
        and str(layer.get("text") or "").strip()
        and isinstance(layer.get("position"), list)
    ]
    letter_layers = [layer for layer in text_layers if len(str(layer.get("text") or "").strip()) == 1]
    if letter_layers:
        target_letter = max(letter_layers, key=lambda layer: float(layer.get("position", [0])[0] or 0))
    elif text_layers:
        target_letter = max(text_layers, key=lambda layer: float(layer.get("position", [0])[0] or 0))
    else:
        return None
    ball_size = 58
    for layer in ball_layers:
        size = layer.get("size")
        if isinstance(size, list) and size:
            try:
                ball_size = int(size[0])
                break
            except Exception:
                pass
    pos = target_letter.get("position") or [0, 0]
    font_config = target_letter.get("font_config") if isinstance(target_letter.get("font_config"), dict) else {}
    try:
        font_size = int(font_config.get("size") or 96)
    except Exception:
        font_size = 96
    target_x = int(round(float(pos[0]) + font_size * 0.34 - ball_size / 2))
    baseline_y = float(pos[1]) + font_size * 0.74
    target_y = int(round(baseline_y + 40 - ball_size / 2))
    return target_x, target_y


_REVISION_OUTPUT_NAME_MAX_BYTES = 240


def _compact_revision_output_path(source: Path, marker: str, suffix: str) -> Path:
    clean_marker = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(marker or "revision")).strip(".-") or "revision"
    clean_suffix = re.sub(r"[^A-Za-z0-9_-]+", "", str(suffix or ""))[:12] or uuid.uuid4().hex[:8]
    candidate = source.with_name(f"{source.stem}.{clean_marker}-{clean_suffix}{source.suffix}")
    if len(candidate.name.encode("utf-8")) <= _REVISION_OUTPUT_NAME_MAX_BYTES:
        return candidate

    base = re.split(r"\.(?:timeline|layer|preview|merge|revision)", source.stem, maxsplit=1)[0] or source.stem
    digest = hashlib.sha1(source.stem.encode("utf-8")).hexdigest()[:10]
    prefix = base[:80] or "revision"
    while prefix:
        name = f"{prefix}.{clean_marker}-{clean_suffix}-{digest}{source.suffix}"
        if len(name.encode("utf-8")) <= _REVISION_OUTPUT_NAME_MAX_BYTES:
            return source.with_name(name)
        prefix = prefix[:-1]
    return source.with_name(f"revision.{clean_marker}-{clean_suffix}-{digest}{source.suffix}")


def _create_timeline_broll_feedback_revision(
    task: dict,
    feedback: dict,
    source_pass: dict,
    source_output: str,
) -> dict | None:
    targets = _timeline_broll_source_targets(source_pass)
    if len(targets) < 2:
        return None
    source = Path(source_output).expanduser().resolve()
    if not source.exists():
        return None
    suffix = str(feedback.get("feedback_id") or uuid.uuid4().hex[:8]).replace("feedback_", "")[:8]
    output = _compact_revision_output_path(source, "timeline-revision", suffix)
    feedback_text = str(feedback.get("feedback") or "")
    duration_sec = _timeline_feedback_duration(source_pass, feedback_text)
    transition_sec = _timeline_feedback_transition(source_pass, feedback_text, duration_sec)
    grade_filter = _timeline_feedback_grade_filter(feedback_text)
    try:
        render_meta = _render_timeline_broll_preview(
            targets[:2],
            output_path=str(output),
            duration_sec=duration_sec,
            transition_sec=transition_sec,
            grade_filter=grade_filter,
            prompt=feedback_text,
        )
    except Exception:
        return None
    if not output.exists():
        return None
    sidecar = output.with_suffix(".timeline-broll.json")
    return {
        "render_pass_id": f"pass_{uuid.uuid4().hex[:12]}",
        "kind": "timeline_broll_revision",
        "output_path": str(output),
        "preview_path": str(output),
        "status": "succeeded",
        "capabilities": ["timeline", "transition", "color", "local-feedback-revision"],
        "layer_ids": [],
        "manifest_paths": [str(sidecar)] if sidecar.exists() else [],
        "source_render_pass_id": str(source_pass.get("render_pass_id") or ""),
        "feedback_id": str(feedback.get("feedback_id") or ""),
        "source_materials": source_pass.get("source_materials") or [],
        "duration_sec": duration_sec,
        "transition_sec": transition_sec,
        "metadata": render_meta,
        "step_functions": ["gemia.agent_workflow.timeline_broll_concat"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _create_timeline_merge_kept_feedback_revision(
    task: dict,
    feedback: dict,
    source_pass: dict,
    source_output: str,
) -> dict | None:
    targets = _timeline_merge_source_targets(source_pass)
    if not targets:
        return None
    source = Path(source_output).expanduser().resolve()
    if not source.exists():
        return None
    suffix = str(feedback.get("feedback_id") or uuid.uuid4().hex[:8]).replace("feedback_", "")[:8]
    output = _compact_revision_output_path(source, "timeline-merge-revision", suffix)
    feedback_text = str(feedback.get("feedback") or "")
    min_clip_duration = _timeline_merge_feedback_min_clip_duration(feedback_text)
    try:
        render_meta = _render_timeline_kept_clip_merge(
            targets,
            output_path=str(output),
            min_clip_duration_sec=min_clip_duration,
        )
    except Exception:
        return None
    if not output.exists():
        return None
    sidecar = output.with_suffix(".timeline-merge.json")
    return {
        "render_pass_id": f"pass_{uuid.uuid4().hex[:12]}",
        "kind": "timeline_merge_kept_revision",
        "output_path": str(output),
        "preview_path": str(output),
        "status": "succeeded",
        "capabilities": ["timeline", "concat", "local-feedback-revision"],
        "layer_ids": [],
        "manifest_paths": [str(sidecar)] if sidecar.exists() else [],
        "source_render_pass_id": str(source_pass.get("render_pass_id") or ""),
        "feedback_id": str(feedback.get("feedback_id") or ""),
        "source_materials": targets,
        "duration_sec": render_meta.get("duration_sec"),
        "min_clip_duration_sec": min_clip_duration,
        "metadata": render_meta,
        "step_functions": ["gemia.agent_workflow.timeline_merge_kept"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _render_pass_uses_timeline_merge_kept(render_pass: dict) -> bool:
    kind = str(render_pass.get("kind") or "").lower()
    if kind in {"timeline_merge_kept", "timeline_merge_kept_revision"}:
        return True
    return any(
        "timeline_merge_kept" in str(function or "").lower()
        for function in render_pass.get("step_functions") or []
    )


def _timeline_merge_source_targets(render_pass: dict) -> list[dict]:
    source_materials = [
        dict(item)
        for item in render_pass.get("source_materials") or []
        if isinstance(item, dict)
    ]
    if not source_materials:
        metadata = render_pass.get("metadata") if isinstance(render_pass.get("metadata"), dict) else {}
        inputs = metadata.get("inputs") if isinstance(metadata, dict) else []
        ranges = metadata.get("source_ranges") if isinstance(metadata, dict) else []
        for index, source_path in enumerate(inputs or []):
            item: dict[str, object] = {"source_path": source_path}
            if isinstance(ranges, list) and index < len(ranges) and isinstance(ranges[index], dict):
                item.update(
                    {
                        "source_in": ranges[index].get("source_in"),
                        "source_out": ranges[index].get("source_out"),
                        "duration": ranges[index].get("duration"),
                    }
                )
            source_materials.append(item)
    targets: list[dict] = []
    for item in source_materials:
        path = str(item.get("source_path") or "").strip()
        if not path or not Path(path).expanduser().exists():
            continue
        targets.append(item)
    return targets


def _timeline_merge_feedback_min_clip_duration(feedback: str) -> float:
    values = [value for value in _feedback_second_values(feedback) if 0.25 <= value <= 10.0]
    min_duration = max(values, default=0.8)
    text = feedback.lower()
    if any(term in text for term in ("太短", "看不到", "可见", "至少", "minimum", "visible")):
        min_duration = max(min_duration, 0.8)
    return min(max(min_duration, 0.25), 10.0)


def _render_pass_uses_timeline_broll(render_pass: dict) -> bool:
    kind = str(render_pass.get("kind") or "").lower()
    if kind in {"timeline_broll_preview", "timeline_broll_revision"}:
        return True
    return any(
        "timeline_broll" in str(function or "").lower()
        or "timeline_broll_concat" in str(function or "").lower()
        for function in render_pass.get("step_functions") or []
    )


def _timeline_broll_source_targets(render_pass: dict) -> list[dict]:
    source_materials = [
        dict(item)
        for item in render_pass.get("source_materials") or []
        if isinstance(item, dict)
    ]
    metadata = render_pass.get("metadata") if isinstance(render_pass.get("metadata"), dict) else {}
    ranges = metadata.get("source_ranges") if isinstance(metadata, dict) else []
    if not source_materials:
        inputs = metadata.get("inputs") if isinstance(metadata, dict) else []
        for index, source_path in enumerate(inputs or []):
            item: dict[str, object] = {"source_path": source_path}
            if isinstance(ranges, list) and index < len(ranges) and isinstance(ranges[index], dict):
                item.update(
                    {
                        "source_in": ranges[index].get("source_in"),
                        "source_out": ranges[index].get("source_out"),
                        "duration": ranges[index].get("duration"),
                    }
                )
            source_materials.append(item)

    targets: list[dict] = []
    for index, item in enumerate(source_materials):
        path = str(item.get("source_path") or "").strip()
        if not path or not Path(path).expanduser().exists():
            continue
        target: dict[str, object] = {"source_path": path}
        if isinstance(ranges, list) and index < len(ranges) and isinstance(ranges[index], dict):
            range_item = ranges[index]
        else:
            range_item = {}
        for key in ("clip_id", "asset_id", "source_in", "source_out", "inPoint", "outPoint", "metadata"):
            value = item.get(key)
            if value is None and key in {"source_in", "source_out"}:
                value = range_item.get(key)
            if value is not None:
                target[key] = value
        targets.append(target)
    return targets


def _timeline_broll_source_inputs(render_pass: dict) -> list[str]:
    metadata = render_pass.get("metadata") if isinstance(render_pass.get("metadata"), dict) else {}
    candidates = metadata.get("inputs") if isinstance(metadata, dict) else None
    if not isinstance(candidates, list) or len(candidates) < 2:
        candidates = [
            item.get("source_path")
            for item in render_pass.get("source_materials") or []
            if isinstance(item, dict)
        ]
    paths: list[str] = []
    for candidate in candidates or []:
        path = str(candidate or "").strip()
        if path and Path(path).expanduser().exists():
            paths.append(path)
    return paths


def _timeline_feedback_duration(source_pass: dict, feedback: str) -> float:
    requested = _requested_total_duration_sec(feedback, default=0.0)
    if requested:
        return requested
    metadata = source_pass.get("metadata") if isinstance(source_pass.get("metadata"), dict) else {}
    return _coerce_float(source_pass.get("duration_sec") or metadata.get("duration_sec"), 3.0)


def _timeline_feedback_transition(source_pass: dict, feedback: str, duration_sec: float) -> float:
    metadata = source_pass.get("metadata") if isinstance(source_pass.get("metadata"), dict) else {}
    base = _coerce_float(source_pass.get("transition_sec") or metadata.get("transition_sec"), min(0.3, duration_sec * 0.1))
    text = feedback.lower()
    wants_smooth_transition = any(term in text for term in ("更顺", "顺滑", "柔和", "叠化", "溶解", "smooth", "smoother", "dissolve", "crossfade"))
    if _prompt_requests_hard_cut(feedback) or (base <= 0.001 and not wants_smooth_transition):
        return 0.0
    if any(term in text for term in ("更短", "短一点", "短些", "snappy", "snappier", "shorter")):
        base *= 0.6
    if wants_smooth_transition:
        base = max(base, 0.16)
    return min(max(base, 0.08), max(0.08, duration_sec * 0.35))


def _timeline_feedback_grade_filter(feedback: str) -> str:
    text = feedback.lower()
    reduce_highlight_terms = (
        "过曝",
        "曝光过",
        "高光",
        "压高光",
        "降低高光",
        "光斑",
        "光效",
        "亮度",
        "刺眼",
        "发白",
        "前景遮罩",
        "遮挡",
        "遮住",
        "盖住",
        "背景层",
        "占比",
        "主体更清楚",
        "highlight",
        "overexposed",
        "blown",
        "glare",
    )
    reduce_saturation_terms = (
        "降低饱和",
        "降饱和",
        "饱和度",
        "低饱和",
        "青绿色",
        "青绿",
        "绿色太",
        "青色太",
        "teal",
        "cyan",
        "less saturated",
        "desaturat",
    )
    if any(term in text for term in reduce_highlight_terms) or any(
        term in text for term in reduce_saturation_terms
    ):
        stronger_reduce_terms = (
            "再",
            "继续",
            "太亮",
            "偏亮",
            "25%",
            "仍不合格",
            "遮挡",
            "遮住",
            "盖住",
            "前景遮罩",
            "背景层",
            "占比",
            "主体",
            "显著",
            "stronger",
            "more",
            "further",
        )
        if any(term in text for term in stronger_reduce_terms):
            return (
                "curves=all='0/0 0.50/0.43 1/0.68',"
                "eq=saturation=0.72:contrast=0.92:brightness=-0.026,"
                "colorbalance=rs=0.025:gs=0.004:bs=-0.036"
            )
        return (
            "curves=all='0/0 0.55/0.50 1/0.78',"
            "eq=saturation=0.86:contrast=0.96:brightness=-0.012,"
            "colorbalance=rs=0.035:gs=0.010:bs=-0.028"
        )
    if any(term in text for term in ("暖色", "电影", "cinematic", "warm", "color grade", "调色")):
        return "colorbalance=rs=0.060:gs=0.018:bs=-0.045,eq=saturation=1.26:contrast=1.08:brightness=0.016"
    return "eq=saturation=1.16:contrast=1.05:brightness=0.012"


def _create_transition_feedback_revision(
    task: dict,
    feedback: dict,
    source_pass: dict,
    source_output: str,
) -> dict | None:
    if not _render_pass_uses_transition_shutter(source_pass):
        return None
    step_info = _find_transition_shutter_step(task)
    if step_info is None:
        return None
    step, plan = step_info
    inputs = _transition_step_inputs(step, plan)
    if len(inputs) < 2:
        return None
    source = Path(source_output).expanduser().resolve()
    if not source.exists():
        return None
    suffix = str(feedback.get("feedback_id") or uuid.uuid4().hex[:8]).replace("feedback_", "")[:8]
    output = _compact_revision_output_path(source, "transition-revision", suffix)
    args = _transition_feedback_args(step, str(feedback.get("feedback") or ""))
    try:
        from gemia.video.transitions import transition_shutter

        transition_shutter(inputs[0], inputs[1], str(output), **args)
    except Exception:
        return None
    if not output.exists():
        return None
    sidecar = output.with_name(f"{output.stem}.transition-revision.json")
    sidecar.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "transition_revision",
                "source_render_pass_id": str(source_pass.get("render_pass_id") or ""),
                "feedback_id": str(feedback.get("feedback_id") or ""),
                "function": "gemia.video.transitions.transition_shutter",
                "inputs": inputs[:2],
                "output_path": str(output),
                "args": args,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "render_pass_id": f"pass_{uuid.uuid4().hex[:12]}",
        "kind": "transition_revision",
        "output_path": str(output),
        "preview_path": str(output),
        "status": "succeeded",
        "capabilities": ["transition", "local-feedback-revision"],
        "layer_ids": [],
        "manifest_paths": [str(sidecar)],
        "source_render_pass_id": str(source_pass.get("render_pass_id") or ""),
        "feedback_id": str(feedback.get("feedback_id") or ""),
        "step_functions": ["gemia.video.transitions.transition_shutter"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _render_pass_uses_transition_shutter(render_pass: dict) -> bool:
    return any(
        "transition_shutter" in str(function or "").lower()
        for function in render_pass.get("step_functions") or []
    )


def _find_transition_shutter_step(task: dict) -> tuple[dict, dict] | None:
    plan_entries = [item for item in task.get("agent_plan") or [] if isinstance(item, dict)]
    for plan_entry in reversed(plan_entries):
        plan = plan_entry.get("plan")
        if not isinstance(plan, dict):
            continue
        steps = [item for item in plan.get("steps") or [] if isinstance(item, dict)]
        for step in reversed(steps):
            if "transition_shutter" in str(step.get("function") or "").lower():
                return step, plan
    return None


def _transition_step_inputs(step: dict, plan: dict) -> list[str]:
    raw_inputs = step.get("input")
    if raw_inputs is None:
        args = step.get("args") if isinstance(step.get("args"), dict) else {}
        raw_inputs = [args.get("input_a"), args.get("input_b")]
    if not isinstance(raw_inputs, list):
        raw_inputs = [raw_inputs]
    resolved = [_transition_resolve_input(value, plan) for value in raw_inputs]
    return [path for path in resolved if path]


def _transition_resolve_input(value: object, plan: dict) -> str:
    if isinstance(value, dict):
        for key in ("source_path", "path", "input_path"):
            resolved = _transition_resolve_input(value.get(key), plan)
            if resolved:
                return resolved
        return ""
    raw = str(value or "").strip()
    if raw == "$input":
        return str(plan.get("input_path") or "").strip()
    if raw.startswith("$"):
        return ""
    return raw


def _transition_feedback_args(step: dict, feedback: str) -> dict:
    source = step.get("args") if isinstance(step.get("args"), dict) else {}
    args: dict[str, object] = {
        "duration_sec": _coerce_float(source.get("duration_sec"), 1.0),
        "hold_sec": _coerce_float(source.get("hold_sec"), 0.0),
        "edge_highlight": bool(source.get("edge_highlight") or False),
        "highlight_strength": _coerce_float(source.get("highlight_strength"), 0.65),
    }
    try:
        args["blade_count"] = int(source.get("blade_count") or 6)
    except (TypeError, ValueError):
        args["blade_count"] = 6

    text = feedback.lower()
    if any(term in text for term in ("六", "6", "six")):
        args["blade_count"] = 6
    if any(term in text for term in ("全黑", "黑场", "停顿", "保持", "hold", "pause", "black")):
        hold_values = [value for value in _feedback_second_values(feedback) if 0 < value <= 0.5]
        args["hold_sec"] = max(float(args["hold_sec"]), max(hold_values, default=0.1), 0.1)
    if any(
        term in text
        for term in (
            "金属",
            "高光",
            "纹理",
            "拉丝",
            "模糊",
            "拖影",
            "机械",
            "metal",
            "highlight",
            "texture",
            "blur",
            "motion",
        )
    ):
        args["edge_highlight"] = True
        args["highlight_strength"] = max(float(args["highlight_strength"]), 0.85)
    args["hold_sec"] = min(float(args["hold_sec"]), float(args["duration_sec"]) * 0.8)
    args["highlight_strength"] = min(max(float(args["highlight_strength"]), 0.0), 1.0)
    return args


def _coerce_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _feedback_second_values(feedback: str) -> list[float]:
    values: list[float] = []
    pattern = r"(\d+(?:\.\d+)?)\s*(?:秒|secs|sec|seconds|second|s)(?:\b|$)"
    for match in re.finditer(pattern, feedback, re.IGNORECASE):
        values.append(_coerce_float(match.group(1), 0.0))
    return values


def _layer_flow_manifest_path(render_pass: dict, source_output: str) -> Path | None:
    for raw in render_pass.get("manifest_paths") or []:
        path = Path(str(raw)).expanduser()
        if path.name.endswith(".layer-flow.json") and path.exists():
            return path
    candidate = Path(source_output).expanduser().resolve().with_suffix(".layer-flow.json")
    return candidate if candidate.exists() else None


def _feedback_wants_visual_revision(feedback: str) -> bool:
    text = feedback.lower()
    return any(
        term in text
        for term in (
            "标题",
            "文字",
            "字体",
            "字",
            "中央",
            "中间",
            "中心",
            "太小",
            "太大",
            "左上角",
            "右侧",
            "位置",
            "节奏",
            "方块",
            "小球",
            "球",
            "弹跳",
            "击中",
            "发光",
            "光晕",
            "擦除",
            "转场",
            "快门",
            "光圈",
            "叶片",
            "金属",
            "高光",
            "纹理",
            "模糊",
            "停顿",
            "时间线",
            "紧凑",
            "片段",
            "可见",
            "看不到",
            "太短",
            "压到",
            "压缩",
            "更短",
            "叠化",
            "调色",
            "暖色",
            "电影感",
            "b-roll",
            "timeline",
            "shorter",
            "snappy",
            "warm",
            "cinematic",
            "color",
            "center",
            "title",
            "text",
            "ball",
            "bounce",
            "glow",
            "hit",
            "bigger",
            "smaller",
            "transition",
            "shutter",
            "iris",
            "metal",
            "highlight",
            "texture",
            "blur",
        )
    )


_REVISION_TITLE_STOPWORDS = {
    "AI",
    "CSS",
    "DIV",
    "DOCTYPE",
    "FPS",
    "H264",
    "HEADLINE",
    "HTML",
    "HTTP",
    "HTTPS",
    "ID",
    "JSON",
    "LAYER",
    "LAYERS",
    "PLAN",
    "MP4",
    "MOV",
    "REVIEW",
    "SCHEMA",
    "SCRIPT",
    "SPAN",
    "STYLE",
    "TEXT",
    "TYPE",
    "VERSION",
    "WEBM",
}


def _clean_revision_title_candidate(candidate: str) -> str:
    raw = str(candidate or "").strip()
    if not raw or "." in raw or "_" in raw:
        return ""
    tokens = [token.strip().upper() for token in raw.split() if token.strip()]
    if not tokens:
        return ""
    if any(token in _REVISION_TITLE_STOPWORDS for token in tokens):
        return ""
    compact = "".join(tokens)
    if len(compact) < 3 or not any(char.isalpha() for char in compact):
        return ""
    return " ".join(tokens)


def _revision_title_from_feedback_or_plan(feedback: str, plan: dict) -> str:
    sources = (feedback, json.dumps(plan, ensure_ascii=False))
    for source in sources:
        if re.search(r"(?<![A-Za-z0-9_])lumeri(?![A-Za-z0-9_])", source, re.IGNORECASE):
            return "LUMERI"
    for source in sources:
        for match in re.finditer(
            r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9]*(?:\s+[A-Za-z0-9]+){0,3})(?![A-Za-z0-9_])",
            source,
        ):
            title = _clean_revision_title_candidate(match.group(1))
            if title:
                return title
    return "LUMERI"


def _feedback_wants_mg_ball_title_revision(feedback: str, plan: dict) -> bool:
    text = f"{feedback}\n{json.dumps(plan, ensure_ascii=False)}".lower()
    wants_title = any(term in text for term in ("lumeri", "标题", "title", "文字", "字母"))
    wants_balls = any(term in text for term in ("小球", "球", "ball", "balls"))
    wants_motion = any(term in text for term in ("弹跳", "击中", "从左到右", "光晕", "发光", "bounce", "hit", "glow"))
    return wants_title and wants_balls and wants_motion


def _write_mg_ball_sprite(path: Path, color: tuple[int, int, int]) -> None:
    from PIL import Image, ImageDraw, ImageFilter

    size = 82
    center = size // 2
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    draw.ellipse((9, 9, size - 9, size - 9), fill=(*color, 130))
    glow = glow.filter(ImageFilter.GaussianBlur(13))

    core = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(core)
    draw.ellipse((center - 17, center - 17, center + 17, center + 17), fill=(*color, 245))
    draw.ellipse((center - 9, center - 10, center + 5, center + 4), fill=(255, 255, 255, 150))
    Image.alpha_composite(glow, core).save(path)


def _measure_title_width(title: str, font_size: int) -> int:
    from PIL import Image, ImageDraw, ImageFont

    try:
        font = ImageFont.truetype("DejaVuSans.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()
    probe = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    bbox = ImageDraw.Draw(probe).textbbox((0, 0), title, font=font)
    return max(1, int(bbox[2] - bbox[0]))


def _mg_keyframe_points(points: list[tuple[int, list[int], str]]) -> dict:
    return {
        "points": [
            {"frame": frame, "value": value, "easing": easing}
            for frame, value, easing in points
        ]
    }


def _mg_scalar_points(points: list[tuple[int, float, str]]) -> dict:
    return {
        str(frame): {"value": value, "easing": easing}
        for frame, value, easing in points
    }


def _render_mg_ball_title_revision(output_path: Path, *, title: str) -> list[str]:
    from gemia.video.layer_flow import render_layer_workflow

    width, height, fps, total_frames = 540, 304, 15, 45
    compact_title = title.strip() or "LUMERI"
    title_size = 64 if len(compact_title.replace(" ", "")) <= 8 else 52
    title_width = min(430, _measure_title_width(compact_title, title_size) + 16)
    title_x = int((width - title_width) / 2)
    title_y = 116
    title_center_y = title_y + 38
    colors = [
        (64, 220, 255),
        (255, 76, 216),
        (255, 214, 76),
        (86, 255, 158),
        (150, 112, 255),
    ]

    sprite_paths: list[Path] = []
    for index, color in enumerate(colors):
        sprite_path = output_path.with_name(f"{output_path.stem}.ball-{index}.png")
        _write_mg_ball_sprite(sprite_path, color)
        sprite_paths.append(sprite_path)

    overlay_layers: list[dict] = [
        {
            "id": "revision_bg",
            "type": "solid",
            "color": [0.01, 0.012, 0.024, 1.0],
            "size": [width, height],
            "duration": total_frames,
            "z_index": 0,
        },
        {
            "id": "revision_floor_glow",
            "type": "solid",
            "color": [0.0, 0.62, 1.0, 0.18],
            "position": [64, 222],
            "size": [412, 3],
            "duration": total_frames,
            "z_index": 1,
            "blend_mode": "screen",
        },
        {
            "id": "revision_title_aura_cyan",
            "type": "text",
            "text": compact_title,
            "position": [title_x - 3, title_y - 2],
            "font_config": {"size": title_size, "color": [0.0, 0.82, 1.0, 0.40], "padding": 8},
            "duration": total_frames,
            "z_index": 2,
            "blend_mode": "screen",
        },
        {
            "id": "revision_title_aura_magenta",
            "type": "text",
            "text": compact_title,
            "position": [title_x + 4, title_y + 3],
            "font_config": {"size": title_size, "color": [1.0, 0.08, 0.82, 0.36], "padding": 8},
            "duration": total_frames,
            "z_index": 2,
            "blend_mode": "screen",
        },
        {
            "id": "revision_title_shadow",
            "type": "text",
            "text": compact_title,
            "position": [title_x + 2, title_y + 4],
            "font_config": {"size": title_size, "color": [0.0, 0.0, 0.0, 0.75], "padding": 8},
            "duration": total_frames,
            "z_index": 3,
        },
        {
            "id": "revision_title",
            "type": "text",
            "text": compact_title,
            "position": [title_x, title_y],
            "font_config": {"size": title_size, "color": [0.94, 0.98, 1.0, 1.0], "padding": 8},
            "duration": total_frames,
            "z_index": 4,
        },
    ]

    layer_ids = [str(layer["id"]) for layer in overlay_layers]
    ball_size = 82
    hit_frames = [10, 16, 22, 28, 34]
    target_span = max(title_width - 34, 1)
    for index, (sprite_path, rgb, hit_frame) in enumerate(zip(sprite_paths, colors, hit_frames, strict=True)):
        target_x = int(title_x + index * target_span / 4 - ball_size / 2 + 17)
        target_y = int(title_center_y - ball_size / 2 - 4)
        start_x = -90 - index * 12
        end_x = width + 24 + index * 12
        color = [round(channel / 255.0, 3) for channel in rgb]
        overlay_layers.extend(
            [
                {
                    "id": f"revision_hit_glow_{index}",
                    "type": "image",
                    "source": str(sprite_path),
                    "position": [target_x, target_y],
                    "duration": total_frames,
                    "opacity": 0.0,
                    "scale": 1.38,
                    "z_index": 5,
                    "blend_mode": "screen",
                    "keyframes": {
                        "opacity": _mg_scalar_points(
                            [
                                (max(hit_frame - 3, 0), 0.0, "linear"),
                                (hit_frame, 0.88, "ease_out"),
                                (min(hit_frame + 5, total_frames - 1), 0.0, "ease_in"),
                            ]
                        ),
                        "scale": _mg_scalar_points(
                            [
                                (max(hit_frame - 3, 0), 0.75, "ease_out"),
                                (hit_frame, 1.62, "ease_out"),
                                (min(hit_frame + 5, total_frames - 1), 2.1, "ease_in"),
                            ]
                        ),
                    },
                },
                {
                    "id": f"revision_title_hit_pulse_{index}",
                    "type": "text",
                    "text": compact_title,
                    "position": [title_x, title_y],
                    "font_config": {
                        "size": title_size,
                        "color": [color[0], color[1], color[2], 0.95],
                        "padding": 8,
                    },
                    "duration": total_frames,
                    "opacity": 0.0,
                    "scale": 1.0,
                    "z_index": 6,
                    "blend_mode": "screen",
                    "keyframes": {
                        "opacity": _mg_scalar_points(
                            [
                                (max(hit_frame - 2, 0), 0.0, "linear"),
                                (hit_frame, 0.68, "ease_out"),
                                (min(hit_frame + 4, total_frames - 1), 0.0, "ease_in"),
                            ]
                        ),
                        "scale": _mg_scalar_points(
                            [
                                (max(hit_frame - 2, 0), 1.0, "ease_out"),
                                (hit_frame, 1.06, "ease_out"),
                                (min(hit_frame + 4, total_frames - 1), 1.0, "ease_in_out"),
                            ]
                        ),
                    },
                },
                {
                    "id": f"revision_ball_{index}",
                    "type": "image",
                    "source": str(sprite_path),
                    "position": [start_x, target_y + 42],
                    "duration": total_frames,
                    "opacity": 0.0,
                    "z_index": 7 + index,
                    "blend_mode": "screen",
                    "keyframes": {
                        "position": _mg_keyframe_points(
                            [
                                (0, [start_x, target_y + 48], "linear"),
                                (max(hit_frame - 7, 0), [target_x - 118, target_y + 26], "ease_out"),
                                (max(hit_frame - 3, 0), [target_x - 48, target_y - 42], "ease_out"),
                                (hit_frame, [target_x, target_y], "ease_in"),
                                (min(hit_frame + 4, total_frames - 1), [target_x + 42, target_y - 36], "ease_out"),
                                (total_frames - 1, [end_x, target_y + 18], "ease_in_out"),
                            ]
                        ),
                        "opacity": _mg_scalar_points(
                            [
                                (0, 0.0, "linear"),
                                (max(hit_frame - 8, 0), 1.0, "ease_out"),
                                (min(hit_frame + 8, total_frames - 1), 1.0, "linear"),
                                (total_frames - 1, 0.0, "ease_in"),
                            ]
                        ),
                    },
                },
            ]
        )
        layer_ids.extend([f"revision_hit_glow_{index}", f"revision_title_hit_pulse_{index}", f"revision_ball_{index}"])

    render_layer_workflow(
        "",
        str(output_path),
        canvas={"width": width, "height": height, "fps": fps, "total_frames": total_frames},
        frame_step=1,
        max_long_edge=540,
        overlay_layers=overlay_layers,
    )
    return layer_ids


def _render_centered_title_revision(output_path: Path, *, title: str) -> list[str]:
    from gemia.video.layer_flow import render_layer_workflow

    width, height, total_frames = 540, 304, 45
    title_size = 54 if len(title.replace(" ", "")) <= 8 else 46
    title_width = max(220, min(430, int(len(title) * title_size * 0.56)))
    title_x = int((width - title_width) / 2)
    title_y = int(height * 0.43)
    square_x = min(width - 40, title_x + title_width + 14)
    square_y = title_y + 12
    grid_layers = [
        {
            "id": f"revision_grid_v_{index}",
            "type": "solid",
            "color": [0.0, 0.72, 0.9, 0.16],
            "position": [x, 0],
            "size": [1, height],
            "duration": total_frames,
            "z_index": 1,
        }
        for index, x in enumerate(range(48, width, 72))
    ] + [
        {
            "id": f"revision_grid_h_{index}",
            "type": "solid",
            "color": [1.0, 0.0, 0.92, 0.10],
            "position": [0, y],
            "size": [width, 1],
            "duration": total_frames,
            "z_index": 1,
        }
        for index, y in enumerate(range(42, height, 58))
    ]
    layer_ids = [
        "revision_bg",
        *(layer["id"] for layer in grid_layers),
        "revision_title_shadow",
        "revision_title",
        "revision_cyan_wipe",
        "revision_magenta_square",
    ]
    render_layer_workflow(
        "",
        str(output_path),
        canvas={"width": width, "height": height, "fps": 15, "total_frames": total_frames},
        frame_step=1,
        max_long_edge=540,
        overlay_layers=[
            {
                "id": "revision_bg",
                "type": "solid",
                "color": [0.012, 0.016, 0.03, 1.0],
                "size": [width, height],
                "duration": total_frames,
                "z_index": 0,
            },
            *grid_layers,
            {
                "id": "revision_title_shadow",
                "type": "text",
                "text": title,
                "position": [title_x + 3, title_y + 3],
                "font_config": {"size": title_size, "color": [0.75, 0.05, 0.88, 0.45], "padding": 4},
                "duration": total_frames,
                "z_index": 2,
            },
            {
                "id": "revision_title",
                "type": "text",
                "text": title,
                "position": [title_x, title_y],
                "font_config": {"size": title_size, "color": [0.94, 0.98, 1.0, 1.0], "padding": 4},
                "duration": total_frames,
                "z_index": 3,
            },
            {
                "id": "revision_cyan_wipe",
                "type": "solid",
                "color": [0.0, 0.88, 1.0, 0.9],
                "position": [title_x - 12, title_y - 12],
                "size": [5, title_size + 30],
                "duration": total_frames,
                "z_index": 4,
                "keyframes": {
                    "position": {
                        "points": [
                            {"frame": 6, "value": [title_x - 12, title_y - 12], "easing": "ease_in_out"},
                            {"frame": 24, "value": [title_x + title_width + 10, title_y - 12], "easing": "ease_in_out"},
                            {"frame": 44, "value": [title_x + title_width + 10, title_y - 12], "easing": "linear"},
                        ]
                    }
                },
            },
            {
                "id": "revision_magenta_square",
                "type": "solid",
                "color": [1.0, 0.0, 0.92, 1.0],
                "position": [square_x, square_y],
                "size": [18, 18],
                "duration": total_frames,
                "z_index": 5,
            },
        ],
    )
    return layer_ids


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:  # quieter logs
        print(f"  {self.address_string()} {fmt % args}")

    def _security_gate(self, *, mutating: bool) -> bool:
        """Return True if the request should be rejected (and a response sent).

        Always validates the Host header. For mutating verbs (POST/DELETE)
        also validates Origin/Referer to block DNS-rebinding from a browser
        page that resolves to 127.0.0.1.
        """
        if not _host_allowed(self.headers.get("Host", "")):
            _json_response(self, 403, {"error": "host not allowed"})
            return True
        if mutating:
            origin = self.headers.get("Origin")
            referer = self.headers.get("Referer")
            if origin and not _origin_allowed(origin):
                _json_response(self, 403, {"error": "origin not allowed"})
                return True
            if referer and not _origin_allowed(referer):
                _json_response(self, 403, {"error": "referer not allowed"})
                return True
        return False

    def _handle_get_like(self, *, body: bool) -> None:
        parsed_url = urlparse(self.path)
        path = unquote(parsed_url.path).rstrip("/") or "/"

        # Web UI
        if path == "/":
            _file_response(self, _web_index_path(), body=body)
            return

        if path == "/next":
            _file_response(self, _vnext_index_path(), body=body)
            return

        if path.startswith("/assets/"):
            asset_path = _web_asset_path(path[len("/assets/"):])
            if asset_path is None:
                _json_response(self, 403, {"error": "forbidden"})
                return
            _file_response(self, asset_path, body=body)
            return

        if path == "/favicon.ico":
            _empty_response(self)
            return

        if path == "/health":
            _json_response(self, 200, _health_payload())
            return
        # Lumeri v3 session HTTP surface (sessions / turn / assets / stream).
        if path == "/sessions" or path.startswith("/sessions/"):
            from gemia.v3_routes import try_handle as _v3_try
            if _v3_try(self, method=("GET" if body else "HEAD")):
                return
        # Lumeri v3 frontend (vanilla HTML/JS at static/v3/).
        if path == "/v3" or path == "/v3/" or path.startswith("/v3/"):
            rel = "index.html" if path in ("/v3", "/v3/") else path[len("/v3/"):]
            v3_root = (Path(__file__).resolve().parent / "static" / "v3").resolve()
            target = _safe_child_path(v3_root, rel)
            if target is None:
                _json_response(self, 404, {"error": "v3 asset not found"})
                return
            _file_response(self, target, body=body)
            return

        # Config status (for first-run key check). Network topology fields
        # (bind host, port, LAN URLs) are gated behind a signed-in account so
        # the first-run check stays anonymous but a logged-in user can still
        # retrieve LAN pairing info.
        if path == "/config":
            payload = {
                "has_key": _has_valid_key(),
                "has_image_key": _has_valid_image_key(),
                "image_provider": "openrouter/nano-banana",
                "stability_gate": _stability_gate_enabled(),
                "health_url": "/health",
            }
            if accounts.current_account_id():
                bind_host = _configured_server_host()
                try:
                    bind_port = int(os.environ.get("LUMERI_PORT") or os.environ.get("GEMIA_PORT") or "7788")
                except ValueError:
                    bind_port = 7788
                payload.update(
                    {
                        "image_model": _configured_image_model(),
                        "image_base_url": _configured_image_base_url(),
                        "server_bind_host": bind_host,
                        "server_port": bind_port,
                        "server_urls": _server_urls(bind_host, bind_port),
                    }
                )
            _json_response(self, 200, payload)
            return

        if path == "/auth/session":
            _json_response(self, 200, accounts.auth_session_payload())
            return

        if path == "/agent-links/status":
            from gemia.agent_links import status_payload

            _json_response(self, 200, status_payload())
            return

        if path == "/agent-links/messages":
            from gemia.agent_links import list_messages

            query = parse_qs(parsed_url.query)
            try:
                limit = int(query.get("limit", ["80"])[0] or 80)
            except ValueError:
                limit = 80
            _json_response(self, 200, {"ok": True, "messages": list_messages(limit=limit)})
            return

        opencode_path = _opencode_compat_path(path)
        if opencode_path is not None:
            if not _vnext_enabled():
                _json_response(self, 404, {"error": "vNext runtime is disabled"})
                return
            try:
                from gemia.opencode_compat import sse_lines

                query = parse_qs(parsed_url.query)
                compat = _opencode_compat_service()
                if opencode_path in {"/event", "/global/event"}:
                    session_id = str((query.get("sessionID") or query.get("session_id") or query.get("id") or [""])[0] or "").strip()
                    _sse_response(self, 200, sse_lines(compat.event_stream(session_id or None)))
                    return
                if opencode_path == "/session":
                    _json_response(self, 200, compat.list_sessions())
                    return
                if opencode_path == "/session/status":
                    _json_response(self, 200, compat.session_status())
                    return
                if opencode_path == "/project":
                    _json_response(self, 200, compat.list_projects())
                    return
                if opencode_path == "/project/current":
                    project_id = str((query.get("projectID") or query.get("project_id") or [""])[0] or "").strip()
                    _json_response(self, 200, compat.current_project(project_id or None))
                    return
                if opencode_path == "/file":
                    _json_response(self, 200, compat.file_list(str((query.get("path") or ["."])[0] or ".")))
                    return
                if opencode_path == "/file/content":
                    _json_response(self, 200, compat.file_read(str((query.get("path") or [""])[0] or "")))
                    return
                if opencode_path == "/file/status":
                    _json_response(self, 200, compat.file_status())
                    return
                if opencode_path == "/find/file":
                    limit = int((query.get("limit") or ["80"])[0] or 80)
                    kind = str((query.get("type") or [""])[0] or "") or None
                    include_dirs = str((query.get("dirs") or ["false"])[0] or "").lower() == "true"
                    _json_response(self, 200, compat.find_files(str((query.get("query") or [""])[0] or ""), include_dirs=include_dirs, kind=kind, limit=limit))
                    return
                if opencode_path == "/find":
                    _json_response(self, 200, compat.find_text(str((query.get("pattern") or [""])[0] or "")))
                    return
                parts = opencode_path.strip("/").split("/")
                if len(parts) >= 2 and parts[0] == "session":
                    session_id = parts[1]
                    if len(parts) == 2:
                        _json_response(self, 200, compat.session_payload(session_id))
                        return
                    if len(parts) == 3 and parts[2] == "message":
                        _json_response(self, 200, compat.messages(session_id))
                        return
                    if len(parts) == 4 and parts[2] == "message":
                        _json_response(self, 200, compat.message(session_id, parts[3]))
                        return
                _json_response(self, 404, {"error": "opencode compatibility route not found"})
            except Exception as exc:
                _opencode_compat_error_response(self, exc)
            return

        if path.startswith("/runtime/dev/workspace"):
            if not _vnext_enabled():
                _json_response(self, 404, {"error": "vNext runtime is disabled"})
                return
            try:
                parts = path.split("/")
                service = _creative_sandbox_service()
                if len(parts) >= 5 and parts[1:4] == ["runtime", "dev", "workspace"]:
                    session_id = unquote(parts[4]).strip()
                    action = parts[5] if len(parts) >= 6 else ""
                    if not action:
                        _json_response(self, 200, service.get_workspace(session_id))
                        return
                    if action == "artifacts":
                        _json_response(self, 200, service.list_artifacts(session_id))
                        return
                    if action == "preview":
                        _json_response(self, 200, _creative_sandbox_preview_payload(service, session_id))
                        return
                    if action == "report":
                        report = service.report(session_id)
                        preview = report.get("preview")
                        if isinstance(preview, dict):
                            report["preview"] = _creative_sandbox_preview_payload(service, session_id)
                        _json_response(self, 200, report)
                        return
                    if action == "logs":
                        _json_response(self, 200, service.list_logs(session_id))
                        return
                    if action == "files":
                        query = parse_qs(urlparse(self.path).query)
                        if str((query.get("raw") or [""])[0]).strip().lower() in {"1", "true", "yes"}:
                            target = service.file_path(
                                session_id,
                                {
                                    "kind": (query.get("kind") or ["scripts"])[0],
                                    "path": (query.get("path") or [""])[0],
                                },
                            )
                            _file_response(self, target)
                            return
                        _json_response(
                            self,
                            200,
                            service.read_file(
                                session_id,
                                {
                                    "kind": (query.get("kind") or ["scripts"])[0],
                                    "path": (query.get("path") or [""])[0],
                                },
                            ),
                        )
                        return
                _json_response(self, 404, {"error": "creative sandbox route not found"})
            except Exception as exc:
                _creative_sandbox_error_response(self, exc)
            return

        if path.startswith("/runtime/task/") or path.startswith("/runtime/events/") or path.startswith("/runtime/project/"):
            if not _vnext_enabled():
                _json_response(self, 404, {"error": "vNext runtime is disabled"})
                return
            try:
                if path.startswith("/runtime/task/"):
                    task_id = unquote(path.removeprefix("/runtime/task/")).strip()
                    payload = _runtime_task_payload(task_id)
                    status = 404 if (payload.get("error") or {}).get("code") == "task_not_found" else 200
                    _json_response(self, status, payload)
                    return
                service = _runtime_service()
                if path.startswith("/runtime/events/"):
                    session_id = unquote(path.removeprefix("/runtime/events/")).strip()
                    _json_response(self, 200, service.events(session_id))
                    return
                project_id = unquote(path.removeprefix("/runtime/project/")).strip()
                _json_response(self, 200, service.project(project_id))
            except Exception as exc:
                _runtime_error_response(self, exc)
            return

        if path == "/auth/google/callback":
            query = parse_qs(parsed_url.query)
            google_error = (query.get("error") or [""])[0]
            if google_error:
                _html_response(
                    self,
                    400,
                    _auth_callback_html(ok=False, message=f"Google 返回错误：{google_error}。您可以回到 Lumeri 重试。"),
                    body=body,
                )
                return
            state = (query.get("state") or [""])[0]
            code = (query.get("code") or [""])[0]
            try:
                profile = accounts.finish_google_oauth(state, code)
                name = str(profile.get("name") or profile.get("email") or "当前账号")
                _html_response(
                    self,
                    200,
                    _auth_callback_html(ok=True, message=f"{name} 已登录。您现在可以返回 Lumeri 了。"),
                    body=body,
                )
            except Exception as exc:
                _html_response(
                    self,
                    400,
                    _auth_callback_html(ok=False, message=f"{exc}。您现在可以返回 Lumeri 重试。"),
                    body=body,
                )
            return

        if path == "/project/current":
            from gemia.project_model import normalize_project
            from gemia.session_history import load_current_session

            account_id = accounts.current_account_id()
            if not account_id:
                _json_response(self, 401, {"error": "not signed in"})
                return
            session = load_current_session(account_id=account_id)
            project = normalize_project(
                session.get("project") if isinstance(session.get("project"), dict) else None,
                project_state=session.get("project_state") if isinstance(session.get("project_state"), dict) else None,
                account_id=account_id,
            )
            _json_response(self, 200, {"project": project})
            return

        if path == "/session-history":
            from gemia.session_history import load_current_session

            account_id = accounts.current_account_id()
            if not account_id:
                _json_response(self, 401, {"error": "not signed in"})
                return
            _json_response(self, 200, load_current_session(account_id=account_id))
            return

        if path == "/session-history/list":
            from gemia.session_history import list_session_snapshots

            account_id = accounts.current_account_id()
            if not account_id:
                _json_response(self, 401, {"error": "not signed in"})
                return
            query = parse_qs(parsed_url.query)
            try:
                limit = int(query.get("limit", ["30"])[0] or 30)
            except ValueError:
                limit = 30
            _json_response(self, 200, {"sessions": list_session_snapshots(limit=limit, account_id=account_id)})
            return

        if path.startswith("/session-history/"):
            from gemia.session_history import load_session_snapshot

            account_id = accounts.current_account_id()
            if not account_id:
                _json_response(self, 401, {"error": "not signed in"})
                return
            snapshot_id = unquote(path.removeprefix("/session-history/")).strip()
            try:
                _json_response(self, 200, load_session_snapshot(snapshot_id, account_id=account_id, activate=True))
            except FileNotFoundError:
                _json_response(self, 404, {"error": "session not found"})
            except Exception as exc:
                _json_response(self, 500, {"error": str(exc)})
            return

        if path == "/media-library/list":
            from gemia.media_library import list_assets

            account_id = accounts.current_account_id()
            if not account_id:
                _json_response(self, 401, {"error": "not signed in"})
                return
            query = parse_qs(parsed_url.query)
            kind = str(query.get("kind", [""])[0] or "")
            q = str(query.get("q", [""])[0] or "")
            try:
                limit = int(query.get("limit", ["200"])[0] or 200)
            except ValueError:
                limit = 200
            _json_response(self, 200, {"assets": list_assets(account_id, kind=kind, q=q, limit=limit)})
            return

        if path.startswith("/media-library/file/"):
            from gemia.media_library import MediaLibraryError, resolve_asset_file

            account_id = accounts.current_account_id()
            if not account_id:
                _json_response(self, 401, {"error": "not signed in"})
                return
            parts = path.split("/")
            try:
                asset_id = parts[3] if len(parts) >= 5 else ""
                area = parts[4] if len(parts) >= 5 else ""
                filename = parts[5] if len(parts) >= 6 else None
                _file_response(self, resolve_asset_file(account_id, asset_id, area, filename), body=body)
            except MediaLibraryError as exc:
                _json_response(self, 404, {"error": str(exc)})
            return

        if path.startswith("/media-library/"):
            from gemia.media_library import get_asset

            account_id = accounts.current_account_id()
            if not account_id:
                _json_response(self, 401, {"error": "not signed in"})
                return
            parts = path.split("/")
            asset_id = parts[2] if len(parts) >= 3 else ""
            asset = get_asset(account_id, asset_id)
            if not asset:
                _json_response(self, 404, {"error": "media asset not found"})
                return
            _json_response(self, 200, {"asset": asset})
            return

        # Safe file serving: /file/outputs/..., /file/demo/...
        if path.startswith("/file/"):
            rel = path[len("/file/"):]
            # Reject traversal attempts
            parts_rel = Path(rel).parts
            if not parts_rel or parts_rel[0] not in _ALLOWED_ROOTS or ".." in parts_rel:
                _json_response(self, 403, {"error": "forbidden"})
                return
            resolved = (_BASE_DIR / rel).resolve()
            try:
                resolved.relative_to(_BASE_DIR.resolve())
            except ValueError:
                # Symlink (or otherwise) escaped the project root — refuse.
                _json_response(self, 403, {"error": "forbidden"})
                return
            _file_response(self, resolved, body=body)
            return

        if path == "/agents":
            _json_response(self, 200, {"agents": SubAgentRegistry.list_agents()})
            return

        if path == "/skills":
            # Load from skills_v2/ (preferred) with name+description from JSON
            skills_v2 = []
            for p in sorted(_SKILLS_V2_DIR.glob("*.json")):
                try:
                    data = json.loads(p.read_text())
                    skills_v2.append({
                        "id": p.stem,
                        "name": data.get("name", p.stem),
                        "description": data.get("description", ""),
                        "file": str(p),
                    })
                except Exception:
                    pass
            # Fallback: legacy skills/ dir
            legacy_skills = [
                {"id": p.stem, "name": p.stem, "description": "", "file": str(p)}
                for p in sorted(_SKILLS_DIR.glob("*.json"))
            ] if _SKILLS_DIR.exists() else []
            all_skills = skills_v2 + legacy_skills
            inputs = sorted(
                {
                    p.name: str(p.resolve())
                    for p in _INPUTS_DIR.glob("**/*")
                    if p.is_file()
                }.items()
            )
            _json_response(self, 200, {
                "skills": all_skills,
                "inputs": [
                    {"name": name, "path": abs_path}
                    for name, abs_path in inputs
                ]
            })
            return

        if path == "/tasks":
            items = []
            for p in sorted(_TASKS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
                try:
                    payload = _normalize_task_contract(json.loads(p.read_text()))
                    revisions = payload.get("revisions", [])
                    latest_revision = revisions[-1] if revisions else None
                    task_id = payload.get("task_id", p.stem)
                    goal = _goal_for_task(task_id) or payload.get("goal")
                    latest_feedback = latest_revision.get("feedback") if latest_revision else None
                    items.append({
                        "task_id": task_id,
                        "status": payload.get("status", "unknown"),
                        "plan_id": payload.get("plan_id"),
                        "created_at": payload.get("created_at"),
                        "outputs": payload.get("outputs", []),
                        "artifact_outputs": payload.get("artifact_outputs", []),
                        "revision_count": len(revisions),
                        "latest_feedback": latest_feedback,
                        "latest_style": latest_feedback or _style_from_goal(goal),
                        "latest_preview_task_id": latest_revision.get("revision_task_id") if latest_revision else task_id,
                        "goal": goal,
                    })
                except Exception:
                    continue
            _json_response(self, 200, {"tasks": items[:30]})
            return

        # /task/<task_id>  or  /task/<task_id>/assets  or  /task/<task_id>/progress
        parts = path.split("/")
        if len(parts) >= 3 and parts[1] == "task":
            task_id = parts[2]
            try:
                if len(parts) == 4 and parts[3] == "progress":
                    prog = _task_progress.get(task_id, {})
                    try:
                        task_data = _load_task_payload(task_id)
                        status = task_data.get("status", "running")
                    except FileNotFoundError:
                        status = "running" if task_id in _task_progress else "unknown"
                    _json_response(self, 200, {
                        "task_id": task_id,
                        "status": status,
                        "current_step": prog.get("current_step", 0),
                        "total_steps": prog.get("total_steps", 0),
                        "current_function": prog.get("current_function", ""),
                    })
                    return
                if len(parts) == 4 and parts[3] == "logs":
                    _json_response(self, 200, _logs_payload_for_task(task_id))
                    return
                if len(parts) == 4 and parts[3] == "assets":
                    _json_response(self, 200, _task_assets_payload(task_id))
                else:
                    task = _normalize_task_contract(_load_task_payload(task_id))
                    revisions = task.get("revisions", [])
                    latest_revision = revisions[-1] if revisions else None
                    goal = _goal_for_task(task_id) or task.get("goal")
                    latest_feedback = latest_revision.get("feedback") if latest_revision else None
                    task["revision_count"] = len(revisions)
                    task["latest_preview_task_id"] = latest_revision.get("revision_task_id") if latest_revision else task_id
                    task["goal"] = goal
                    task["latest_style"] = latest_feedback or _style_from_goal(goal)
                    _json_response(self, 200, task)
            except FileNotFoundError:
                _json_response(self, 404, {"error": f"task not found: {task_id}"})
            except Exception as exc:
                _json_response(self, 500, _error_payload(exc, context=f"/task/{task_id}"))
            return

        _json_response(self, 404, {"error": "not found"})

    def do_GET(self) -> None:  # noqa: N802
        if self._security_gate(mutating=False):
            return
        self._handle_get_like(body=True)

    def do_HEAD(self) -> None:  # noqa: N802
        if self._security_gate(mutating=False):
            return
        self._handle_get_like(body=False)

    def do_DELETE(self) -> None:  # noqa: N802
        if self._security_gate(mutating=True):
            return
        route = unquote(urlparse(self.path).path).rstrip("/")
        if route.startswith("/media-library/"):
            try:
                from gemia.media_library import MediaLibraryError, soft_delete_asset

                account_id = accounts.current_account_id()
                if not account_id:
                    _json_response(self, 401, {"error": "not signed in"})
                    return
                parts = route.split("/")
                asset_id = parts[2] if len(parts) >= 3 else ""
                _json_response(self, 200, {"asset": soft_delete_asset(account_id, asset_id)})
            except MediaLibraryError as exc:
                _json_response(self, 404, {"error": str(exc)})
            except Exception as exc:
                _json_response(self, 500, {"error": str(exc)})
            return
        _json_response(self, 404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self._security_gate(mutating=True):
            return
        route = unquote(urlparse(self.path).path).rstrip("/")
        # Lumeri v3 session HTTP surface.
        if route == "/sessions" or route.startswith("/sessions/"):
            from gemia.v3_routes import try_handle as _v3_try
            if _v3_try(self, method="POST"):
                return
        if route == "/auth/google/start":
            try:
                _json_response(self, 200, accounts.start_google_oauth())
            except Exception as exc:
                _json_response(self, 400, _error_payload(exc))
            return

        if route == "/auth/logout":
            accounts.sign_out()
            _json_response(self, 200, {"ok": True, **accounts.auth_session_payload()})
            return

        opencode_path = _opencode_compat_path(route)
        if opencode_path is not None:
            if not _vnext_enabled():
                _json_response(self, 404, {"error": "vNext runtime is disabled"})
                return
            try:
                from gemia.opencode_compat import prompt_payload_to_runtime

                post_query = parse_qs(urlparse(self.path).query)
                payload = _read_json_body(self)
                if opencode_path == "/session":
                    _json_response(self, 200, _opencode_create_session(payload, account_id=accounts.current_account_id()))
                    return
                parts = opencode_path.strip("/").split("/")
                if len(parts) >= 3 and parts[0] == "session":
                    session_id = parts[1]
                    action = parts[2]
                    if action == "prompt_async":
                        service = _runtime_service()
                        task_payload = prompt_payload_to_runtime(session_id, payload)
                        if "sync" in post_query:
                            _json_response(self, 200, service.post_message(task_payload))
                        else:
                            _start_runtime_message_task(service, task_payload)
                            _empty_response(self)
                        return
                    if action == "message":
                        service = _runtime_service()
                        task_payload = prompt_payload_to_runtime(session_id, payload)
                        if _runtime_message_sync_requested(task_payload, post_query):
                            result = service.post_message(task_payload)
                            messages = _opencode_compat_service().messages(session_id)
                            _json_response(self, 200, messages[-1] if messages else result)
                        else:
                            _start_runtime_message_task(service, task_payload)
                            messages = _opencode_compat_service().messages(session_id)
                            _json_response(self, 202, messages[-1] if messages else {"status": "accepted"})
                        return
                    if action == "shell":
                        _json_response(self, 200, _opencode_shell(session_id, payload))
                        return
                    if action == "command":
                        service = _runtime_service()
                        command_text = str(payload.get("command") or payload.get("message") or payload.get("name") or "").strip()
                        task_payload = {"session_id": session_id, "message": command_text}
                        _start_runtime_message_task(service, task_payload)
                        _empty_response(self)
                        return
                _json_response(self, 404, {"error": "opencode compatibility route not found"})
            except Exception as exc:
                _opencode_compat_error_response(self, exc)
            return

        if route.startswith("/runtime/"):
            if not _vnext_enabled():
                _json_response(self, 404, {"error": "vNext runtime is disabled"})
                return
            try:
                post_query = parse_qs(urlparse(self.path).query)
                payload = _read_json_body(self)
                if route == "/runtime/dev/workspace":
                    service = _creative_sandbox_service()
                    _json_response(self, 200, service.create_workspace(payload, account_id=accounts.current_account_id()))
                    return
                if route.startswith("/runtime/dev/workspace/"):
                    parts = route.split("/")
                    service = _creative_sandbox_service()
                    if len(parts) >= 6 and parts[1:4] == ["runtime", "dev", "workspace"]:
                        session_id = unquote(parts[4]).strip()
                        action = parts[5]
                        if action == "files":
                            _json_response(self, 200, service.write_file(session_id, payload))
                            return
                        if action == "command-events":
                            _json_response(self, 200, service.record_command_event(session_id, payload))
                            return
                        if action == "run":
                            command_id = f"cmd_{uuid.uuid4().hex[:12]}"
                            args = payload.get("args") or payload.get("command")
                            if isinstance(args, str):
                                args = [args]
                            if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
                                _json_response(
                                    self,
                                    400,
                                    {
                                        "status": "failed",
                                        "error": {
                                            "code": "invalid_command_args",
                                            "message": "args must be an argv array of strings",
                                        },
                                    },
                                )
                                return
                            service.append_event(
                                session_id,
                                "dev_command_started",
                                {
                                    "command_id": command_id,
                                    "command": " ".join(args)[:240],
                                    "label": str(payload.get("label") or ""),
                                    "executed": True,
                                },
                            )
                            from gemia.creative_sandbox_runner import CreativeSandboxRunner

                            runner = CreativeSandboxRunner(_BASE_DIR, session_id=session_id)
                            result = runner.run(
                                args,
                                cwd=payload.get("cwd"),
                                timeout_sec=float(payload.get("timeout_sec") or 30),
                                declared_artifact_paths=payload.get("declared_artifact_paths") or (),
                                command_id=command_id,
                            ).to_dict()
                            service.append_event(
                                session_id,
                                "dev_command_finished",
                                {
                                    "command_id": result.get("command_id"),
                                    "status": result.get("status"),
                                    "exit_code": result.get("exit_code"),
                                    "duration_ms": result.get("duration_ms"),
                                    "stdout_tail": result.get("stdout_tail"),
                                    "stderr_tail": result.get("stderr_tail"),
                                    "artifact_count": len(result.get("artifacts") or []),
                                    "executed": True,
                                },
                            )
                            for artifact in result.get("artifacts") or []:
                                service.append_event(
                                    session_id,
                                    "dev_artifact_ready",
                                    {
                                        "path": artifact.get("rel_path") or artifact.get("path"),
                                        "size": artifact.get("size"),
                                        "declared": artifact.get("declared"),
                                        "command_id": result.get("command_id"),
                                    },
                                )
                            _json_response(
                                self,
                                200,
                                {
                                    "status": "succeeded" if result.get("status") == "succeeded" else result.get("status"),
                                    "session_id": session_id,
                                    "result": result,
                                    "workspace": service.get_workspace(session_id).get("workspace"),
                                    "events": service.read_events(session_id),
                                    "artifacts": service.list_artifacts(session_id).get("artifacts", []),
                                    "preview": _creative_sandbox_preview_payload(service, session_id),
                                    "report": service.report(session_id),
                                },
                            )
                            return
                    _json_response(self, 404, {"error": "creative sandbox route not found"})
                    return
                service = _runtime_service()
                account_id = accounts.current_account_id()
                if route == "/runtime/session":
                    _json_response(self, 200, service.create_session(payload, account_id=account_id))
                    return
                if route == "/runtime/message":
                    if _runtime_message_sync_requested(payload, post_query):
                        _json_response(self, 200, service.post_message(payload))
                    else:
                        _json_response(self, 202, _start_runtime_message_task(service, payload))
                    return
                if route == "/runtime/approval":
                    _json_response(self, 200, service.approval(payload))
                    return
                if route == "/runtime/feedback":
                    _json_response(self, 200, service.feedback(payload))
                    return
                _json_response(self, 404, {"error": "runtime route not found"})
            except Exception as exc:
                if route.startswith("/runtime/dev/workspace"):
                    _creative_sandbox_error_response(self, exc)
                else:
                    _runtime_error_response(self, exc)
            return

        if route == "/agent-links/link":
            if _require_account(self) is None:
                return
            try:
                from gemia.agent_links import link_agent

                payload = _read_json_body(self)
                agent_id = str(payload.get("agent_id") or payload.get("agent") or "").strip()
                linked = bool(payload.get("linked", True))
                _json_response(self, 200, link_agent(agent_id, linked=linked))
            except Exception as exc:
                _json_response(self, 400, {"error": str(exc)})
            return

        if route == "/agent-links/message":
            if _require_account(self) is None:
                return
            try:
                from gemia.agent_links import send_message

                payload = _read_json_body(self)
                result = send_message(
                    sender=str(payload.get("sender") or "lumeri"),
                    target=str(payload.get("target") or ""),
                    message=str(payload.get("message") or ""),
                    invoke=bool(payload.get("invoke", False)),
                    cwd=_BASE_DIR,
                    timeout_seconds=int(payload.get("timeout_seconds") or 180),
                )
                _json_response(self, 200, result)
            except Exception as exc:
                _json_response(self, 400, {"error": str(exc)})
            return

        if route == "/agent-links/relay":
            if _require_account(self) is None:
                return
            try:
                from gemia.agent_links import relay_round

                payload = _read_json_body(self)
                result = relay_round(
                    message=str(payload.get("message") or ""),
                    first=str(payload.get("first") or "codex-lumeri"),
                    second=str(payload.get("second") or "gemini-lumeri"),
                    cwd=_BASE_DIR,
                    timeout_seconds=int(payload.get("timeout_seconds") or 180),
                )
                _json_response(self, 200, result)
            except Exception as exc:
                _json_response(self, 400, {"error": str(exc)})
            return

        if route in ("/upload-video", "/upload-media"):
            from gemia.media_library import MediaLibraryError, import_media, upload_response_for_asset
            from gemia.video.timeline_assets import SUPPORTED_MEDIA_EXTENSIONS

            account_id = accounts.current_account_id()
            if not account_id:
                _json_response(self, 401, {"error": "not signed in"})
                return
            filename = (self.headers.get("X-Filename") or "upload").strip()
            safe_name = Path(filename).name.strip() or "upload"
            ext = Path(safe_name).suffix.lower()
            if not ext:
                content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip()
                guessed = mimetypes.guess_extension(content_type) or ""
                if guessed == ".jpe":
                    guessed = ".jpg"
                if guessed in SUPPORTED_MEDIA_EXTENSIONS:
                    ext = guessed
                    safe_name = f"{safe_name}{ext}"
            if ext not in SUPPORTED_MEDIA_EXTENSIONS:
                allowed = ", ".join(sorted(SUPPORTED_MEDIA_EXTENSIONS))
                _json_response(self, 400, {"error": f"unsupported media type: {ext or 'unknown'}", "allowed_extensions": allowed})
                return
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0:
                _json_response(self, 400, {"error": "empty upload"})
                return
            incoming_dir = _INPUTS_DIR / ".incoming"
            incoming_dir.mkdir(parents=True, exist_ok=True)
            dest = incoming_dir / f"{uuid.uuid4().hex}{ext}"
            with dest.open("wb") as f:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            try:
                actual = dest.stat().st_size
                if actual != length:
                    # Reject truncated uploads instead of silently importing a
                    # half-written file that the user thinks is intact.
                    _json_response(self, 400, {
                        "error": "incomplete upload",
                        "expected_bytes": length,
                        "received_bytes": actual,
                    })
                    return
                asset = import_media(account_id, dest, original_name=safe_name)
                _json_response(self, 200, upload_response_for_asset(asset))
            except MediaLibraryError as exc:
                _json_response(self, 400, {"error": str(exc)})
            except Exception as exc:
                _json_response(self, 500, {"error": str(exc)})
            finally:
                try:
                    dest.unlink()
                except OSError:
                    pass
            return

        if route == "/config":
            # Save API keys to ~/.gemia/config.json and reload into env. The
            # first-run UI lets a logged-out user paste a key, so we only
            # require auth once any account has been provisioned locally.
            if accounts.list_accounts() and _require_account(self) is None:
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b"{}"
                body = json.loads(raw)
                cfg_dir = _CONFIG_PATH.parent
                cfg_dir.mkdir(parents=True, exist_ok=True)
                existing = {}
                if _CONFIG_PATH.exists():
                    try:
                        existing = json.loads(_CONFIG_PATH.read_text())
                    except Exception:
                        pass
                if key := body.get("openrouter_api_key", "").strip():
                    existing["openrouter_api_key"] = key
                    os.environ["OPENROUTER_API_KEY"] = key
                if key := body.get("gemini_api_key", "").strip():
                    existing["gemini_api_key"] = key
                    os.environ["GEMINI_API_KEY"] = key
                if key := body.get("image_api_key", "").strip():
                    existing["image_api_key"] = key
                    os.environ["GEMIA_IMAGE_API_KEY"] = key
                if key := body.get("nano_banana_api_key", "").strip():
                    existing["nano_banana_api_key"] = key
                    os.environ["GEMIA_IMAGE_API_KEY"] = key
                if value := body.get("image_base_url", "").strip():
                    existing["image_base_url"] = value
                    os.environ["GEMIA_IMAGE_BASE_URL"] = value
                if value := body.get("openrouter_image_url", "").strip():
                    existing["openrouter_image_url"] = value
                    os.environ["OPENROUTER_IMAGE_URL"] = value
                if value := body.get("image_model", "").strip():
                    if _legacy_image_model(value):
                        existing.pop("image_model", None)
                        os.environ.pop("GEMIA_IMAGE_MODEL", None)
                    else:
                        existing["image_model"] = value
                        os.environ["GEMIA_IMAGE_MODEL"] = value
                _CONFIG_PATH.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
                _json_response(self, 200, {"ok": True})
            except Exception as exc:
                _json_response(self, 500, {"error": str(exc)})
            return

        if route == "/dev-feedback":
            if _require_account(self) is None:
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b"{}"
                body = json.loads(raw)
                feedback = str(body.get("feedback", "")).strip()
                if not feedback:
                    _json_response(self, 400, {"error": "feedback is empty"})
                    return
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %z")
                dev_file = Path(__file__).parent / "dev_feedback.txt"
                with dev_file.open("a", encoding="utf-8") as f:
                    f.write(f"[PENDING] {ts}\n{feedback}\n---\n")
                _json_response(self, 200, {"ok": True})
            except Exception as exc:
                _json_response(self, 500, {"error": str(exc)})
            return

        if route == "/session-history":
            try:
                from gemia.session_history import save_current_session

                payload = _read_json_body(self)
                account_id = accounts.current_account_id()
                if not account_id:
                    _json_response(self, 401, {"error": "not signed in"})
                    return
                _json_response(self, 200, save_current_session(payload, account_id=account_id))
            except Exception as exc:
                _json_response(self, 500, {"error": str(exc)})
            return

        if route == "/project/normalize":
            account_id = _require_account(self)
            if account_id is None:
                return
            try:
                from gemia.project_model import normalize_project

                payload = _read_json_body(self)
                project = normalize_project(
                    payload.get("project") if isinstance(payload.get("project"), dict) else None,
                    project_state=payload.get("project_state") if isinstance(payload.get("project_state"), dict) else None,
                    account_id=account_id,
                )
                _json_response(self, 200, {"project": project})
            except Exception as exc:
                _json_response(self, 500, {"error": str(exc)})
            return

        if route == "/media-library/import":
            try:
                from gemia.media_library import MediaLibraryError, import_media, upload_response_for_asset

                payload = _read_json_body(self)
                account_id = accounts.current_account_id()
                if not account_id:
                    _json_response(self, 401, {"error": "not signed in"})
                    return
                source_path = str(payload.get("path") or payload.get("source_path") or "")
                original_name = str(payload.get("name") or "") or None
                asset = import_media(account_id, source_path, original_name=original_name)
                _json_response(self, 200, upload_response_for_asset(asset))
            except MediaLibraryError as exc:
                _json_response(self, 400, {"error": str(exc)})
            except Exception as exc:
                _json_response(self, 500, {"error": str(exc)})
            return

        if route.startswith("/media-library/") and route.endswith("/add-to-project"):
            try:
                from gemia.media_library import default_clip_for_asset, get_asset

                account_id = accounts.current_account_id()
                if not account_id:
                    _json_response(self, 401, {"error": "not signed in"})
                    return
                parts = route.split("/")
                asset_id = parts[2] if len(parts) >= 4 else ""
                asset = get_asset(account_id, asset_id)
                if not asset:
                    _json_response(self, 404, {"error": "media asset not found"})
                    return
                _json_response(self, 200, {"asset": asset, "clip": default_clip_for_asset(asset)})
            except Exception as exc:
                _json_response(self, 500, {"error": str(exc)})
            return

        if route in ("/video-summary", "/video-summary/batch"):
            account_id = _require_account(self)
            if account_id is None:
                return
            try:
                from gemia.video.summary import batch_summarize, video_summarize

                payload = _read_json_body(self)
                if route == "/video-summary":
                    video_path = str(payload.get("video_path") or payload.get("video") or "").strip()
                    if not video_path:
                        _json_response(self, 400, {"error": "video_path is required"})
                        return
                    if not _video_path_allowed(account_id, video_path):
                        _json_response(self, 403, {"error": "video_path is outside this account's media library"})
                        return
                    _json_response(self, 200, video_summarize(video_path))
                    return
                videos = payload.get("videos") or payload.get("video_list") or []
                if not isinstance(videos, list):
                    _json_response(self, 400, {"error": "videos is required"})
                    return
                cleaned = [str(item) for item in videos]
                bad = [v for v in cleaned if not _video_path_allowed(account_id, v)]
                if bad:
                    _json_response(self, 403, {"error": "video_list contains paths outside this account's media library", "rejected": bad})
                    return
                _json_response(self, 200, {"summaries": batch_summarize(cleaned)})
            except json.JSONDecodeError as exc:
                _json_response(self, 400, {"error": f"invalid JSON: {exc}"})
            except Exception as exc:
                _json_response(self, 500, {"error": str(exc)})
            return

        # /dev/claude and /dev/gemini-code were removed: both shelled out to a
        # CLI agent with full filesystem write powers and no auth/origin gate.
        # Use the local `claude` / `codex` CLI directly during development.

        if route == "/quick-action":
            account_id = _require_account(self)
            if account_id is None:
                return
            _qa_length = int(self.headers.get("Content-Length", 0))
            _qa_raw = self.rfile.read(_qa_length) if _qa_length else b"{}"
            try:
                payload = json.loads(_qa_raw)
            except json.JSONDecodeError as exc:
                _json_response(self, 400, {"error": f"invalid JSON: {exc}"})
                return
            action = str(payload.get("action", "")).strip()
            asset_id = str(payload.get("asset_id") or payload.get("assetId") or "").strip()
            # Legacy callers passed `video`/`video_path` directly; accept both
            # but require the path to live in this account's library or in
            # one of the project-local staging dirs (see _video_path_allowed).
            legacy_video = str(payload.get("video_path") or payload.get("video", "")).strip()
            if not action:
                _json_response(self, 400, {"error": "action is required"})
                return
            if not asset_id and not legacy_video:
                _json_response(self, 400, {"error": "asset_id is required"})
                return
            video: str = ""
            if asset_id:
                from gemia.media_library import get_asset
                asset = get_asset(account_id, asset_id)
                if not asset:
                    _json_response(self, 404, {"error": "media asset not found"})
                    return
                video = str(asset.get("storage_path") or asset.get("source_path") or "")
            else:
                if not _video_path_allowed(account_id, legacy_video):
                    _json_response(self, 403, {"error": "video path is outside this account's media library"})
                    return
                video = legacy_video
            if not video:
                _json_response(self, 400, {"error": "asset has no readable file"})
                return
            _QUICK_PLANS: dict[str, dict] = {
                "rotate_cw":  {"function": "gemia.video.timeline.rotate_video", "args": {"degrees": 90}},
                "rotate_ccw": {"function": "gemia.video.timeline.rotate_video", "args": {"degrees": 270}},
                "rotate_180": {"function": "gemia.video.timeline.rotate_video", "args": {"degrees": 180}},
                "flip_h":     {"function": "gemia.video.timeline.flip_video",   "args": {"direction": "horizontal"}},
                "flip_v":     {"function": "gemia.video.timeline.flip_video",   "args": {"direction": "vertical"}},
            }
            spec = _QUICK_PLANS.get(action)
            if not spec:
                _json_response(self, 400, {"error": f"unknown action: {action}"})
                return
            orch = GemiaOrchestrator()
            output_path = str((orch.outputs_dir / f"qa_{uuid.uuid4().hex[:8]}.mp4").resolve())
            plan = {
                "version": "2.0",
                "goal": action,
                "steps": [{"id": "step_1", "function": spec["function"], "args": spec["args"],
                           "input": "$input", "output": "$output"}],
                "input_path": video,
                "output_path": output_path,
            }
            try:
                task_id = orch.run_plan_dict(plan)
                _json_response(self, 200, {"task_id": task_id})
            except Exception as exc:
                _json_response(self, 500, {"error": str(exc)})
            return

        if route == "/merge-clips":
            account_id = _require_account(self)
            if account_id is None:
                return
            try:
                payload = _read_json_body(self)
                clips = payload.get("clips")
                if not isinstance(clips, list):
                    _json_response(self, 400, {"error": "clips is required"})
                    return
                project_state = payload.get("project_state") or payload.get("projectState")
                if not isinstance(project_state, dict):
                    project_state = {"clips": clips}
                events: list[dict] = []
                result = run_timeline_kept_clip_merge(
                    GemiaOrchestrator(),
                    clips=[item for item in clips if isinstance(item, dict)],
                    project_state=project_state,
                    account_id=account_id,
                    event_callback=events.append,
                )
                task_id = _write_agent_workflow_task(
                    prompt="合并保留片段",
                    result=result,
                    events=events,
                    project_state=project_state,
                )
                _json_response(self, 200, {"task_id": task_id})
            except json.JSONDecodeError as exc:
                _json_response(self, 400, {"error": f"invalid JSON: {exc}"})
            except Exception as exc:
                task_id = _write_failed_workflow_task(
                    prompt="合并保留片段",
                    exc=exc,
                    context="/merge-clips",
                )
                _json_response(self, 200, {"task_id": task_id})
            return

        if route not in ("/accounts/switch", "/run-skill", "/run-prompt") \
                and not route.startswith("/revise-task/") \
                and not route.startswith("/answer-ask/") \
                and not (route.startswith("/task/") and route.endswith("/feedback")):
            _json_response(self, 404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            _json_response(self, 400, {"error": f"invalid JSON: {exc}"})
            return

        if route == "/accounts/switch":
            account_id = str(payload.get("account_id") or "").strip()
            if not account_id:
                _json_response(self, 400, {"error": "account_id is required"})
                return
            try:
                account = accounts.switch_account(account_id)
                _json_response(self, 200, {"ok": True, "account": account, **accounts.auth_session_payload()})
            except Exception as exc:
                _json_response(self, 400, _error_payload(exc))
            return

        if route.startswith("/task/") and route.endswith("/feedback"):
            if _require_account(self) is None:
                return
            parts = route.split("/")
            task_id = parts[2] if len(parts) >= 4 else ""
            feedback = str(payload.get("feedback", "") or payload.get("text", "")).strip()
            if not task_id:
                _json_response(self, 400, {"error": "task_id is required"})
                return
            if not feedback:
                _json_response(self, 400, {"error": "feedback is required"})
                return
            try:
                updated, entry, revision_plan = _append_human_feedback(
                    task_id,
                    {
                        "feedback": feedback,
                        "render_pass_id": payload.get("render_pass_id") or payload.get("renderPassId") or "",
                        "layer_id": payload.get("layer_id") or payload.get("layerId") or "",
                        "time_range": payload.get("time_range") or payload.get("timeRange"),
                    },
                )
                updated_task = _normalize_task_contract(updated)
                _json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "task_id": task_id,
                        "feedback": entry,
                        "revision_plan": revision_plan,
                        "human_feedback_count": len(updated.get("human_feedback") or []),
                        "task": updated_task,
                    },
                )
            except FileNotFoundError as exc:
                _json_response(self, 404, {"error": str(exc)})
            except Exception as exc:
                _json_response(self, 500, {"error": str(exc)})
            return

        if route == "/run-skill":
            account_id = _require_account(self)
            if account_id is None:
                return
            skill_id = payload.get("skill_id")
            inputs = payload.get("inputs", {})
            # Support multi-select: input_paths overrides inputs["video"] when provided
            input_paths = payload.get("input_paths")
            if input_paths and isinstance(input_paths, list):
                inputs["video"] = input_paths  # engine handles list for concat
            if not skill_id:
                _json_response(self, 400, {"error": "skill_id is required"})
                return

            def _all_videos_allowed(value: object) -> bool:
                if isinstance(value, list):
                    return all(_video_path_allowed(account_id, str(item)) for item in value if str(item).strip())
                return _video_path_allowed(account_id, str(value or ""))

            check_video = inputs.get("video") or inputs.get("input_path", "")
            if check_video and not _all_videos_allowed(check_video):
                _json_response(self, 403, {"error": "video input is outside this account's media library"})
                return

            # Try skills_v2/ first (by slug matching), then fall back to legacy skills/
            skill_v2_path = _SKILLS_V2_DIR / f"{skill_id}.json"
            if skill_v2_path.exists():
                try:
                    from gemia.skill_store import SkillStore
                    from gemia.engine import PlanEngine
                    store = SkillStore()
                    skill_data = json.loads(skill_v2_path.read_text())
                    video_input = inputs.get("video") or inputs.get("input_path", "")
                    if not video_input:
                        _json_response(self, 400, {"error": "video input is required"})
                        return
                    orch = GemiaOrchestrator()
                    out_path = str((orch.outputs_dir / f"skill_{uuid.uuid4().hex[:8]}.mp4").resolve())
                    engine = PlanEngine()
                    engine.execute(skill_data["plan"], video_input, out_path)
                    task_id = f"task_{uuid.uuid4().hex[:12]}"
                    import datetime as _dt
                    task = {
                        "task_id": task_id,
                        "status": "succeeded",
                        "skill": skill_data.get("name", skill_id),
                        "outputs": [out_path],
                        "created_at": _dt.datetime.now().isoformat(),
                        "version": "2.0",
                    }
                    (_BASE_DIR / "tasks" / f"{task_id}.json").write_text(
                        json.dumps(task, ensure_ascii=False, indent=2) + "\n"
                    )
                    _json_response(self, 200, {"task_id": task_id})
                except FileNotFoundError as exc:
                    _json_response(self, 404, {"error": str(exc)})
                except Exception as exc:
                    _json_response(self, 500, {"error": str(exc)})
            else:
                try:
                    task_id = run_skill(skill_id, inputs)
                    _json_response(self, 200, {"task_id": task_id})
                except FileNotFoundError as exc:
                    _json_response(self, 404, {"error": str(exc)})
                except Exception as exc:
                    _json_response(self, 500, {"error": str(exc)})
            return

        if route == "/run-prompt":
            account_id = _require_account(self)
            if account_id is None:
                return
            prompt = str(payload.get("prompt", "")).strip()
            # Accept both "video" and "video_path" keys
            video = str(payload.get("video_path") or payload.get("video", "")).strip()
            if video and not _video_path_allowed(account_id, video):
                _json_response(self, 403, {"error": "video is outside this account's media library"})
                return
            agent = str(payload.get("agent", "")).strip() or None
            use_agent_workflow = _is_agent_workflow_request(payload) or not video
            execution_scope = str(payload.get("execution_scope", payload.get("executionScope", "auto")) or "auto")
            project_state = payload.get("project_state") or payload.get("projectState")
            if not isinstance(project_state, dict):
                project_state = None
            if not prompt:
                _json_response(self, 400, {"error": "prompt is required"})
                return
            if not video and not use_agent_workflow:
                _json_response(self, 400, {"error": "video is required"})
                return
            orch = GemiaOrchestrator()
            if use_agent_workflow:
                if bool(payload.get("stream_logs") or payload.get("live_logs")):
                    task_id = f"task_{uuid.uuid4().hex[:12]}"
                    created_at = datetime.now(timezone.utc).isoformat()
                    _write_live_agent_task_snapshot(
                        task_id=task_id,
                        created_at=created_at,
                        prompt=prompt,
                        events=[],
                        status="planning",
                        project_state=project_state,
                    )
                    worker = threading.Thread(
                        target=_run_agent_workflow_live_task,
                        kwargs={
                            "task_id": task_id,
                            "created_at": created_at,
                            "prompt": prompt,
                            "video": video,
                            "project_state": project_state,
                            "account_id": account_id,
                            "execution_scope": execution_scope,
                            "agent": agent,
                        },
                        daemon=True,
                    )
                    worker.start()
                    _json_response(self, 200, {"task_id": task_id, "live_logs": True})
                    return
                events: list[dict] = []
                try:
                    result = run_agent_workflow(
                        orch,
                        prompt=prompt,
                        input_path=video or None,
                        project_state=project_state,
                        account_id=accounts.current_account_id(),
                        scope=execution_scope,
                        agent=agent,
                        event_callback=events.append,
                    )
                except Exception as exc:
                    task_id = _write_failed_workflow_task(
                        prompt=prompt,
                        events=events,
                        project_state=project_state,
                        exc=exc,
                        context="/run-prompt.agent_workflow",
                    )
                    _json_response(self, 200, {"task_id": task_id})
                    return
                if result.get("ask"):
                    import time as _time
                    ask_id = uuid.uuid4().hex[:12]
                    session = result.get("_pending_ask_session") if isinstance(result.get("_pending_ask_session"), dict) else {}
                    _pending_asks[ask_id] = {
                        **session,
                        "prompt": prompt,
                        "video": session.get("video") or video,
                        "project_state": session.get("project_state") if isinstance(session.get("project_state"), dict) else project_state,
                        "agent": session.get("agent") or agent,
                        "execution_scope": session.get("execution_scope") or execution_scope,
                        "execution_mode": "agent_loop",
                        "agent_events": events,
                        "ask_rounds": 1,
                        "account_id": account_id,
                        "created_at_ts": _time.time(),
                    }
                    _json_response(self, 200, {
                        "ask": True,
                        "ask_id": ask_id,
                        "questions": result.get("questions") or (result.get("pending_ask") or {}).get("questions", []),
                    })
                    return
                task_id = _write_agent_workflow_task(
                    prompt=prompt,
                    result=result,
                    events=events,
                    project_state=project_state,
                )
                _json_response(self, 200, {"task_id": task_id})
                return

            output_path = str((orch.outputs_dir / f"ai_{uuid.uuid4().hex[:8]}.mp4").resolve())
            try:
                result = orch.plan_from_primitives(
                    prompt,
                    input_path=video,
                    output_path=output_path,
                    agent=agent,
                    project_state=project_state,
                )
            except Exception as exc:
                task_id = _write_failed_workflow_task(
                    prompt=prompt,
                    exc=exc,
                    project_state=project_state,
                    context="/run-prompt.plan_from_primitives",
                )
                _json_response(self, 200, {"task_id": task_id})
                return
            if result.get("ask"):
                import time as _time
                ask_id = uuid.uuid4().hex[:12]
                _pending_asks[ask_id] = {
                    "prompt": prompt,
                    "video": video,
                    "output_path": output_path,
                    "project_state": project_state,
                    "agent": agent,
                    "ask_rounds": 1,
                    "account_id": account_id,
                    "created_at_ts": _time.time(),
                }
                _json_response(self, 200, {
                    "ask": True,
                    "ask_id": ask_id,
                    "questions": result.get("questions", []),
                })
            else:
                try:
                    result.setdefault("input_path", video)
                    result.setdefault("output_path", output_path)
                    # Reserve task_id slot for progress tracking
                    _tmp_task_id = f"task_pending_{uuid.uuid4().hex[:8]}"

                    def _on_step(current: int, total: int, fn: str, _tid: list = [_tmp_task_id]) -> None:
                        _task_progress[_tid[0]] = {"current_step": current, "total_steps": total, "current_function": fn}

                    task_id = orch.run_plan_dict(result, progress_callback=_on_step)
                    # Update progress key to real task_id
                    if _tmp_task_id in _task_progress:
                        _task_progress[task_id] = _task_progress.pop(_tmp_task_id)
                    _json_response(self, 200, {"task_id": task_id})
                except Exception as exc:
                    task_id = _write_failed_workflow_task(
                        prompt=prompt,
                        exc=exc,
                        project_state=project_state,
                        context="/run-prompt.run_plan_dict",
                    )
                    _json_response(self, 200, {"task_id": task_id})
            return

        if route.startswith("/answer-ask/"):
            ask_id = route.split("/")[-1]
            session = _resolve_pending_ask(self, ask_id)
            if session is None:
                return
            account_id = accounts.current_account_id() or session.get("account_id") or ""
            answers = payload.get("answers") or {}
            if isinstance(answers, str):
                answers = {"answer": answers}
            orch = GemiaOrchestrator()
            if session.get("execution_mode") == "agent_loop":
                events = list(session.get("agent_events") if isinstance(session.get("agent_events"), list) else [])
                try:
                    result = run_agent_workflow(
                        orch,
                        prompt=session["prompt"],
                        input_path=session.get("video") or None,
                        answers=answers,
                        project_state=session.get("project_state") if isinstance(session.get("project_state"), dict) else None,
                        account_id=accounts.current_account_id(),
                        scope=str(session.get("execution_scope") or "auto"),
                        agent=session.get("agent"),
                        event_callback=events.append,
                    )
                except Exception as exc:
                    _pending_asks.pop(ask_id, None)
                    task_id = _write_failed_workflow_task(
                        prompt=session["prompt"],
                        events=events,
                        project_state=session.get("project_state") if isinstance(session.get("project_state"), dict) else None,
                        exc=exc,
                        context="/answer-ask.agent_workflow",
                    )
                    _json_response(self, 200, {"task_id": task_id})
                    return
                if result.get("ask"):
                    questions = result.get("questions") or (result.get("pending_ask") or {}).get("questions", [])
                    if int(session.get("ask_rounds") or 1) >= 1:
                        events.append(_stop_repeated_ask_event(questions))
                        _pending_asks.pop(ask_id, None)
                        task_id = _write_agent_workflow_task(
                            prompt=session["prompt"],
                            result={"goal": session["prompt"], "outputs": []},
                            events=events,
                            status="failed",
                            project_state=session.get("project_state") if isinstance(session.get("project_state"), dict) else None,
                        )
                        _json_response(self, 200, {"task_id": task_id})
                        return
                    import time as _time
                    next_session = result.get("_pending_ask_session") if isinstance(result.get("_pending_ask_session"), dict) else {}
                    _pending_asks[ask_id] = {
                        **session,
                        **next_session,
                        "execution_mode": "agent_loop",
                        "agent_events": events,
                        "ask_rounds": int(session.get("ask_rounds") or 1) + 1,
                        "account_id": account_id,
                        "created_at_ts": _time.time(),
                    }
                    _json_response(self, 200, {
                        "ask": True,
                        "ask_id": ask_id,
                        "questions": questions,
                    })
                    return
                _pending_asks.pop(ask_id, None)
                task_id = _write_agent_workflow_task(
                    prompt=session["prompt"],
                    result=result,
                    events=events,
                    project_state=session.get("project_state") if isinstance(session.get("project_state"), dict) else None,
                )
                _json_response(self, 200, {"task_id": task_id})
                return

            try:
                result = orch.plan_from_primitives(
                    session["prompt"],
                    input_path=session["video"],
                    output_path=session["output_path"],
                    answers=answers,
                    agent=session.get("agent"),
                    project_state=session.get("project_state") if isinstance(session.get("project_state"), dict) else None,
                )
            except Exception as exc:
                _pending_asks.pop(ask_id, None)
                task_id = _write_failed_workflow_task(
                    prompt=session["prompt"],
                    exc=exc,
                    project_state=session.get("project_state") if isinstance(session.get("project_state"), dict) else None,
                    context="/answer-ask.plan_from_primitives",
                )
                _json_response(self, 200, {"task_id": task_id})
                return
            if result.get("ask"):
                questions = result.get("questions", [])
                if int(session.get("ask_rounds") or 1) >= 1:
                    _pending_asks.pop(ask_id, None)
                    task_id = f"task_{uuid.uuid4().hex[:12]}"
                    task = {
                        "task_id": task_id,
                        "status": "failed",
                        "goal": session["prompt"],
                        "outputs": [],
                        "agent_events": [_stop_repeated_ask_event(questions)],
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "version": "2.0",
                    }
                    _task_file(task_id).write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n")
                    _json_response(self, 200, {"task_id": task_id})
                    return
                # Still unclear — return new questions with same ask_id
                _pending_asks[ask_id] = session
                _json_response(self, 200, {
                    "ask": True,
                    "ask_id": ask_id,
                    "questions": questions,
                })
                return
            _pending_asks.pop(ask_id, None)
            try:
                result.setdefault("input_path", session["video"])
                result.setdefault("output_path", session["output_path"])
                task_id = orch.run_plan_dict(result)
                _json_response(self, 200, {"task_id": task_id})
            except Exception as exc:
                task_id = _write_failed_workflow_task(
                    prompt=session["prompt"],
                    exc=exc,
                    project_state=session.get("project_state") if isinstance(session.get("project_state"), dict) else None,
                    context="/answer-ask.run_plan_dict",
                )
                _json_response(self, 200, {"task_id": task_id})
            return

        if route.startswith("/revise-task/"):
            if _require_account(self) is None:
                return
            task_id = route.split("/")[-1]
            feedback = str(payload.get("feedback", "")).strip()
            if not feedback:
                _json_response(self, 400, {"error": "feedback is required"})
                return

            try:
                plan = _load_plan_payload(task_id)
                skill_id = plan.get("skill_id")
                input_path = plan.get("input_path") or (plan.get("inputs") or {}).get("video")
                if not skill_id or not input_path:
                    raise ValueError("original plan is missing skill_id or input_path")
                revision_task_id = run_skill(skill_id, {"video": input_path, "style": feedback})
                revision_task = get_task(revision_task_id)
                updated = _append_revision(task_id, {
                    "revision_task_id": revision_task_id,
                    "feedback": feedback,
                    "created_at": revision_task.get("created_at"),
                    "outputs": revision_task.get("outputs", []),
                    "status": revision_task.get("status", "unknown")
                })
                _json_response(self, 200, {
                    "task_id": task_id,
                    "revision_task_id": revision_task_id,
                    "revision_count": len(updated.get("revisions", [])),
                    "status": "succeeded"
                })
            except FileNotFoundError as exc:
                _json_response(self, 404, {"error": str(exc)})
            except Exception as exc:
                _json_response(self, 500, {"error": str(exc)})
            return

        _json_response(self, 404, {"error": "not found"})


def main(host: str | None = None, port: int | None = None) -> None:
    _load_config_keys()  # Load API keys from ~/.gemia/config.json on startup
    host = host or _configured_server_host()
    port = int(port or os.environ.get("LUMERI_PORT") or os.environ.get("GEMIA_PORT") or 7788)
    os.environ["GEMIA_HOST"] = host
    os.environ["GEMIA_PORT"] = str(port)
    server = ThreadingHTTPServer((host, port), _Handler)
    server.daemon_threads = True
    print(f"Lumeri server listening on http://{host}:{port}")
    for url in _server_urls(host, port):
        print(f"  available at {url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Gemia MVP local HTTP server")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    main(args.host, args.port)
