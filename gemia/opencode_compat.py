"""OpenCode-compatible API adapter for Lumeri vNext.

The goal of this module is not to vendor OpenCode. It gives Lumeri the same
server contract shape that OpenCode's web/TUI clients are built around:
sessions, messages with parts, event streams, file reads, and shell tool calls.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .creative_sandbox import CreativeSandboxService
from .runtime_vnext import RuntimeService

_SKIP_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".DS_Store",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    "target",
    "dist",
}
_SECRETISH = re.compile(r"(^|[._-])(secret|token|password|passwd|apikey|api_key|credential|private[_-]?key)([._-]|$)", re.I)
_MAX_TEXT_BYTES = 512_000


class OpenCodeCompatError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class OpenCodeCompatService:
    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir).expanduser().resolve()
        self.runtime = RuntimeService(self.root_dir)
        self.sandbox = CreativeSandboxService(self.root_dir)

    def session_payload(self, session_id: str) -> dict[str, Any]:
        if not self.runtime.sessions.exists(session_id):
            raise OpenCodeCompatError("session_not_found", f"Session not found: {session_id}", status=404)
        return self._session_from_meta(self.runtime.sessions.load_meta(session_id))

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions: list[dict[str, Any]] = []
        for meta_path in sorted((self.root_dir / "sessions").glob("*/meta.json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                sessions.append(self._session_from_meta(meta))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(sessions, key=lambda item: item["time"]["updated"], reverse=True)

    def session_status(self) -> dict[str, Any]:
        statuses: dict[str, Any] = {}
        for session in self.list_sessions():
            statuses[session["id"]] = {
                "sessionID": session["id"],
                "status": "idle",
                "time": session["time"],
            }
        return statuses

    def messages(self, session_id: str) -> list[dict[str, Any]]:
        self.session_payload(session_id)
        runtime_events = self.runtime.sessions.read_events(session_id)
        dev_events = self.sandbox.read_events(session_id)
        events = sorted([*runtime_events, *dev_events], key=lambda event: str(event.get("timestamp") or ""))
        return _events_to_messages(session_id, events, root_dir=self.root_dir)

    def message(self, session_id: str, message_id: str) -> dict[str, Any]:
        for message in self.messages(session_id):
            if message.get("info", {}).get("id") == message_id:
                return message
        raise OpenCodeCompatError("message_not_found", f"Message not found: {message_id}", status=404)

    def event_stream(self, session_id: str | None = None) -> list[dict[str, Any]]:
        session_ids = [session_id] if session_id else [item["id"] for item in self.list_sessions()]
        out: list[dict[str, Any]] = [{"type": "server.connected", "properties": {}}]
        for sid in session_ids:
            if not sid or not self.runtime.sessions.exists(sid):
                continue
            out.append({"type": "session.updated", "properties": {"info": self.session_payload(sid)}})
            for message in self.messages(sid):
                out.append({"type": "message.updated", "properties": {"info": message["info"]}})
                for part in message.get("parts") or []:
                    out.append({"type": "message.part.updated", "properties": {"part": part}})
        return out

    def current_project(self, project_id: str | None = None) -> dict[str, Any]:
        project_id = project_id or _latest_project_id(self.list_sessions())
        return {
            "id": project_id or "lumeri-vnext",
            "directory": str(self.root_dir),
            "worktree": str(self.root_dir),
            "name": "Lumeri",
            "time": {"created": _now_ms(), "updated": _now_ms()},
        }

    def list_projects(self) -> list[dict[str, Any]]:
        return [self.current_project()]

    def file_list(self, path: str | None = None) -> list[dict[str, Any]]:
        target = self._safe_path(path or ".")
        if not target.exists():
            raise OpenCodeCompatError("file_not_found", f"File not found: {path}", status=404)
        if target.is_file():
            return [_file_node(target, root_dir=self.root_dir)]
        nodes: list[dict[str, Any]] = []
        for child in sorted(target.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
            if _skip_path(child):
                continue
            nodes.append(_file_node(child, root_dir=self.root_dir))
        return nodes

    def file_read(self, path: str) -> dict[str, Any]:
        target = self._safe_path(path)
        if not target.exists() or not target.is_file():
            raise OpenCodeCompatError("file_not_found", f"File not found: {path}", status=404)
        raw = target.read_bytes()
        if len(raw) > _MAX_TEXT_BYTES:
            return {"type": "binary", "content": ""}
        try:
            return {"type": "text", "content": raw.decode("utf-8")}
        except UnicodeDecodeError:
            return {"type": "binary", "content": ""}

    def file_status(self) -> list[dict[str, Any]]:
        try:
            proc = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(self.root_dir),
                text=True,
                capture_output=True,
                timeout=3,
            )
        except Exception:
            return []
        if proc.returncode != 0:
            return []
        out: list[dict[str, Any]] = []
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            code = line[:2]
            path = line[3:].strip()
            status = "modified"
            if "A" in code or "?" in code:
                status = "added"
            if "D" in code:
                status = "deleted"
            out.append({"path": path, "added": 0, "removed": 0, "status": status})
        return out

    def find_files(self, query: str, *, include_dirs: bool = False, kind: str | None = None, limit: int = 80) -> list[str]:
        query = str(query or "").lower()
        if not query:
            return []
        matches: list[str] = []
        for child in self.root_dir.rglob("*"):
            if _skip_path(child):
                if child.is_dir():
                    continue
                continue
            if kind == "file" and not child.is_file():
                continue
            if kind == "directory" and not child.is_dir():
                continue
            if child.is_dir() and not include_dirs and kind != "directory":
                continue
            rel = _rel(child, self.root_dir)
            if query in rel.lower():
                matches.append(rel)
                if len(matches) >= limit:
                    break
        return matches

    def find_text(self, pattern: str, *, limit: int = 80) -> list[dict[str, Any]]:
        pattern = str(pattern or "")
        if not pattern:
            return []
        out: list[dict[str, Any]] = []
        lowered = pattern.lower()
        for child in self.root_dir.rglob("*"):
            if len(out) >= limit:
                break
            if _skip_path(child) or not child.is_file() or child.stat().st_size > _MAX_TEXT_BYTES:
                continue
            try:
                text = child.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            absolute_offset = 0
            for line_no, line in enumerate(text.splitlines(), start=1):
                start = line.lower().find(lowered)
                if start >= 0:
                    out.append(
                        {
                            "path": _rel(child, self.root_dir),
                            "lines": {"text": line},
                            "line_number": line_no,
                            "absolute_offset": absolute_offset + start,
                            "submatches": [{"match": {"text": line[start : start + len(pattern)]}, "start": start, "end": start + len(pattern)}],
                        }
                    )
                    if len(out) >= limit:
                        break
                absolute_offset += len(line) + 1
        return out

    def shell_message(self, session_id: str, command: str, result: dict[str, Any]) -> dict[str, Any]:
        message_id = f"msg_shell_{_stable_suffix(command + str(result.get('command_id') or ''))}"
        part_id = f"part_shell_{_stable_suffix(command + str(result.get('status') or ''))}"
        now = _now_ms()
        status = "completed" if result.get("status") == "succeeded" else "error"
        state: dict[str, Any]
        output = "\n".join(item for item in [str(result.get("stdout_tail") or ""), str(result.get("stderr_tail") or "")] if item).strip()
        if status == "completed":
            state = {
                "status": "completed",
                "input": {"command": command},
                "output": output,
                "title": command,
                "metadata": {"exit_code": result.get("exit_code"), "command_id": result.get("command_id")},
                "time": {"start": _parse_ms(result.get("start_time")), "end": _parse_ms(result.get("end_time")) or now},
            }
        else:
            state = {
                "status": "error",
                "input": {"command": command},
                "error": output or ((result.get("error") or {}).get("message")) or str(result.get("status") or "failed"),
                "metadata": {"exit_code": result.get("exit_code"), "command_id": result.get("command_id")},
                "time": {"start": _parse_ms(result.get("start_time")), "end": _parse_ms(result.get("end_time")) or now},
            }
        return {
            "info": _assistant_info(message_id, session_id, now, root_dir=self.root_dir, parent_id=""),
            "parts": [
                {
                    "id": part_id,
                    "sessionID": session_id,
                    "messageID": message_id,
                    "type": "tool",
                    "callID": str(result.get("command_id") or part_id),
                    "tool": "bash",
                    "state": state,
                }
            ],
        }

    def _session_from_meta(self, meta: dict[str, Any]) -> dict[str, Any]:
        session_id = str(meta.get("session_id") or meta.get("id") or "")
        return {
            "id": session_id,
            "projectID": str(meta.get("project_id") or "lumeri-vnext"),
            "directory": str(self.root_dir),
            "title": str(meta.get("goal") or "Lumeri vNext"),
            "version": "lumeri-opencode-compat-v0",
            "time": {
                "created": _parse_ms(meta.get("created_at")),
                "updated": _parse_ms(meta.get("updated_at")),
            },
        }

    def _safe_path(self, path: str | None) -> Path:
        raw = str(path or ".").strip()
        if raw in {"", "/", "."}:
            candidate = self.root_dir
        else:
            source = Path(raw)
            candidate = source if source.is_absolute() else self.root_dir / source
        resolved = candidate.expanduser().resolve()
        try:
            resolved.relative_to(self.root_dir)
        except ValueError as exc:
            raise OpenCodeCompatError("file_forbidden", f"Path is outside the Lumeri workspace: {raw}", status=403) from exc
        if _SECRETISH.search(resolved.name):
            raise OpenCodeCompatError("file_forbidden", f"Refusing to expose secret-like path: {resolved.name}", status=403)
        return resolved


def prompt_payload_to_runtime(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    text = str(payload.get("message") or payload.get("prompt") or payload.get("text") or "").strip()
    if not text:
        parts = payload.get("parts") or []
        if isinstance(parts, list):
            text = "\n".join(str(part.get("text") or "") for part in parts if isinstance(part, dict) and part.get("type") == "text").strip()
    return {
        "session_id": session_id,
        "message": text,
        "ai_agent": payload.get("agent"),
        "ai_model": (payload.get("model") or {}).get("modelID") if isinstance(payload.get("model"), dict) else payload.get("ai_model"),
        "script": str(payload.get("script") or "").strip(),
    }


def opencode_error_payload(exc: Exception) -> tuple[int, dict[str, Any]]:
    if isinstance(exc, OpenCodeCompatError):
        return exc.status, {"error": {"code": exc.code, "message": str(exc)}}
    return 500, {"error": {"code": "opencode_compat_failed", "message": str(exc)}}


def sse_lines(events: list[dict[str, Any]]) -> str:
    chunks = []
    for event in events:
        event_type = str(event.get("type") or "message")
        chunks.append(f"event: {event_type}\n")
        chunks.append("data: " + json.dumps(event, ensure_ascii=False) + "\n\n")
    return "".join(chunks)


def _events_to_messages(session_id: str, events: list[dict[str, Any]], *, root_dir: Path) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    last_user_id = ""
    for index, event in enumerate(events, start=1):
        event_type = str(event.get("type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        timestamp = _parse_ms(event.get("timestamp"))
        suffix = _stable_suffix(f"{index}:{event_type}:{event.get('timestamp')}")
        if event_type == "runtime_task_started" and payload.get("message"):
            message_id = f"msg_user_{suffix}"
            last_user_id = message_id
            messages.append(
                {
                    "info": _user_info(message_id, session_id, timestamp),
                    "parts": [_text_part(f"part_{suffix}", message_id, session_id, str(payload.get("message") or ""), timestamp)],
                }
            )
            continue
        text = _text_for_event(event_type, payload)
        tool = _tool_for_event(event_type, payload, timestamp)
        if not text and not tool:
            continue
        message_id = f"msg_assistant_{suffix}"
        parts: list[dict[str, Any]] = []
        if text:
            parts.append(_text_part(f"part_text_{suffix}", message_id, session_id, text, timestamp))
        if tool:
            tool["id"] = f"part_tool_{suffix}"
            tool["sessionID"] = session_id
            tool["messageID"] = message_id
            parts.append(tool)
        messages.append(
            {
                "info": _assistant_info(message_id, session_id, timestamp, root_dir=root_dir, parent_id=last_user_id),
                "parts": parts,
            }
        )
    return messages


def _text_for_event(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == "agent_message":
        return str(payload.get("text") or "")
    if event_type == "review_note":
        return str(payload.get("note") or payload.get("feedback") or "")
    if event_type == "preview_ready":
        return "预览已经好了。"
    if event_type == "succeeded":
        return "完成了。"
    if event_type == "failed":
        return str(payload.get("user_message") or payload.get("error_code") or "执行失败。")
    return ""


def _tool_for_event(event_type: str, payload: dict[str, Any], timestamp: int) -> dict[str, Any] | None:
    titles = {
        "runtime_task_started": ("task", "接收任务"),
        "script_generated": ("edit", "写入 runtime/script.py"),
        "sandbox_started": ("sandbox", "运行沙盒"),
        "patch_applied": ("edit", "应用 timeline.patch"),
        "render_started": ("render", "渲染预览"),
        "dev_workspace_ready": ("workspace", "准备工作区"),
        "dev_file_written": ("edit", "写入文件"),
        "dev_command_started": ("bash", payload.get("command") or "运行命令"),
        "dev_command_finished": ("bash", payload.get("command") or "命令结束"),
        "dev_artifact_ready": ("artifact", "生成产物"),
    }
    if event_type not in titles:
        return None
    tool_name, title = titles[event_type]
    status = "running" if event_type in {"runtime_task_started", "sandbox_started", "render_started", "dev_command_started"} else "completed"
    if event_type == "dev_command_finished" and str(payload.get("status") or "") not in {"succeeded", "completed"}:
        status = "error"
    call_id = str(payload.get("command_id") or payload.get("task_id") or payload.get("script_hash") or f"call_{timestamp}")
    if status == "error":
        state = {
            "status": "error",
            "input": dict(payload),
            "error": str(payload.get("stderr_tail") or payload.get("user_message") or payload.get("status") or "failed"),
            "metadata": dict(payload),
            "time": {"start": timestamp, "end": timestamp},
        }
    elif status == "running":
        state = {
            "status": "running",
            "input": dict(payload),
            "title": str(title),
            "metadata": dict(payload),
            "time": {"start": timestamp},
        }
    else:
        output = "\n".join(str(payload.get(key) or "") for key in ("stdout_tail", "stderr_tail", "path", "preview_url") if payload.get(key)).strip()
        state = {
            "status": "completed",
            "input": dict(payload),
            "output": output,
            "title": str(title),
            "metadata": dict(payload),
            "time": {"start": timestamp, "end": timestamp},
        }
    return {"type": "tool", "callID": call_id, "tool": tool_name, "state": state}


def _user_info(message_id: str, session_id: str, timestamp: int) -> dict[str, Any]:
    return {
        "id": message_id,
        "sessionID": session_id,
        "role": "user",
        "time": {"created": timestamp},
        "agent": "lumeri-video",
        "model": {"providerID": "lumeri", "modelID": "runtime-kernel"},
    }


def _assistant_info(message_id: str, session_id: str, timestamp: int, *, root_dir: Path, parent_id: str) -> dict[str, Any]:
    return {
        "id": message_id,
        "sessionID": session_id,
        "role": "assistant",
        "time": {"created": timestamp, "completed": timestamp},
        "parentID": parent_id,
        "modelID": "runtime-kernel",
        "providerID": "lumeri",
        "mode": "build",
        "path": {"cwd": str(root_dir), "root": str(root_dir)},
        "cost": 0,
        "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
        "finish": "stop",
    }


def _text_part(part_id: str, message_id: str, session_id: str, text: str, timestamp: int) -> dict[str, Any]:
    return {
        "id": part_id,
        "sessionID": session_id,
        "messageID": message_id,
        "type": "text",
        "text": text,
        "time": {"start": timestamp, "end": timestamp},
    }


def _file_node(path: Path, *, root_dir: Path) -> dict[str, Any]:
    return {
        "name": path.name,
        "path": _rel(path, root_dir),
        "absolute": str(path),
        "type": "directory" if path.is_dir() else "file",
        "ignored": False,
    }


def _latest_project_id(sessions: list[dict[str, Any]]) -> str:
    if sessions:
        return str(sessions[0].get("projectID") or "")
    return ""


def _skip_path(path: Path) -> bool:
    return any(part in _SKIP_NAMES or _SECRETISH.search(part) for part in path.parts)


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix() or "."
    except ValueError:
        return path.name


def _parse_ms(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    if not text:
        return _now_ms()
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return _now_ms()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _stable_suffix(value: str) -> str:
    import hashlib

    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
