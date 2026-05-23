"""Experimental agent loop for the Lumeri runtime kernel.

Closes the loop the planner system can't: AI writes a ``lumerai`` script,
the sandbox runs it, the host applies the patch, the loop inspects the
result, and on the next turn the AI sees both the new state and the
previous error (if any). The loop stops on ``# DONE`` (in the generated
script), no-progress detection, max turns, permission denial, or
unrecoverable error.

This is deliberately small. It is **not** a streaming SSE backend or a
full agent runtime; those come later. The event bus, session store, and
permission policy are all real components so they can be reused.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Protocol

from .events import EventBus, JsonlEventSink
from .permissions import ALLOW, PermissionError, PermissionSet
from .project_inspect import inspect_project
from .session_store import SessionStore


_DONE_MARKER = "# DONE"


class ScriptGenerator(Protocol):
    async def generate_script(
        self,
        request: str,
        *,
        project_state: dict[str, Any] | None = None,
        agent: str | None = None,
        previous_error: dict[str, Any] | None = None,
    ) -> str:
        ...


def run_agent_loop(
    orchestrator: Any,
    ai_client: ScriptGenerator,
    *,
    project_id: str,
    goal: str,
    session_id: str,
    max_turns: int = 3,
    ai_model: str = "agent-loop",
    permissions: PermissionSet | None = None,
    bus: EventBus | None = None,
    session_store: SessionStore | None = None,
) -> dict[str, Any]:
    """Run the agent loop end-to-end and return a summary dict.

    The orchestrator must be a ``GemiaOrchestrator`` (we use
    ``plan_from_script`` and ``project_store``). The AI client must look
    like ``AIClient`` (it just needs ``generate_script``).
    """
    if os.environ.get("LUMERAI_SCRIPT_MODE", "0") != "1":
        raise RuntimeError("LUMERAI_SCRIPT_MODE=1 is required for run_agent_loop")
    if max_turns <= 0:
        raise ValueError("max_turns must be positive")

    perms = permissions or PermissionSet()
    bus = bus or EventBus()
    sessions = session_store or SessionStore(orchestrator.root_dir / "sessions")

    sessions.create(
        session_id,
        project_id=project_id,
        goal=goal,
        max_turns=max_turns,
        ai_model=ai_model,
    )
    bus.subscribe(JsonlEventSink(sessions.events_path(session_id)))

    bus.emit(
        "agent.started",
        {
            "session_id": session_id,
            "project_id": project_id,
            "goal": goal,
            "max_turns": max_turns,
        },
    )

    previous_error: dict[str, Any] | None = None
    previous_script_hash: str | None = None
    completed_turns = 0
    status = "max_turns_reached"

    for turn_seq in range(1, max_turns + 1):
        try:
            perms.require("agent:run_turn")
        except PermissionError as exc:
            status = "permission_denied"
            bus.emit("turn.denied", {"seq": turn_seq, "reason": str(exc)})
            break

        bus.emit("turn.started", {"seq": turn_seq})
        turn_record: dict[str, Any] = {
            "seq": turn_seq,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "running",
        }

        # 1. Observe.
        project_state_before = orchestrator.project_store.load(project_id)
        summary_before = inspect_project(orchestrator.project_store, project_id)
        turn_record["project_summary_before"] = summary_before
        bus.emit(
            "inspect.completed",
            {
                "seq": turn_seq,
                "patch_seq": summary_before["patch_seq"],
                "clip_count": summary_before["timeline"]["clip_count"],
            },
        )

        # 2. Generate script.
        try:
            perms.require("script:generate")
        except PermissionError as exc:
            turn_record["status"] = "denied_generate"
            turn_record["error"] = str(exc)
            sessions.write_turn(session_id, turn_record)
            status = "permission_denied"
            bus.emit("turn.completed", {"seq": turn_seq, "status": "denied_generate"})
            break

        bus.emit("script.generating", {"seq": turn_seq})
        try:
            script_text = asyncio.run(
                ai_client.generate_script(
                    goal,
                    project_state=project_state_before,
                    previous_error=previous_error,
                )
            )
        except Exception as exc:
            turn_record["status"] = "llm_invalid_script"
            turn_record["error"] = f"{type(exc).__name__}: {exc}"
            bus.emit(
                "script.failed",
                {"seq": turn_seq, "error": turn_record["error"]},
            )
            previous_error = {"stage": "generate_script", "message": str(exc)}
            sessions.write_turn(session_id, turn_record)
            completed_turns = turn_seq
            bus.emit("turn.completed", {"seq": turn_seq, "status": "llm_invalid_script"})
            continue

        script_hash = _quick_hash(script_text)
        turn_record["generated_script"] = script_text
        turn_record["script_hash"] = script_hash
        bus.emit(
            "script.generated",
            {"seq": turn_seq, "script_hash": script_hash, "chars": len(script_text)},
        )

        if (
            previous_script_hash is not None
            and previous_script_hash == script_hash
            and previous_error is None
        ):
            turn_record["status"] = "no_progress"
            sessions.write_turn(session_id, turn_record)
            status = "no_progress"
            bus.emit("turn.completed", {"seq": turn_seq, "status": "no_progress"})
            completed_turns = turn_seq
            break

        # 3. Execute through sandbox + apply patches.
        try:
            perms.require("script:execute")
            perms.require("patch:apply")
        except PermissionError as exc:
            turn_record["status"] = "denied_execute"
            turn_record["error"] = str(exc)
            sessions.write_turn(session_id, turn_record)
            status = "permission_denied"
            bus.emit("turn.completed", {"seq": turn_seq, "status": "denied_execute"})
            completed_turns = turn_seq
            break

        bus.emit("sandbox.started", {"seq": turn_seq, "script_hash": script_hash})
        task = orchestrator.plan_from_script(
            script_text,
            project_id=project_id,
            session_id=session_id,
            ai_model=ai_model,
        )
        bus.emit(
            "sandbox.completed",
            {
                "seq": turn_seq,
                "ok": task.get("status") == "succeeded",
                "patch_count": len(task.get("timeline_patches") or []),
            },
        )

        if task.get("status") != "succeeded":
            if task.get("error_code") == "no_timeline_patches":
                turn_record["status"] = "no_patch"
                turn_record["error"] = task.get("error")
                turn_record["task_id"] = task.get("task_id")
                sessions.write_turn(session_id, turn_record)
                status = "no_patch"
                completed_turns = turn_seq
                bus.emit("turn.completed", {"seq": turn_seq, "status": "no_patch"})
                break
            turn_record["status"] = "sandbox_failed"
            turn_record["error"] = task.get("error")
            turn_record["task_id"] = task.get("task_id")
            previous_error = {
                "stage": "sandbox",
                "message": task.get("error") or task.get("stderr") or "sandbox failed",
            }
            sessions.write_turn(session_id, turn_record)
            previous_script_hash = script_hash
            completed_turns = turn_seq
            bus.emit("turn.completed", {"seq": turn_seq, "status": "sandbox_failed"})
            continue
        if not (task.get("timeline_patches") or []):
            turn_record["status"] = "no_patch"
            turn_record["error"] = "Script executed successfully but emitted no TimelinePatch operations."
            turn_record["task_id"] = task.get("task_id")
            sessions.write_turn(session_id, turn_record)
            status = "no_patch"
            completed_turns = turn_seq
            bus.emit("turn.completed", {"seq": turn_seq, "status": "no_patch"})
            break

        # 4. Success path.
        bus.emit(
            "patch.applied",
            {
                "seq": turn_seq,
                "patch_seq_start": task.get("patch_seq_start"),
                "patch_seq_end": task.get("patch_seq_end"),
            },
        )
        summary_after = inspect_project(orchestrator.project_store, project_id)
        turn_record["status"] = "succeeded"
        turn_record["task_id"] = task.get("task_id")
        turn_record["patch_seq_start"] = task.get("patch_seq_start")
        turn_record["patch_seq_end"] = task.get("patch_seq_end")
        turn_record["clip_count_after"] = summary_after["timeline"]["clip_count"]
        previous_error = None
        previous_script_hash = script_hash
        sessions.write_turn(session_id, turn_record)
        completed_turns = turn_seq
        bus.emit("turn.completed", {"seq": turn_seq, "status": "succeeded"})

        if _DONE_MARKER in script_text:
            status = "done_marker"
            break
    else:
        status = "max_turns_reached"

    final_summary = inspect_project(orchestrator.project_store, project_id)
    sessions.update_meta(
        session_id,
        {
            "status": status,
            "turn_count": completed_turns,
            "final_patch_seq": final_summary["patch_seq"],
            "final_clip_count": final_summary["timeline"]["clip_count"],
        },
    )
    bus.emit(
        "agent.finished",
        {
            "status": status,
            "turns": completed_turns,
            "final_clip_count": final_summary["timeline"]["clip_count"],
        },
    )
    return {
        "session_id": session_id,
        "project_id": project_id,
        "status": status,
        "experimental": True,
        "turns": completed_turns,
        "final_patch_seq": final_summary["patch_seq"],
        "final_clip_count": final_summary["timeline"]["clip_count"],
        "events_path": str(sessions.events_path(session_id)),
        "session_meta_path": str(sessions.meta_path(session_id)),
    }


def _quick_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()
