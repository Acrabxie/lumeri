"""Minimal local HTTP server for Gemia MVP.

Endpoints:
  GET  /                        → Lumeri v3 web UI (static/v3/index.html)
  GET  /v3/<rel-path>           → Lumeri v3 frontend assets (static/v3/)
  GET  /sessions, POST /sessions/… → Lumeri v3 session surface (gemia/v3_routes.py)
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
  POST /runtime/dev/workspace   → gated Creative Dev Sandbox workspace
  POST /runtime/dev/workspace/<id>/run → gated Creative Dev Sandbox command runner
  GET  /skills
"""

from __future__ import annotations

import json
import hashlib
import mimetypes
import os
import re
import socket
import subprocess
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from gemia.model_strength import strongest_media_model


_CONFIG_PATH = Path.home() / ".gemia" / "config.json"
_DEFAULT_IMAGE_MODEL = strongest_media_model("image", "openrouter")
_DEFAULT_IMAGE_BASE_URL = "https://openrouter.ai/api/v1"


def _legacy_image_model(value: object) -> bool:
    lowered = str(value or "").strip().lower()
    return lowered in {"gpt-image-2", "gpt_image2", "gpt image2"}


def _configured_image_model() -> str:
    value = os.environ.get("GEMIA_IMAGE_MODEL") or ""
    candidate = "" if _legacy_image_model(value) else value
    return strongest_media_model("image", "openrouter", (candidate, _DEFAULT_IMAGE_MODEL))


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
    """Return the acting account_id or send 401 and return None.

    Resolution goes through gemia.identity (per-request X-Lumeri-Account pin
    first, process-global active.json as fallback) so one client switching
    accounts no longer retargets every other open client."""
    account_id = identity.resolve_account_id(handler)
    if not account_id:
        _json_response(handler, 401, {"error": "not signed in"})
        return None
    return account_id


def _video_path_allowed(account_id: str | None, video: str) -> bool:
    """Reject media paths that don't live in this account's library or in
    the project-local input/output staging dirs. Used by /video-summary to
    keep unauthenticated callers (or swapped accounts) from coercing ffmpeg
    into reading another user's media originals.
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
from gemia import identity
from gemia.artifacts import artifact_outputs as _artifact_outputs
from gemia.artifacts import is_document_artifact_output as _is_document_artifact_output
from gemia.artifacts import is_media_output as _is_media_output
from gemia.artifacts import is_video_output as _is_video_output
from gemia.artifacts import media_outputs as _media_outputs
from gemia.artifacts import output_paths as _output_paths
from gemia.stability import (
    TASK_STATUSES,
    error_envelope as _stability_error_envelope,
    error_event as _stability_error_event,
    normalize_task_status as _normalize_task_status,
    stability_gate_enabled as _stability_gate_enabled,
)
from gemia.ai.sub_agents import SubAgentRegistry
from lumerai.sandbox import sandbox_ctx as _sandbox_ctx
from gemia.sandbox_v4 import set_sandbox_disabled as _set_v4_sandbox_disabled, is_sandbox_disabled as _is_v4_sandbox_disabled

# In-memory store for pending ask sessions. Each entry MUST carry account_id
# and created_at so that account-switch cannot let user B answer user A's ask.
# In-memory store for task execution progress {task_id: {current_step, total_steps, current_function}}
_BASE_DIR = Path(__file__).resolve().parent
_SKILLS_DIR = _BASE_DIR / "skills"
_SKILLS_V2_DIR = _BASE_DIR / "skills_v2"
_STATIC_DIR = _BASE_DIR / "static"
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


def _vnext_enabled() -> bool:
    return os.environ.get("LUMERAI_VNEXT", "0") == "1"


def _creative_sandbox_service():
    from gemia.creative_sandbox import CreativeSandboxService

    return CreativeSandboxService(_BASE_DIR)


def _creative_sandbox_error_response(handler: BaseHTTPRequestHandler, exc: Exception) -> None:
    from gemia.creative_sandbox import creative_sandbox_error_payload

    status, payload = creative_sandbox_error_payload(exc)
    _json_response(handler, status, payload)


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
    v3_root = _STATIC_DIR / "v3"
    missing = [name for name in ("index.html", "v3.js", "v3.css") if not (v3_root / name).exists()]
    if missing:
        return False, "missing v3 frontend files: " + ", ".join(missing)
    return True, str(v3_root / "index.html")


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

        # Web UI — default page serves the Lumeri v3 frontend (static/v3/).
        if path == "/":
            _file_response(self, _STATIC_DIR / "v3" / "index.html", body=body)
            return

        if path == "/favicon.ico":
            _empty_response(self)
            return

        if path == "/health":
            _json_response(self, 200, _health_payload())
            return

        if path == "/settings/sandbox":
            _json_response(self, 200, {"sandbox_disabled": _is_v4_sandbox_disabled()})
            return
        # Lumeri v3 session HTTP surface (sessions / turn / assets / stream).
        if path == "/sessions" or path.startswith("/sessions/"):
            from gemia.v3_routes import try_handle as _v3_try
            if _v3_try(self, method=("GET" if body else "HEAD")):
                return
        # Read-only file browsing for the web UI (whitelisted roots only).
        if path.startswith("/files/"):
            from gemia.file_browse_routes import try_handle as _files_try
            if _files_try(
                self,
                method=("GET" if body else "HEAD"),
                serve_file=lambda p: _file_response(self, p, body=body),
            ):
                return
        # Quanta (discrete video) interactive demo.
        if path == "/quanta" or path.startswith("/quanta/"):
            rel = "index.html" if path in ("/quanta", "/quanta/") else path[len("/quanta/"):]
            quanta_root = (Path(__file__).resolve().parent / "static" / "v3" / "quanta").resolve()
            target = _safe_child_path(quanta_root, rel)
            if target is None:
                _json_response(self, 404, {"error": "quanta asset not found"})
                return
            _file_response(self, target, body=body)
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
            if identity.resolve_account_id(self):
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
                # 搜索引擎配置状态（密钥脱敏，供 Setup 面板渲染）。
                try:
                    _cfg_search = {}
                    if _CONFIG_PATH.exists():
                        _cfg_search = json.loads(_CONFIG_PATH.read_text())
                    payload["search"] = {
                        "provider": _cfg_search.get("search_provider", "auto"),
                        "has_key": {
                            "tavily": bool(_cfg_search.get("tavily_api_key")),
                            "brave": bool(_cfg_search.get("brave_api_key")),
                            "serper": bool(_cfg_search.get("serper_api_key")),
                            "exa": bool(_cfg_search.get("exa_api_key")),
                            "bing": bool(_cfg_search.get("bing_api_key")),
                            "google_cse": bool(_cfg_search.get("google_cse_key") and _cfg_search.get("google_cse_id")),
                            "searxng": bool(_cfg_search.get("searxng_url")),
                        },
                    }
                except Exception:
                    pass
                # 大脑 provider 现状（密钥脱敏，供 Setup 面板渲染）。
                try:
                    from gemia import brain_config
                    _cfg = {}
                    if _CONFIG_PATH.exists():
                        _cfg = json.loads(_CONFIG_PATH.read_text())
                    payload["brain"] = brain_config.read_status(_cfg)
                except Exception:
                    pass
            _json_response(self, 200, payload)
            return

        if path == "/model":
            from gemia.memory import model_selection_payload

            _json_response(self, 200, model_selection_payload("planner"))
            return

        if path == "/auth/session":
            _json_response(self, 200, accounts.auth_session_payload())
            return

        if path == "/accounts":
            _json_response(self, 200, {"accounts": accounts.list_accounts(), **accounts.auth_session_payload()})
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

            account_id = identity.resolve_account_id(self)
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

            try:
                account_id = identity.resolve_account_id(self)
            except Exception:
                account_id = None
            _json_response(self, 200, load_current_session(account_id=account_id))
            return

        if path == "/session-history/list":
            from gemia.session_history import list_session_snapshots

            try:
                account_id = identity.resolve_account_id(self)
            except Exception:
                account_id = None
            query = parse_qs(parsed_url.query)
            try:
                limit = int(query.get("limit", ["30"])[0] or 30)
            except ValueError:
                limit = 30
            _json_response(self, 200, {"sessions": list_session_snapshots(limit=limit, account_id=account_id)})
            return

        if path.startswith("/session-history/"):
            from gemia.session_history import load_session_snapshot

            try:
                account_id = identity.resolve_account_id(self)
            except Exception:
                account_id = None
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

            account_id = identity.resolve_account_id(self)
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

        if path.startswith("/media-library/") and path.endswith("/annotations"):
            from gemia.media_annotations import MediaAnnotationError, list_annotations

            account_id = identity.resolve_account_id(self)
            if not account_id:
                _json_response(self, 401, {"error": "not signed in"})
                return
            parts = path.split("/")
            asset_id = parts[2] if len(parts) >= 4 else ""
            try:
                _json_response(self, 200, {"annotations": list_annotations(account_id, asset_id)})
            except MediaAnnotationError as exc:
                _json_response(self, 404, {"error": str(exc)})
            return

        if path.startswith("/media-library/file/"):
            from gemia.media_library import MediaLibraryError, resolve_asset_file

            account_id = identity.resolve_account_id(self)
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

            account_id = identity.resolve_account_id(self)
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
        if route.startswith("/media-library/") and "/annotations/" in route:
            try:
                from gemia.media_annotations import MediaAnnotationError, delete_annotation

                account_id = identity.resolve_account_id(self)
                if not account_id:
                    _json_response(self, 401, {"error": "not signed in"})
                    return
                parts = route.split("/")
                asset_id = parts[2] if len(parts) >= 5 else ""
                annotation_id = parts[4] if len(parts) >= 5 else ""
                _json_response(self, 200, {"annotation": delete_annotation(account_id, asset_id, annotation_id)})
            except MediaAnnotationError as exc:
                _json_response(self, 404, {"error": str(exc)})
            except Exception as exc:
                _json_response(self, 500, {"error": str(exc)})
            return
        if route.startswith("/media-library/"):
            try:
                from gemia.media_library import MediaLibraryError, soft_delete_asset

                account_id = identity.resolve_account_id(self)
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

        if route == "/auth/email/start":
            try:
                payload = _read_json_body(self)
                _json_response(self, 200, accounts.start_email_login(payload.get("email", "")))
            except Exception as exc:
                _json_response(self, 400, _error_payload(exc))
            return

        if route == "/auth/email/verify":
            try:
                payload = _read_json_body(self)
                profile = accounts.verify_email_login(payload.get("email", ""), payload.get("code", ""))
                _json_response(self, 200, {"ok": True, "account": profile, **accounts.auth_session_payload()})
            except Exception as exc:
                _json_response(self, 400, _error_payload(exc))
            return

        if route == "/model":
            try:
                from gemia.memory import apply_model_selection, model_selection_payload, strongest_model_lock

                payload = _read_json_body(self) or {}
                if strongest_model_lock("planner").get("enabled"):
                    _json_response(self, 423, {"error": "模型已强制锁定为最强配置，不能降级或切换"})
                    return
                apply_model_selection(payload, "planner")
                _json_response(self, 200, {"ok": True, **model_selection_payload("planner")})
            except ValueError as exc:
                _json_response(self, 400, {"error": str(exc)})
            except Exception as exc:
                _json_response(self, 500, _error_payload(exc))
            return

        if route == "/model/add":
            try:
                from gemia.memory import add_model_to_catalog, model_selection_payload, strongest_model_lock

                if strongest_model_lock("planner").get("enabled"):
                    _json_response(self, 423, {"error": "模型已锁定为最强配置，不能添加或切换"})
                    return

                payload = _read_json_body(self) or {}
                model_id = payload.get("id", "").strip()
                if not model_id:
                    _json_response(self, 400, {"error": "missing model id"})
                    return
                add_model_to_catalog(
                    model_id,
                    label=payload.get("label", ""),
                    provider=payload.get("provider", ""),
                    slot="planner",
                )
                _json_response(self, 200, {"ok": True, **model_selection_payload("planner")})
            except ValueError as exc:
                _json_response(self, 400, {"error": str(exc)})
            except Exception as exc:
                _json_response(self, 500, _error_payload(exc))
            return

        if route == "/model/remove":
            try:
                from gemia.memory import model_selection_payload, remove_model_from_catalog, strongest_model_lock

                if strongest_model_lock("planner").get("enabled"):
                    _json_response(self, 423, {"error": "模型已锁定为最强配置，不能删除或切换"})
                    return

                payload = _read_json_body(self) or {}
                model_id = payload.get("id", "").strip()
                if not model_id:
                    _json_response(self, 400, {"error": "missing model id"})
                    return
                remove_model_from_catalog(model_id, slot="planner")
                _json_response(self, 200, {"ok": True, **model_selection_payload("planner")})
            except ValueError as exc:
                _json_response(self, 400, {"error": str(exc)})
            except Exception as exc:
                _json_response(self, 500, _error_payload(exc))
            return

        if route == "/auth/logout":
            accounts.sign_out()
            _json_response(self, 200, {"ok": True, **accounts.auth_session_payload()})
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
                    _json_response(self, 200, service.create_workspace(payload, account_id=identity.resolve_account_id(self)))
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
                _json_response(self, 404, {"error": "runtime route not found"})
            except Exception as exc:
                _creative_sandbox_error_response(self, exc)
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

            account_id = identity.resolve_account_id(self)
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
                        strongest = strongest_media_model("image", "openrouter", (value,))
                        existing["image_model"] = strongest
                        os.environ["GEMIA_IMAGE_MODEL"] = strongest
                # 搜索引擎字段（白名单合并）。
                _SEARCH_CONFIG_KEYS = (
                    "search_provider", "tavily_api_key", "brave_api_key",
                    "serper_api_key", "exa_api_key", "bing_api_key",
                    "google_cse_key", "google_cse_id",
                    "searxng_url", "searxng_api_key",
                )
                for sk in _SEARCH_CONFIG_KEYS:
                    if sk in body:
                        v = str(body[sk]).strip() if body[sk] else ""
                        if v:
                            existing[sk] = v
                        else:
                            existing.pop(sk, None)
                # 大脑 provider 字段（白名单合并 + 即时设 env）。
                try:
                    from gemia import brain_config
                    brain_config.apply_update(existing, body)
                except Exception:
                    pass
                _CONFIG_PATH.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
                _json_response(self, 200, {"ok": True})
            except Exception as exc:
                _json_response(self, 500, {"error": str(exc)})
            return

        if route == "/config/list-models":
            if accounts.list_accounts() and _require_account(self) is None:
                return
            try:
                from gemia import brain_config
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                cfg = {}
                if _CONFIG_PATH.exists():
                    cfg = json.loads(_CONFIG_PATH.read_text())
                if body:
                    brain_config.apply_update(cfg, body)
                proxy = os.environ.get("HTTPS_PROXY") or ""
                if not proxy:
                    try:
                        proxy = json.loads(_CONFIG_PATH.read_text()).get("proxy") or ""
                    except Exception:
                        proxy = ""
                pv = body.get("provider") or os.environ.get("LUMERI_V3_PROVIDER") or "openai"
                result = brain_config.list_models(pv, cfg, proxy=proxy or None)
                _json_response(self, 200, result)
            except Exception as exc:
                _json_response(self, 500, {"ok": False, "error": str(exc), "models": []})
            return

        if route == "/config/test-brain":
            # 用当前配置发极小探针，验证 provider 连通与鉴权（Setup 面板的"测试连接"）。
            if accounts.list_accounts() and _require_account(self) is None:
                return
            try:
                from gemia import brain_config
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b"{}"
                body = json.loads(raw) if raw.strip() else {}
                # 允许先带上未存盘的字段临时应用（仅设 env，不写盘），测完即真实反映当前 env。
                if body:
                    brain_config.apply_update({}, body)
                proxy = os.environ.get("HTTPS_PROXY") or ""
                if not proxy and _CONFIG_PATH.exists():
                    try:
                        proxy = json.loads(_CONFIG_PATH.read_text()).get("proxy") or ""
                    except Exception:
                        proxy = ""
                result = brain_config.test_provider(proxy=proxy or None)
                _json_response(self, 200, result)
            except Exception as exc:
                _json_response(self, 500, {"ok": False, "error": str(exc)})
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
                try:
                    account_id = identity.resolve_account_id(self)
                except Exception:
                    account_id = None
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
                account_id = identity.resolve_account_id(self)
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

        if route == "/media-library/annotate":
            try:
                from gemia.media_annotations import MediaAnnotationError, annotate_asset_heuristic
                from gemia.media_library import list_assets

                payload = _read_json_body(self)
                account_id = identity.resolve_account_id(self)
                if not account_id:
                    _json_response(self, 401, {"error": "not signed in"})
                    return
                asset_ids = payload.get("asset_ids") or payload.get("assets") or []
                if isinstance(asset_ids, str):
                    asset_ids = [asset_ids]
                if not isinstance(asset_ids, list):
                    _json_response(self, 400, {"error": "asset_ids must be a list"})
                    return
                if not asset_ids and payload.get("all"):
                    asset_ids = [asset.get("asset_id") for asset in list_assets(account_id, kind="video", limit=int(payload.get("max_assets") or 20))]
                if not asset_ids:
                    _json_response(self, 400, {"error": "asset_ids is required"})
                    return
                max_assets = max(1, min(int(payload.get("max_assets") or len(asset_ids)), 100))
                results = []
                for asset_id in [str(item) for item in asset_ids[:max_assets]]:
                    results.append(
                        annotate_asset_heuristic(
                            account_id,
                            asset_id,
                            mode=str(payload.get("mode") or "quick"),
                            language=str(payload.get("language") or "auto"),
                            tags=payload.get("tags") if isinstance(payload.get("tags"), list) else None,
                            replace_existing=bool(payload.get("replace_existing", True)),
                        )
                    )
                _json_response(self, 200, {"results": results, "asset_count": len(results)})
            except MediaAnnotationError as exc:
                _json_response(self, 404, {"error": str(exc)})
            except Exception as exc:
                _json_response(self, 500, {"error": str(exc)})
            return

        if route.startswith("/media-library/") and "/annotations" in route:
            try:
                from gemia.media_annotations import MediaAnnotationError, create_annotation, update_annotation

                payload = _read_json_body(self)
                account_id = identity.resolve_account_id(self)
                if not account_id:
                    _json_response(self, 401, {"error": "not signed in"})
                    return
                parts = route.split("/")
                asset_id = parts[2] if len(parts) >= 4 else ""
                if len(parts) >= 5 and parts[3] == "annotations" and parts[4]:
                    _json_response(self, 200, {"annotation": update_annotation(account_id, asset_id, parts[4], payload)})
                    return
                if len(parts) >= 4 and parts[3] == "annotations":
                    _json_response(self, 200, {"annotation": create_annotation(account_id, asset_id, payload)})
                    return
                _json_response(self, 404, {"error": "not found"})
            except MediaAnnotationError as exc:
                _json_response(self, 404, {"error": str(exc)})
            except Exception as exc:
                _json_response(self, 500, {"error": str(exc)})
            return

        if route.startswith("/media-library/") and route.endswith("/add-to-project"):
            try:
                from gemia.media_library import default_clip_for_asset, get_asset

                account_id = identity.resolve_account_id(self)
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

        if route == "/settings/sandbox":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) if length else b"{}")
            _set_v4_sandbox_disabled(bool(body.get("disabled", False)))
            _json_response(self, 200, {"sandbox_disabled": _is_v4_sandbox_disabled()})
            return

        if route != "/accounts/switch":
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

        _json_response(self, 404, {"error": "not found"})


def main(host: str | None = None, port: int | None = None) -> None:
    # First-run onboarding: if no usable model provider is configured, prompt
    # interactively (TTY) or print instructions and exit cleanly (headless).
    # When a provider is already configured this is a no-op, so existing
    # startup behaviour is unchanged.
    from gemia.onboarding import ensure_onboarded

    if not ensure_onboarded():
        # Headless + unconfigured: instructions already printed. Do NOT bind a
        # brain-less server.
        return

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
