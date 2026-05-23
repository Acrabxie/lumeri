"""Gated vNext runtime API helpers for Lumeri Desktop.

This module is intentionally separate from the legacy Plan-v2 HTTP path. It
adapts the experimental Runtime Kernel into a small desktop/mobile API surface:
session, message, events, project, approval, and feedback.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .orchestrator import GemiaOrchestrator
from .project_inspect import inspect_project
from .project_model import empty_project, normalize_project
from .project_render import ProjectRenderError, render_project_preview
from .project_store import ProjectStoreError
from .session_store import SessionStore, SessionStoreError


class RuntimeApiError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: int = 400, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.detail = detail


class RuntimeService:
    """Small facade used by the hidden `/next` API routes."""

    def __init__(self, root_dir: str | Path, *, ai_client: Any | None = None) -> None:
        self.root_dir = Path(root_dir).expanduser().resolve()
        self.orchestrator = GemiaOrchestrator(root_dir=self.root_dir)
        self.sessions = SessionStore(self.root_dir / "sessions")
        self.ai_client = ai_client

    def create_session(self, payload: dict[str, Any] | None = None, *, account_id: str | None = None) -> dict[str, Any]:
        payload = payload or {}
        project_id = _safe_id(str(payload.get("project_id") or ""), "project")
        if not project_id:
            project_id = f"vnext_{uuid.uuid4().hex[:10]}"
        session_id = _safe_id(str(payload.get("session_id") or ""), "session")
        if not session_id:
            session_id = f"session_{uuid.uuid4().hex[:10]}"
        goal = str(payload.get("goal") or "Lumeri vNext 会话").strip() or "Lumeri vNext 会话"

        created_project = False
        if not self.orchestrator.project_store.exists(project_id):
            seed = self._seed_project(payload, account_id=account_id)
            self.orchestrator.project_store.create(project_id, seed=seed)
            created_project = True

        if not self.sessions.exists(session_id):
            self.sessions.create(
                session_id,
                project_id=project_id,
                goal=goal,
                max_turns=int(payload.get("max_turns") or 3),
                ai_model=str(payload.get("ai_model") or "runtime-vnext"),
            )
        else:
            self.sessions.update_meta(session_id, {"project_id": project_id, "goal": goal})

        self._emit(
            session_id,
            "agent_message",
            {
                "text": "vNext 运行时会话已就绪。",
                "project_id": project_id,
                "created_project": created_project,
            },
        )
        project = self.orchestrator.project_store.load(project_id)
        return {
            "status": "succeeded",
            "session_id": session_id,
            "project_id": project_id,
            "created_project": created_project,
            "project": project,
            "events": self.sessions.read_events(session_id),
        }

    def post_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = str(payload.get("session_id") or payload.get("sessionId") or "").strip()
        if not session_id:
            raise RuntimeApiError("missing_session_id", "缺少 session_id")
        if not self.sessions.exists(session_id):
            raise RuntimeApiError("session_not_found", f"找不到会话：{session_id}", status=404)
        meta = self.sessions.load_meta(session_id)
        project_id = str(payload.get("project_id") or meta.get("project_id") or "")
        if not project_id or not self.orchestrator.project_store.exists(project_id):
            raise RuntimeApiError("project_not_found", f"找不到项目：{project_id}", status=404)

        message = str(payload.get("message") or payload.get("prompt") or "").strip()
        if not message:
            raise RuntimeApiError("empty_message", "请输入要执行的内容")
        if _is_conversational_message(message):
            self._emit(session_id, "agent_message", {"text": _conversation_reply(message), "message": message})
            return {
                "status": "succeeded",
                "session_id": session_id,
                "project_id": project_id,
                "project": self.orchestrator.project_store.load(project_id),
                "events": self.sessions.read_events(session_id),
            }

        self._emit(session_id, "runtime_notice", {"text": "开始生成并校验脚本。", "message": message})
        project_before = self.orchestrator.project_store.load(project_id)
        if _is_followup_execute_message(message):
            fallback_script = _fallback_prompt_only_script(message, project_before, allow_generic=True)
            if fallback_script:
                provided_script = fallback_script
                payload = {**payload, "script": fallback_script, "render_label": payload.get("render_label") or "runtime"}
            else:
                provided_script = str(payload.get("script") or "").strip()
        else:
            provided_script = str(payload.get("script") or "").strip()
        max_attempts = 1 if provided_script else 2
        previous_error: dict[str, Any] | None = None
        task: dict[str, Any] | None = None
        script = provided_script
        script_hash = ""
        for attempt in range(1, max_attempts + 1):
            if not provided_script:
                try:
                    script = self._generate_script(
                        message,
                        project_state=project_before,
                        previous_error=previous_error,
                        agent=str(payload.get("ai_agent") or payload.get("agent") or "") or None,
                    )
                except Exception as exc:
                    previous_error = {
                        "stage": "generate_script",
                        "message": _user_error_message(exc),
                    }
                    if attempt < max_attempts:
                        self._emit(session_id, "runtime_notice", {"text": "脚本校验失败，准备重试。", "attempt": attempt})
                        continue
                    fallback_script = _fallback_prompt_only_script(message, project_before, allow_generic=True)
                    if fallback_script:
                        self._emit(
                            session_id,
                            "runtime_notice",
                            {
                                "text": "切换到本地安全小样脚本。",
                                "previous_error": previous_error["message"],
                            },
                        )
                        script = fallback_script
                    else:
                        self._emit(
                            session_id,
                            "failed",
                            {
                                "error_code": "script_generation_failed",
                                "user_message": previous_error["message"],
                            },
                        )
                        return {
                            "status": "failed",
                            "error": {"code": "script_generation_failed", "message": previous_error["message"]},
                            "session_id": session_id,
                            "project_id": project_id,
                            "events": self.sessions.read_events(session_id),
                        }

            script_hash = hashlib.sha256(script.encode("utf-8")).hexdigest()[:12]
            self._emit(
                session_id,
                "script_generated",
                {"script_hash": script_hash, "script": script, "attempt": attempt},
            )
            self._emit(session_id, "sandbox_started", {"script_hash": script_hash, "attempt": attempt, "dry_run": True})

            with _script_mode_enabled():
                dry_run = self.orchestrator.plan_from_script(
                    script,
                    project_id=project_id,
                    session_id=session_id,
                    ai_model=str(payload.get("ai_model") or "runtime-vnext"),
                    timeout_sec=int(payload.get("timeout_sec") or 120),
                    dry_run=True,
                )
            if dry_run.get("status") != "succeeded":
                previous_error = {
                    "stage": "sandbox_dry_run",
                    "message": _user_error_text(dry_run.get("error") or dry_run.get("stderr") or "脚本没有通过沙盒预检查"),
                }
                if attempt < max_attempts and not provided_script:
                    self._emit(session_id, "runtime_notice", {"text": "沙盒预检查失败，准备重试。", "attempt": attempt})
                    continue
                self._emit(
                    session_id,
                    "failed",
                    {
                        "error_code": "sandbox_dry_run_failed",
                        "user_message": previous_error["message"],
                    },
                )
                return {
                    "status": "failed",
                    "error": {"code": "sandbox_dry_run_failed", "message": previous_error["message"]},
                    "session_id": session_id,
                    "project_id": project_id,
                    "events": self.sessions.read_events(session_id),
                }

            self._emit(session_id, "sandbox_started", {"script_hash": script_hash, "attempt": attempt, "dry_run": False})

            with _script_mode_enabled():
                task = self.orchestrator.plan_from_script(
                    script,
                    project_id=project_id,
                    session_id=session_id,
                    ai_model=str(payload.get("ai_model") or "runtime-vnext"),
                    timeout_sec=int(payload.get("timeout_sec") or 120),
                )
            if task.get("status") == "succeeded":
                break
            previous_error = {
                "stage": "sandbox_execute",
                "message": _user_error_text(task.get("error") or "沙盒没有产出可用的时间线补丁。"),
                "error_code": task.get("error_code") or "",
            }
            if attempt < max_attempts and not provided_script:
                self._emit(session_id, "runtime_notice", {"text": "脚本没有产出可用补丁，准备重试。", "attempt": attempt})
                continue

        if not isinstance(task, dict) or task.get("status") != "succeeded":
            error_text = _user_error_text((previous_error or {}).get("message") or "沙盒没有产出可用的时间线补丁。")
            error_code = (task or {}).get("error_code") or (previous_error or {}).get("error_code") or "sandbox_failed"
            self._emit(
                session_id,
                "failed",
                {
                    "error_code": error_code,
                    "user_message": error_text,
                    "task_id": (task or {}).get("task_id"),
                },
            )
            return {
                "status": "failed",
                "error": {"code": error_code, "message": error_text},
                "session_id": session_id,
                "project_id": project_id,
                "task": _public_task_summary(task),
                "events": self.sessions.read_events(session_id),
            }

        self._emit(
            session_id,
            "patch_applied",
            {
                "task_id": task.get("task_id"),
                "patch_seq_start": task.get("patch_seq_start"),
                "patch_seq_end": task.get("patch_seq_end"),
                "patch_count": len(task.get("timeline_patches") or []),
            },
        )
        self._emit(session_id, "render_started", {"patch_seq": task.get("patch_seq_end")})
        try:
            manifest = render_project_preview(
                self.orchestrator.project_store,
                project_id,
                output_root=self.orchestrator.outputs_dir,
                max_long_edge=int(payload.get("max_long_edge") or 640),
                label=_safe_render_label(str(payload.get("render_label") or "runtime")),
            )
        except ProjectRenderError as exc:
            self._emit(
                session_id,
                "failed",
                {
                    "error_code": exc.code,
                    "user_message": str(exc),
                    "technical_detail": exc.detail,
                },
            )
            return {
                "status": "failed",
                "error": {"code": exc.code, "message": str(exc)},
                "session_id": session_id,
                "project_id": project_id,
                "task": _public_task_summary(task),
                "events": self.sessions.read_events(session_id),
            }

        preview_url = _file_url_for(self.root_dir, manifest["preview_path"])
        self._emit(
            session_id,
            "preview_ready",
            {
                "render_id": manifest.get("render_id"),
                "preview_path": manifest.get("preview_path"),
                "preview_url": preview_url,
                "manifest_path": manifest.get("manifest_path"),
                "duration": manifest.get("duration"),
                "resolution": manifest.get("resolution"),
            },
        )
        self._emit(
            session_id,
            "review_note",
            {
                "render_id": manifest.get("render_id"),
                "note": "预览已渲染，并且媒体文件可读取。v0 先只检查媒体有效性。",
            },
        )
        self._emit(
            session_id,
            "succeeded",
            {
                "task_id": task.get("task_id"),
                "render_id": manifest.get("render_id"),
                "preview_url": preview_url,
            },
        )
        return {
            "status": "succeeded",
            "session_id": session_id,
            "project_id": project_id,
            "task": task,
            "render": {**manifest, "preview_url": preview_url},
            "project": self.orchestrator.project_store.load(project_id),
            "events": self.sessions.read_events(session_id),
        }

    def events(self, session_id: str) -> dict[str, Any]:
        if not self.sessions.exists(session_id):
            raise RuntimeApiError("session_not_found", f"找不到会话：{session_id}", status=404)
        return {"status": "succeeded", "session_id": session_id, "events": self.sessions.read_events(session_id)}

    def project(self, project_id: str) -> dict[str, Any]:
        if not self.orchestrator.project_store.exists(project_id):
            raise RuntimeApiError("project_not_found", f"找不到项目：{project_id}", status=404)
        renders: list[dict[str, Any]] = []
        render_dir = self.orchestrator.project_store.renders_dir(project_id)
        for path in sorted(render_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
            try:
                render = json.loads(path.read_text(encoding="utf-8"))
                render["manifest_path"] = str(path)
                render["preview_url"] = _file_url_for(self.root_dir, str(render.get("preview_path") or ""))
                renders.append(render)
            except (OSError, json.JSONDecodeError):
                continue
        return {
            "status": "succeeded",
            "project_id": project_id,
            "project": self.orchestrator.project_store.load(project_id),
            "summary": inspect_project(self.orchestrator.project_store, project_id, history=5),
            "renders": renders,
        }

    def approval(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            raise RuntimeApiError("missing_session_id", "缺少 session_id")
        decision = "approved" if bool(payload.get("approved", True)) else "rejected"
        event = self._emit(session_id, "needs_approval", {"decision": decision, "reason": str(payload.get("reason") or "")})
        return {"status": "succeeded", "event": event}

    def feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            raise RuntimeApiError("missing_session_id", "缺少 session_id")
        if not self.sessions.exists(session_id):
            raise RuntimeApiError("session_not_found", f"找不到会话：{session_id}", status=404)
        meta = self.sessions.load_meta(session_id)
        project_id = str(payload.get("project_id") or meta.get("project_id") or "")
        if not project_id or not self.orchestrator.project_store.exists(project_id):
            raise RuntimeApiError("project_not_found", f"找不到项目：{project_id}", status=404)
        text = str(payload.get("feedback") or payload.get("text") or "").strip()
        if not text:
            raise RuntimeApiError("empty_feedback", "请输入反馈内容")
        render_id = str(payload.get("render_id") or payload.get("renderPassId") or "").strip()
        time_range = payload.get("time_range") or payload.get("timeRange")
        note = self._emit(
            session_id,
            "review_note",
            {
                "feedback": text,
                "render_id": render_id,
                "time_range": time_range,
            },
        )
        self._emit(
            session_id,
            "agent_message",
            {
                "text": "我会把这条反馈变成一次局部修改脚本，先校验，再渲染新的预览。",
                "render_id": render_id,
            },
        )
        revision_result = self.post_message(
            {
                "session_id": session_id,
                "project_id": project_id,
                "message": _build_feedback_revision_request(
                    text,
                    render_id=render_id,
                    time_range=time_range,
                ),
                "script": str(payload.get("script") or "").strip(),
                "ai_agent": payload.get("ai_agent") or payload.get("agent"),
                "ai_model": payload.get("ai_model"),
                "timeout_sec": payload.get("timeout_sec"),
                "max_long_edge": payload.get("max_long_edge"),
                "render_label": "revision",
            }
        )
        revision_result["feedback_event"] = note
        revision_result["revision"] = True
        return revision_result

    def _seed_project(self, payload: dict[str, Any], *, account_id: str | None) -> dict[str, Any]:
        raw_project = payload.get("project") if isinstance(payload.get("project"), dict) else None
        raw_seed = payload.get("seed_project") if isinstance(payload.get("seed_project"), dict) else None
        if raw_project or raw_seed:
            return normalize_project(raw_project or raw_seed, account_id=account_id)
        video_path = str(payload.get("video_path") or payload.get("video") or "").strip()
        if video_path:
            return _seed_project_from_video(video_path, account_id=account_id)
        return empty_project(account_id=account_id, title="Lumeri vNext 项目")

    def _emit(self, session_id: str, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.sessions.append_event(session_id, event_type, payload or {})

    def _generate_script(
        self,
        request: str,
        *,
        project_state: dict[str, Any],
        previous_error: dict[str, Any] | None,
        agent: str | None,
    ) -> str:
        ai_client = self.ai_client
        if ai_client is None:
            from .ai.ai_client import AIClient

            ai_client = AIClient()
        return asyncio.run(
            ai_client.generate_script(
                request,
                project_state=project_state,
                previous_error=previous_error,
                agent=agent,
            )
        )


def runtime_error_payload(exc: Exception) -> tuple[int, dict[str, Any]]:
    if isinstance(exc, RuntimeApiError):
        payload = {
            "status": "failed",
            "error": {"code": exc.code, "message": str(exc)},
        }
        if exc.detail:
            payload["error"]["detail"] = exc.detail
        return exc.status, payload
    if isinstance(exc, (ProjectStoreError, SessionStoreError)):
        return 400, {"status": "failed", "error": {"code": "runtime_store_error", "message": str(exc)}}
    return 500, {"status": "failed", "error": {"code": "runtime_failed", "message": _user_error_message(exc)}}


def _user_error_message(exc: Exception) -> str:
    return _user_error_text(str(exc) or repr(exc))


def _user_error_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "执行失败，但没有返回具体原因。"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        text = lines[-1]
    replacements = {
        "Generated Lumeri script does not emit a timeline patch or an honest ValueError": "Gemini 生成的 Lumeri 脚本没有创建时间线补丁，也没有诚实说明缺少能力。",
        "Script executed but emitted no TimelinePatch operations.": "脚本执行了，但没有创建时间线补丁。",
    }
    if text in replacements:
        return replacements[text]
    if text.startswith("ValueError:"):
        detail = text.split(":", 1)[1].strip()
        return detail or "脚本主动停止了执行。"
    if text.startswith("SandboxViolation:"):
        text = text.split(":", 1)[1].strip()
    if text.startswith("Syntax error:"):
        return "这次脚本没有通过校验，我没有执行它。"
    lowered = text.lower()
    if (
        "unterminated string literal" in lowered
        or "unterminated triple-quoted string literal" in lowered
        or "invalid syntax" in lowered
        or "detected at line" in lowered
    ):
        return "这次脚本没有通过校验，我没有执行它。"
    if text.startswith("Blocked import at line "):
        return "沙盒阻止了不安全的 import：" + text.removeprefix("Blocked import at line ").strip()
    if text.startswith("Blocked call at line "):
        return "沙盒阻止了不安全的调用：" + text.removeprefix("Blocked call at line ").strip()
    if text.startswith("Blocked attribute at line "):
        return "沙盒阻止了不安全的属性访问：" + text.removeprefix("Blocked attribute at line ").strip()
    if text.startswith("Blocked name at line "):
        return "沙盒阻止了不安全的名称访问：" + text.removeprefix("Blocked name at line ").strip()
    if text.startswith("Script timed out after "):
        return "脚本执行超时：" + text.removeprefix("Script timed out after ").strip()
    if text.startswith("Script exited with "):
        return "脚本执行失败，退出码：" + text.removeprefix("Script exited with ").strip()
    return text


def _is_conversational_message(message: str) -> bool:
    raw = str(message or "").strip()
    compact = "".join(raw.lower().split())
    if not compact:
        return False
    greetings = {
        "在吗",
        "在么",
        "你在吗",
        "还在吗",
        "hello",
        "hi",
        "hey",
        "你好",
        "您好",
    }
    if compact in greetings or compact.rstrip("？?！!") in greetings:
        return True
    discussion_markers = [
        "你是谁",
        "你是什么",
        "你能做什么",
        "你会做什么",
        "你有什么用",
        "介绍一下",
        "自我介绍",
        "帮助",
        "怎么用",
        "如何使用",
        "你觉得",
        "你认为",
        "怎么看",
        "怎么说",
        "为什么",
        "怎么办",
        "能不能",
        "可以吗",
        "要不要",
        "建议",
        "想知道",
        "如何规划",
        "下一步",
    ]
    execution_markers = [
        "直接执行",
        "开始执行",
        "开始做",
        "帮我做一个",
        "生成一个",
        "剪一个",
        "渲染一个",
        "导出",
        "创建一个视频",
    ]
    if any(marker in compact for marker in execution_markers):
        return False
    return any(marker in compact for marker in discussion_markers) or compact.endswith(("?", "？"))


def _conversation_reply(message: str) -> str:
    compact = "".join(str(message or "").strip().lower().split())
    if any(marker in compact for marker in ["你是谁", "你是什么", "自我介绍", "介绍一下"]):
        return (
            "我是 Lumeri 的视频创作运行时助手。当前我能先读会话和项目状态，回答规划问题，"
            "在你明确要求制作或修改视频时再生成脚本、进沙盒、应用时间线补丁并渲染预览。"
        )
    if any(marker in compact for marker in ["你能做什么", "你会做什么", "你有什么用", "帮助", "怎么用", "如何使用"]):
        return (
            "你可以直接描述视频目标、素材选择、剪辑方式或反馈当前预览。普通问题我会先对话；"
            "明确说“开始做”“生成一个”“剪一个”时，我才会进入脚本、工具和预览流程。"
        )
    if any(marker in compact for marker in ["你觉得", "你认为", "建议", "做什么", "第一个视频"]):
        return (
            "我建议第一个视频做一个很短的 Lumeri 宣言小样：3 秒，黑底干净画面，一个发光小球或光标从左到右掠过，"
            "带出“从一句话到可预览视频”的感觉。它足够简单，能验证空画布、图层、时间轴、预览和反馈闭环，也不会一上来被复杂素材拖垮。"
        )
    if any(marker in compact for marker in ["为什么", "怎么办", "怎么说", "下一步", "如何规划"]):
        return "我会先用中文把判断说清楚；如果你明确说“开始做”或“生成一个”，我再进入脚本、沙盒和预览流程。"
    return "在，我在。你可以直接说想做的视频效果；如果只是聊天，我会先用自然语言回复，不会启动沙盒。"


def _is_followup_execute_message(message: str) -> bool:
    compact = "".join(str(message or "").strip().lower().split()).rstrip("。.!！?")
    compact = compact.replace("，", "").replace(",", "")
    return compact in {"行来吧", "来吧", "开始吧", "做吧", "可以", "好", "好的", "ok", "okay", "go"}


def _fallback_prompt_only_script(message: str, project_state: dict[str, Any] | None, *, allow_generic: bool = False) -> str:
    clips = ((project_state or {}).get("timeline") or {}).get("clips") or []
    if clips:
        return ""
    compact = str(message or "").lower()
    if not allow_generic and not any(token in compact for token in ["mg", "m g", "动画", "小样", "motion", "blank", "空白", "小球", "球"]):
        return ""
    if any(token in compact for token in ["小球", "球", "ball"]):
        html = '<section class="stage"><div class="floor"></div><div class="ball"></div></section>'
        css = (
            ".stage{width:100%;height:100%;position:relative;overflow:hidden;background:#0b0f14}"
            ".floor{position:absolute;left:9%;right:9%;bottom:24%;height:5px;background:linear-gradient(90deg,transparent,#d8fff5,transparent);box-shadow:0 0 18px rgba(135,228,207,.45)}"
            ".ball{position:absolute;width:52px;height:52px;border-radius:50%;left:8%;bottom:27%;background:radial-gradient(circle at 35% 30%,#ffffff,#87e4cf 38%,#246d64 78%);box-shadow:0 18px 34px rgba(0,0,0,.34),0 0 24px rgba(135,228,207,.42);animation:bounce 3s cubic-bezier(.2,.8,.2,1) both}"
            "@keyframes bounce{0%{transform:translate(0,0) scale(1)}18%{transform:translate(14vw,-30vh) scale(1.03)}34%{transform:translate(28vw,0) scale(.98)}52%{transform:translate(45vw,-22vh) scale(1.02)}68%{transform:translate(62vw,0) scale(.99)}84%{transform:translate(76vw,-13vh) scale(1.01)}100%{transform:translate(86vw,0) scale(1)}}"
        )
        name = "ball-floor-mg"
    else:
        html = '<section class="stage"><div class="cursor"></div><div class="glow"></div><div class="line"></div></section>'
        css = (
            ".stage{width:100%;height:100%;position:relative;overflow:hidden;background:#0b0f14}"
            ".line{position:absolute;left:12%;right:12%;top:54%;height:3px;background:linear-gradient(90deg,transparent,#d8fff5,transparent);opacity:.72}"
            ".cursor{position:absolute;left:10%;top:calc(54% - 18px);width:36px;height:36px;border-radius:50%;background:radial-gradient(circle at 35% 30%,#fff,#87e4cf 42%,#286d65 80%);box-shadow:0 0 32px rgba(135,228,207,.62);animation:sweep 3s cubic-bezier(.2,.8,.2,1) both}"
            ".glow{position:absolute;left:12%;right:12%;top:48%;height:90px;background:radial-gradient(ellipse at center,rgba(135,228,207,.22),transparent 62%);filter:blur(10px);opacity:0;animation:pulse 3s ease both}"
            "@keyframes sweep{0%{transform:translateX(0) scale(.8);opacity:.2}18%{opacity:1}72%{transform:translateX(68vw) scale(1.05)}100%{transform:translateX(76vw) scale(.92);opacity:.9}}"
            "@keyframes pulse{20%{opacity:.28}60%{opacity:.6}100%{opacity:.2}}"
        )
        name = "lumeri-declaration"
    return (
        "import lumerai as lm\n\n"
        f"clip = lm.hyperframes_render({json.dumps(html, ensure_ascii=False)}, css={json.dumps(css, ensure_ascii=False)}, duration=3.0, width=1280, height=720, fps=30, name={json.dumps(name)})\n"
        "lm.timeline_insert(clip, at=0.0)\n"
    )


def _public_task_summary(task: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(task, dict):
        return None
    summary: dict[str, Any] = {}
    for key in [
        "task_id",
        "status",
        "engine",
        "project_id",
        "script_hash",
        "error_code",
        "error",
        "patch_seq_start",
        "patch_seq_end",
    ]:
        if key in task:
            summary[key] = task[key]
    if isinstance(task.get("timeline_patches"), list):
        summary["patch_count"] = len(task["timeline_patches"])
    return summary


def _seed_project_from_video(video_path: str, *, account_id: str | None) -> dict[str, Any]:
    path = Path(video_path).expanduser().resolve()
    if not path.exists():
        raise RuntimeApiError("video_not_found", f"视频路径不存在：{path}", status=404)
    project = empty_project(account_id=account_id, title=path.name)
    duration = 1.0
    try:
        from .project_render import ffprobe_media

        probe = ffprobe_media(path)
        fmt = probe.get("format") if isinstance(probe.get("format"), dict) else {}
        duration = max(float(fmt.get("duration") or 1.0), 0.1)
    except Exception:
        pass
    asset = {
        "id": "asset_seed_video",
        "asset_id": "asset_seed_video",
        "name": path.name,
        "media_kind": "video",
        "mime_type": "video/mp4",
        "source_path": str(path),
        "duration": duration,
        "metadata": {"duration": duration},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    clip = {
        "id": "clip_seed_video",
        "asset_id": "asset_seed_video",
        "track_id": "V1",
        "name": path.name,
        "media_kind": "video",
        "start": 0.0,
        "duration": duration,
        "source_in": 0.0,
        "source_out": duration,
        "enabled": True,
        "effects": {"rotation": 0, "mirrored": False, "muted": False, "audioDetached": False, "speed": 1},
    }
    project["assets"] = [asset]
    project["timeline"]["clips"] = [clip]
    project["timeline"]["duration"] = duration
    return project


def _file_url_for(root_dir: Path, path_value: str) -> str:
    if not path_value:
        return ""
    try:
        rel = Path(path_value).expanduser().resolve().relative_to(root_dir)
    except Exception:
        return ""
    return "/file/" + "/".join(rel.parts)


def _safe_render_label(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or "runtime"))
    slug = "-".join(part for part in slug.split("-") if part)
    return slug[:32] or "runtime"


def _build_feedback_revision_request(
    feedback: str,
    *,
    render_id: str,
    time_range: Any,
) -> str:
    lines = [
        "根据用户反馈修改当前 Lumeri 项目。",
        "请你自己根据可用 runtime API、项目状态、预览上下文和安全边界选择实现方式。",
        "修改应尽量局部、可审计，并与反馈成比例；除非必要，不要改动无关内容。",
        "只返回符合当前 Lumeri runtime 契约的 Python 代码。",
        "任何用户可见文字、错误说明和 ValueError 内容都必须使用简体中文。",
        f"用户反馈：{feedback}",
    ]
    if render_id:
        lines.append(f"目标 render_id：{render_id}")
    if time_range:
        lines.append(f"目标时间范围：{time_range}")
    return "\n".join(lines)


def _safe_id(value: str, prefix: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    allowed = "".join(ch for ch in value if ch.isalnum() or ch in {"_", "-"})
    if not allowed:
        return ""
    if not allowed[0].isalnum():
        allowed = f"{prefix}_{allowed.strip('_-')}"
    return allowed[:64]


@contextmanager
def _script_mode_enabled():
    previous = os.environ.get("LUMERAI_SCRIPT_MODE")
    os.environ["LUMERAI_SCRIPT_MODE"] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("LUMERAI_SCRIPT_MODE", None)
        else:
            os.environ["LUMERAI_SCRIPT_MODE"] = previous
