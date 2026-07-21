"""Multi-agent capability — unbounded parallel sub-task fan-out (``spawn_subtasks``).

``spawn_subtasks`` fans out an arbitrary number of child agents that work IN
PARALLEL on independent goals and return structured results.  Children share the
parent's full tool list (minus ``spawn_subtasks`` itself to prevent recursion)
and the parent's budget — no slicing, no reservation, no artificial caps.

Architecture: children are ``asyncio`` tasks on the session's single event loop
— no threads, ever — so ``AssetRegistry`` thread-confinement, the loop-hop edit
discipline, and ``AskBridge`` future resolution all stay intact.  A child shares
the parent's ``GeminiClientV3`` (stateless per call), ``AssetRegistry``,
``JobRegistry``, and ``ProjectHandle`` via a per-child ``ToolContext`` whose
``extra`` has ``ask_bridge`` stripped (elicit is structurally impossible) and
whose ``output_dir`` is a per-child subdir.

Protocol: a child opens with exactly one ``subagent_start`` and closes with
exactly one ``subagent_result``.  Child TOOL activity rides the EXISTING
``tool_exec_*`` kinds carrying an optional ``agent_id`` field (absent = the
root/parent loop).

Model routing: each subtask may specify a ``model`` override.  When a task
requires full video understanding (visual content analysis, scene recognition,
frame-level reasoning), route it to a multimodal model such as Gemini.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any, Callable

from gemia.budget_guard import BudgetGuard
from gemia.errors import (
    GemiaError,
    RECOVERY_FIX_ARGS,
    RECOVERY_SWITCH_TOOL,
    RECOVERY_TRANSIENT_RETRY,
    ToolError,
)
from gemia.plan_mode import is_plan_safe, plan_gate_message
from gemia.tools import DISPATCHER, TOOL_SCHEMAS
from gemia.tool_outcome import classify_tool_exception, classify_tool_result
from gemia.tools._context import ProgressUpdate, ToolContext
from gemia.turn_ledger import MUTATION_TOOLS, tool_target_key

if TYPE_CHECKING:  # avoid an import cycle: agent_loop_v3 imports tools which... etc.
    from gemia.agent_loop_v3 import AgentLoopV3


# ── legacy tool profiles (kept for backward compat) ─────────────────────────
# New callers should omit tool_profile (defaults to "full" = parent's tool list).

PROFILE_ANNOTATE = frozenset({
    "probe_media", "analyze_media", "extract_frame", "search_library",
    "get_media_annotations", "annotate_media", "write_media_annotation",
})
PROFILE_PROBE = frozenset({
    "probe_media", "analyze_media", "search_library",
    "get_media_annotations", "get_timeline", "get_lumenframe", "get_safe_areas",
})

FORBIDDEN_IN_CHILDREN = frozenset({"spawn_subtasks"})

PROFILES: dict[str, frozenset[str] | None] = {
    "annotate": PROFILE_ANNOTATE,
    "probe": PROFILE_PROBE,
    "full": None,
}

# Backward compat alias
FORBIDDEN_IN_ANY_PROFILE = FORBIDDEN_IN_CHILDREN


# ── rails ────────────────────────────────────────────────────────────────────

CHILD_DEPTH = 1                  # children cannot spawn (depth 1)
DEFAULT_MAX_STEPS = 30           # child model-call cap
HARD_MAX_STEPS = 128
DEFAULT_DEADLINE_SEC = 600.0     # per-batch shared wall-clock deadline
HARD_DEADLINE_SEC = 3600.0
_DOOM_LOOP_THRESHOLD = 3         # per-child, byte-identical (name,args) streak
_REPEATED_FAILURE_NUDGE_THRESHOLD = 5
_TRANSIENT_RETRY_NUDGE_THRESHOLD = 8
_PROGRESS_COALESCE_SEC = 1.0     # ≥1 s between child tool_exec_progress emits
_SUMMARY_CAP = 1200              # per-child summary char cap
_RESULT_CAP = 16_000             # whole tool_result byte cap

_VALID_STATUSES = {"ok", "error", "timeout", "cancelled", "needs_user"}


class SubtaskError(ToolError):
    """Raised by the spawn dispatcher for structural refusals (over-cap children,
    unknown profile, exhausted budget pool) so the model reads a typed refusal.

    A ``ToolError`` subclass so it carries ``code`` + ``recovery`` and the parent
    loop surfaces both in the tool_result and the ``tool_exec_error`` event."""

    def __init__(
        self, message: str, *, code: str = "E_SUBTASK", recovery: str = RECOVERY_FIX_ARGS
    ) -> None:
        super().__init__(message, code=code, recovery=recovery)


def _profile_tools(profile_name: str | None) -> frozenset[str] | None:
    """Return the tool set for a profile.  ``None`` means "full" (all parent tools)."""
    if profile_name is None or profile_name == "full":
        return None
    tools = PROFILES.get(profile_name)
    if tools is None:
        raise SubtaskError(
            f"unknown tool_profile {profile_name!r}; valid: {sorted(PROFILES)}",
            code="E_SUBTASK_PROFILE",
            recovery=RECOVERY_FIX_ARGS,
        )
    return tools


def _child_tool_schemas(profile_tools: frozenset[str] | None) -> list[dict[str, Any]]:
    """The subset of TOOL_SCHEMAS visible to the child.  ``None`` means all tools
    except those in FORBIDDEN_IN_CHILDREN."""
    if profile_tools is None:
        return [
            t for t in TOOL_SCHEMAS
            if t["function"]["name"] not in FORBIDDEN_IN_CHILDREN
        ]
    return [t for t in TOOL_SCHEMAS if t["function"]["name"] in profile_tools]


# ── the child loop ───────────────────────────────────────────────────────────


class SubtaskLoop:
    """AgentLoopV3-lite for child sub-agents.

    Children share the parent's full tool surface (minus ``spawn_subtasks``)
    and the parent's budget by default.  An optional ``client`` override
    enables model routing — e.g. sending video-understanding tasks to a
    multimodal model like Gemini.
    """

    def __init__(
        self,
        *,
        agent_id: str,
        parent: "AgentLoopV3",
        call_id: str,
        goal: str,
        profile_name: str | None = None,
        guard: BudgetGuard | None = None,
        client: Any = None,
        asset_ids: list[str] | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        depth: int = CHILD_DEPTH,
    ) -> None:
        assert depth == CHILD_DEPTH, f"subtask depth must be {CHILD_DEPTH}, got {depth}"
        self.agent_id = agent_id
        self.parent = parent
        self.call_id = call_id
        self.goal = goal
        self.profile_name = profile_name or "full"
        self.profile_tools = _profile_tools(self.profile_name)
        self.guard = guard or parent.budget
        self.client = client or parent.client
        self.asset_ids = list(asset_ids or [])
        self.max_steps = max(1, min(int(max_steps), HARD_MAX_STEPS))

        self._messages: list[dict[str, Any]] = []
        self._final_text_parts: list[str] = []
        self._new_asset_ids: list[str] = []
        self._tool_fail_counts: dict[str, tuple[str, int]] = {}
        self._unresolved_failures: dict[str, str] = {}
        self._recent_calls: list[tuple[str, str]] = []
        self._last_progress_ts: dict[str, float] = {}
        self.steps = 0
        # True once a subagent_result has been emitted for this child (normal
        # completion path). The dispatcher's finally uses this to guarantee
        # exactly one terminal result per started child, even under cancellation.
        self._emitted_result = False

        # Per-child ToolContext: shares registry / jobs / project with the parent
        # (single-loop confinement makes that race-free), but with its own
        # output_dir, its own agent-scoped emit_progress, and ask_bridge REMOVED
        # so elicit is structurally impossible even if a profile bug let it in.
        child_output = self.parent.output_dir / "subtasks" / agent_id
        child_output.mkdir(parents=True, exist_ok=True)
        parent_extra = getattr(self.parent, "_tool_ctx").extra
        child_extra = {k: v for k, v in dict(parent_extra).items() if k != "ask_bridge"}
        self._ctx = ToolContext(
            session_id=self.parent.session_id,
            output_dir=child_output,
            registry=self.parent.registry,
            emit_progress=lambda _u: None,
            extra=child_extra,
            jobs=getattr(self.parent, "_tool_ctx").jobs,
            project=self.parent.project,
        )

    # ── child SSE emits (agent_id attached) ──────────────────────────────

    def _emit(self, event: dict[str, Any]) -> None:
        """Route a child event through the parent's emit sink with agent_id set."""
        event.setdefault("agent_id", self.agent_id)
        event.setdefault("call_id", self.call_id)
        self.parent._emit(event)

    def _make_progress_cb(self, tool_call_id: str, tool_name: str) -> Callable[[ProgressUpdate], None]:
        """Child progress callback, coalesced to ≥1 s per child so 4 verbose
        children cannot evict a disconnected client's replay window (§8)."""
        emit = self.parent._emit
        agent_id = self.agent_id
        call_id = self.call_id
        last = self._last_progress_ts

        def cb(update: ProgressUpdate) -> None:
            now = time.monotonic()
            # Always forward a terminal (100%) update; coalesce the rest.
            is_terminal = update.percent is not None and update.percent >= 100
            if not is_terminal and (now - last.get(agent_id, 0.0)) < _PROGRESS_COALESCE_SEC:
                return
            last[agent_id] = now
            event: dict[str, Any] = {
                "kind": "tool_exec_progress",
                "call_id": call_id,
                "agent_id": agent_id,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
            }
            if update.percent is not None:
                event["percent"] = update.percent
            if update.message:
                event["message"] = update.message
            if update.eta_sec is not None:
                event["eta_seconds"] = update.eta_sec
            emit(event)

        return cb

    # ── transcript helpers (mirrors AgentLoopV3 shapes) ──────────────────

    def _system_prompt(self) -> str:
        if self.profile_tools is not None:
            tool_list = ", ".join(sorted(self.profile_tools))
            tools_note = f"Available tools: {tool_list}."
        else:
            tools_note = (
                "You have the SAME full tool set as the parent agent "
                "(except spawn_subtasks)."
            )
        scope = (
            f"\nScoped assets: {', '.join(self.asset_ids)}." if self.asset_ids else ""
        )
        return (
            "You are a Lumeri sub-agent working on ONE independent goal in "
            "parallel with sibling sub-agents. You CANNOT ask the user or spawn "
            "further sub-agents.\n"
            f"{tools_note}\n"
            f"Your goal:\n{self.goal}{scope}\n\n"
            "Work efficiently: call tools to accomplish the goal, then STOP with a "
            "short final text summary of what you found/did and any asset_ids you "
            "produced. Your final text is the ONLY thing the parent sees, so make "
            "it self-contained. If you need a human decision you cannot make, stop "
            "and say so plainly (the parent will decide whether to ask the user).\n\n"
            "If your task requires understanding VIDEO CONTENT (visual analysis, "
            "scene detection, object recognition, reading on-screen text), prefer "
            "tools like analyze_media that leverage multimodal models."
        )

    def _append_tool_result(self, tool_call_id: str, payload: Any) -> None:
        content = payload if isinstance(payload, str) else json.dumps(
            payload, ensure_ascii=False, default=str
        )
        self._messages.append(
            {"role": "tool", "tool_call_id": tool_call_id, "content": content}
        )

    def _note_failure(self, name: str, code: str, *, limit: int) -> tuple[bool, int]:
        last_code, streak = self._tool_fail_counts.get(name, ("", 0))
        streak = streak + 1 if code == last_code else 1
        self._tool_fail_counts[name] = (code, streak)
        return streak >= limit, streak

    def _append_nudge(self, name: str, code: str, count: int) -> None:
        self._messages.append({
            "role": "user",
            "content": (
                f"Repeated tool failure: `{name}` failed with `{code}` {count} times "
                "in a row. Change arguments, switch tools, or stop and summarize the "
                "blocker in your final text."
            ),
        })

    @staticmethod
    def _operation_class(name: str) -> str:
        """Coarse semantic class used when correlating repaired failures.

        A successful read of an asset proves that the asset is readable; it
        does not prove that an earlier annotation/write against that same
        asset succeeded.  Keeping reads and mutations in separate classes
        still permits legitimate same-target alternatives inside each class
        (for example, ``probe_media`` followed by ``analyze_media``).
        """
        return "mutation" if name in MUTATION_TOOLS else "read"

    @staticmethod
    def _failure_key(name: str, args: dict[str, Any] | None = None) -> str:
        target = tool_target_key(args)
        if target:
            operation_class = SubtaskLoop._operation_class(name)
            return f"target:{operation_class}:{target}"
        return f"tool:{name}"

    def _record_unresolved(
        self, name: str, code: str, args: dict[str, Any] | None = None
    ) -> None:
        self._unresolved_failures[self._failure_key(name, args)] = code

    def _resolve_unresolved(
        self, name: str, args: dict[str, Any] | None = None
    ) -> None:
        self._unresolved_failures.pop(self._failure_key(name, args), None)
        # A successful corrected call also resolves a prior parse failure for
        # that tool, while target-scoped failures on other assets remain.
        self._unresolved_failures.pop(f"tool:{name}", None)

    @staticmethod
    def _is_doom_loop(recent: list[tuple[str, str]]) -> bool:
        if len(recent) < _DOOM_LOOP_THRESHOLD:
            return False
        window = recent[-_DOOM_LOOP_THRESHOLD:]
        return len(set(window)) == 1

    # ── the run ──────────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        """Drive the child to completion and return its structured result dict.

        Never raises for in-child failures (doom loop, budget, plan block) —
        those fold into ``status``/``summary``. asyncio.CancelledError DOES
        propagate (the parent's finally settles + records the terminal status).
        """
        self._emit({
            "kind": "subagent_start",
            "goal": self.goal,
            "tool_profile": self.profile_name,
            "budget": {"max_usd": self.guard.max_usd, "max_seconds": self.guard.max_seconds},
        })

        self._messages.append({"role": "system", "content": self._system_prompt()})
        self._messages.append({"role": "user", "content": self.goal})

        status = "ok"
        child_schemas = _child_tool_schemas(self.profile_tools)

        while self.steps < self.max_steps:
            self.steps += 1
            accum_text: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            by_index: dict[int, dict[str, Any]] = {}

            try:
                async for delta in self.client.stream_turn(
                    self._messages, tools=child_schemas
                ):
                    kind = delta.get("kind")
                    if kind == "text_delta":
                        # Child text is NEVER emitted to SSE (§D6) — accumulate only.
                        accum_text.append(str(delta.get("text", "")))
                    elif kind == "tool_call_start":
                        idx = int(delta["index"])
                        tc = {
                            "index": idx,
                            "id": str(delta.get("id") or f"{self.agent_id}_call_{idx}"),
                            "name": str(delta.get("name")),
                            "args_buf": [],
                            "extra_content": delta.get("extra_content"),
                        }
                        by_index[idx] = tc
                    elif kind == "tool_call_args_delta":
                        tc = by_index.get(int(delta["index"]))
                        if tc is not None:
                            tc["args_buf"].append(str(delta.get("delta", "")))
                    elif kind == "tool_call_extra":
                        tc = by_index.get(int(delta["index"]))
                        if tc is not None:
                            tc["extra_content"] = delta.get("extra_content")
                    elif kind == "error":
                        # A model-stream error ends the child in error status.
                        self._final_text_parts.append(
                            f"[stream error: {delta.get('error')}]"
                        )
                        status = "error"
                        break
            except asyncio.CancelledError:
                raise
            if status == "error":
                break

            if accum_text:
                self._final_text_parts.append("".join(accum_text))

            tool_calls = [by_index[k] for k in sorted(by_index)]

            # Persist the assistant message (text + tool_calls) into the child
            # transcript so the follow-up model call has context.
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": "".join(accum_text) or None,
            }
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    _child_tool_call_message(tc) for tc in tool_calls
                ]
            self._messages.append(assistant_msg)

            if not tool_calls:
                # A returned/raised tool failure is not repaired by merely
                # stopping in prose. Only a later successful call of that same
                # tool clears its unresolved entry.
                if self._unresolved_failures:
                    status = "error"
                break

            for tc in tool_calls:
                await self._dispatch_child_call(tc)
                if self._is_doom_loop(self._recent_calls):
                    self._final_text_parts.append(
                        f"[stopped: repeated the same call {_DOOM_LOOP_THRESHOLD}x with "
                        "no progress]"
                    )
                    status = "error"
                    break
            if status == "error":
                break
        else:
            # Ran out of steps.
            self._final_text_parts.append(
                f"[reached the {self.max_steps}-step cap before finishing]"
            )
            status = "error"

        summary = "\n".join(p for p in self._final_text_parts if p).strip()
        summary = summary[:_SUMMARY_CAP]
        snap = self.guard.snapshot()
        result = {
            "agent_id": self.agent_id,
            "status": status,
            "summary": summary or "(no summary)",
            "asset_ids": list(self._new_asset_ids),
            "data": {},
            "steps": self.steps,
            "spent_usd": snap["spent_usd"],
            "spent_seconds": snap["spent_seconds"],
        }
        self._emit_result(result, elapsed_seconds=snap.get("elapsed_seconds", 0.0))
        return result

    def _emit_result(self, result: dict[str, Any], *, elapsed_seconds: float) -> None:
        if self._emitted_result:
            return
        self._emitted_result = True
        self._emit({
            "kind": "subagent_result",
            "status": result["status"],
            "summary": result["summary"],
            "asset_ids": result["asset_ids"],
            "steps": result["steps"],
            "spent_usd": result["spent_usd"],
            "spent_seconds": result["spent_seconds"],
            "elapsed_seconds": round(float(elapsed_seconds), 2),
        })

    async def _dispatch_child_call(self, tc: dict[str, Any]) -> None:
        """Dispatch one child tool call with the full per-child rail stack:
        fail-closed profile check, live plan-mode re-read, budget gate, then the
        real dispatch through the shared DISPATCHER."""
        tool_call_id = tc["id"]
        name = tc["name"]
        raw_args = "".join(tc["args_buf"])
        parsed_args, parse_error = _parse_child_args(raw_args)

        # Record for the doom-loop guard (byte-identical name+args = pure echo).
        self._recent_calls.append((name, raw_args))

        if parse_error is not None:
            self._append_tool_result(tool_call_id, {
                "error": "arguments were not a valid JSON object",
                "error_code": "E_BAD_ARG",
                "recovery": RECOVERY_FIX_ARGS,
                "parse_error": parse_error,
            })
            self._maybe_nudge(name, "E_BAD_ARG")
            self._record_unresolved(name, "E_BAD_ARG")
            return

        # Recursion prevention: children can never spawn further children.
        if name in FORBIDDEN_IN_CHILDREN:
            self._append_tool_result(tool_call_id, {
                "error": f"'{name}' is forbidden in sub-agents",
                "error_code": "E_SUBTASK_PROFILE",
                "recovery": RECOVERY_SWITCH_TOOL,
            })
            self._maybe_nudge(name, "E_SUBTASK_PROFILE")
            self._record_unresolved(name, "E_SUBTASK_PROFILE", parsed_args)
            return

        # Legacy profile enforcement: when a restricted profile is active, only
        # allow tools explicitly listed in that profile.
        if self.profile_tools is not None and name not in self.profile_tools:
            self._append_tool_result(tool_call_id, {
                "error": (
                    f"'{name}' is not available in the '{self.profile_name}' "
                    "sub-agent profile"
                ),
                "error_code": "E_SUBTASK_PROFILE",
                "recovery": RECOVERY_SWITCH_TOOL,
            })
            self._maybe_nudge(name, "E_SUBTASK_PROFILE")
            self._record_unresolved(name, "E_SUBTASK_PROFILE", parsed_args)
            return

        # Plan-mode inheritance (§7.2): re-read the PARENT's LIVE flag per
        # dispatch so a mid-batch toggle clamps children within one dispatch.
        # Child plan-blocks count toward the CHILD's own failure state only,
        # never the parent's plan_gate hard-stop counter.
        if self.parent.plan_mode and not is_plan_safe(name):
            gate_msg = plan_gate_message(name)
            self._append_tool_result(tool_call_id, {
                "blocked_by_plan_mode": True,
                "error_code": "E_PLAN_MODE",
                "message": gate_msg,
            })
            self._maybe_nudge(name, "E_PLAN_MODE")
            self._record_unresolved(name, "E_PLAN_MODE", parsed_args)
            return

        # Budget gate against the CHILD's own capped guard — a child structurally
        # cannot exceed its slice because its own guard gates it.
        decision = self.guard.check(name)
        if not decision.ok:
            self._append_tool_result(tool_call_id, {
                "blocked_by_budget": True,
                "approval_cannot_override": True,
                "error_code": "E_BUDGET",
                "reason": decision.reason,
                "estimated_cost_usd": decision.estimated_cost_usd,
                "estimated_eta_sec": decision.estimated_eta_sec,
            })
            self._maybe_nudge(name, "E_BUDGET")
            self._record_unresolved(name, "E_BUDGET", parsed_args)
            return

        # Real dispatch. Child tool activity rides the EXISTING tool_exec_*
        # kinds with agent_id attached (§6.2).
        pre_ids = {r.asset_id for r in self.parent.registry.list_records()}
        self._emit({
            "kind": "tool_exec_start",
            "tool_name": name,
            "tool_call_id": tool_call_id,
            "est_cost_usd": decision.estimated_cost_usd,
            "eta_seconds": decision.estimated_eta_sec,
        })
        self._ctx.emit_progress = self._make_progress_cb(tool_call_id, name)

        start_ts = time.monotonic()
        try:
            result = await DISPATCHER[name](parsed_args, self._ctx)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — surface, never swallow
            elapsed = time.monotonic() - start_ts
            self.guard.commit(name, actual_seconds=elapsed)
            outcome = classify_tool_exception(exc)
            err_payload = outcome.error_payload(tool_name=name)
            err_code = str(outcome.error_code or "E_TOOL_FAILED")
            recovery = outcome.recovery
            self._emit({
                "kind": "tool_exec_error",
                "tool_name": name,
                "tool_call_id": tool_call_id,
                "elapsed_seconds": elapsed,
                **err_payload,
            })
            self._append_tool_result(tool_call_id, {**err_payload, "tool_name": name})
            limit = (
                _TRANSIENT_RETRY_NUDGE_THRESHOLD
                if recovery == RECOVERY_TRANSIENT_RETRY
                else _REPEATED_FAILURE_NUDGE_THRESHOLD
            )
            should_nudge, streak = self._note_failure(name, err_code, limit=limit)
            if should_nudge:
                self._append_nudge(name, err_code, streak)
            self._record_unresolved(name, err_code, parsed_args)
            return

        elapsed = time.monotonic() - start_ts
        self.guard.commit(name, actual_seconds=elapsed)
        outcome = classify_tool_result(result)
        if outcome.is_failure:
            err_payload = outcome.error_payload(tool_name=name)
            err_code = str(outcome.error_code or "E_TOOL_FAILED")
            self._emit({
                "kind": "tool_exec_error",
                "tool_name": name,
                "tool_call_id": tool_call_id,
                "elapsed_seconds": elapsed,
                **err_payload,
            })
            self._append_tool_result(tool_call_id, {**err_payload, "tool_name": name})
            limit = (
                _TRANSIENT_RETRY_NUDGE_THRESHOLD
                if outcome.recovery == RECOVERY_TRANSIENT_RETRY
                else _REPEATED_FAILURE_NUDGE_THRESHOLD
            )
            should_nudge, streak = self._note_failure(name, err_code, limit=limit)
            if should_nudge:
                self._append_nudge(name, err_code, streak)
            self._record_unresolved(name, err_code, parsed_args)
            return

        # Only a terminal success proves that this child action completed.
        # ``pending``/``noop``/``partial`` are honest non-failures, but treating
        # them like success would let the child clear an earlier failure and
        # stop in prose with ``status=ok``.  Keep an unresolved marker instead;
        # a later real success on the same target will clear it through the
        # normal retry path below.
        if outcome.state == "success":
            self._tool_fail_counts.pop(name, None)
            self._resolve_unresolved(name, parsed_args)
        else:
            key = self._failure_key(name, parsed_args)
            self._unresolved_failures.setdefault(
                key, f"E_TOOL_{outcome.state.upper()}"
            )

        # New assets this child registered (shared registry; loop-confined ids).
        for r in self.parent.registry.list_records():
            if r.asset_id not in pre_ids and r.asset_id not in self._new_asset_ids:
                self._new_asset_ids.append(r.asset_id)

        model_result = {
            k: v for k, v in result.items()
            if k not in {"thumbnail_path", "thumbnail_for_next_message"}
        } if isinstance(result, dict) else {"result": result}
        event_result = dict(model_result)
        produced_id = model_result.get("asset_id") if isinstance(model_result, dict) else None
        if produced_id and self.parent.registry.contains(str(produced_id)):
            event_result["preview_uri"] = str(
                self.parent.registry.get(str(produced_id)).path
            )
        self._emit({
            "kind": "tool_exec_result",
            "tool_name": name,
            "tool_call_id": tool_call_id,
            "result": event_result,
            "elapsed_seconds": elapsed,
        })
        self._append_tool_result(tool_call_id, model_result)

    def _maybe_nudge(self, name: str, code: str) -> None:
        should_nudge, streak = self._note_failure(
            name, code, limit=_REPEATED_FAILURE_NUDGE_THRESHOLD
        )
        if should_nudge:
            self._append_nudge(name, code, streak)


# ── streaming helpers ────────────────────────────────────────────────────────


def _parse_child_args(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    text = (raw or "").strip()
    if not text:
        return {}, None
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"JSONDecodeError: {exc}"
    if not isinstance(value, dict):
        return None, f"tool args must be a JSON object, got {type(value).__name__}"
    return value, None


def _child_tool_call_message(tc: dict[str, Any]) -> dict[str, Any]:
    message = {
        "id": tc["id"],
        "type": "function",
        "function": {"name": tc["name"], "arguments": "".join(tc["args_buf"])},
    }
    if tc.get("extra_content") is not None:
        message["extra_content"] = tc["extra_content"]
    return message


# ── the spawn_subtasks host verb ─────────────────────────────────────────────


def _build_child_client(
    parent: "AgentLoopV3", model: str | None
) -> Any:
    """Return a model client for the child.  When *model* is set, construct a
    dedicated ``GeminiClientV3`` pointed at that model (e.g. a multimodal Gemini
    for video understanding).  Otherwise reuse the parent's client."""
    if not model:
        return parent.client
    from gemia.gemini_client import GeminiClientV3
    return GeminiClientV3(model=model)


async def dispatch(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """``spawn_subtasks`` host verb.  Fans out an arbitrary number of children as
    asyncio tasks sharing the parent's budget, under a shared deadline."""
    parent: "AgentLoopV3" | None = ctx.extra.get("agent_loop")
    if parent is None:
        raise SubtaskError(
            "spawn_subtasks is only available inside a live agent loop",
            code="E_SUBTASK", recovery=RECOVERY_SWITCH_TOOL,
        )
    call_id: str = str(ctx.extra.get("call_id") or "spawn")

    subtasks = args.get("subtasks")
    if not isinstance(subtasks, list) or not subtasks:
        raise SubtaskError(
            "subtasks must be a non-empty array", code="E_SUBTASK", recovery=RECOVERY_FIX_ARGS
        )
    n = len(subtasks)

    specs: list[dict[str, Any]] = []
    for i, st in enumerate(subtasks):
        if not isinstance(st, dict):
            raise SubtaskError(
                f"subtasks[{i}] must be an object", code="E_SUBTASK", recovery=RECOVERY_FIX_ARGS
            )
        goal = st.get("goal")
        if not isinstance(goal, str) or not goal.strip():
            raise SubtaskError(
                f"subtasks[{i}].goal is required", code="E_SUBTASK", recovery=RECOVERY_FIX_ARGS
            )
        profile_name = st.get("tool_profile")
        if profile_name is not None:
            _profile_tools(str(profile_name))
        specs.append(st)

    deadline = float(args.get("deadline_sec") or DEFAULT_DEADLINE_SEC)
    deadline = max(1.0, min(deadline, HARD_DEADLINE_SEC))

    children: list[SubtaskLoop] = []
    tasks: list[asyncio.Task] = []
    results_by_agent: dict[str, dict[str, Any]] = {}

    try:
        for i, st in enumerate(specs):
            agent_id = f"sub_{i + 1}"
            profile_name = st.get("tool_profile")
            model = st.get("model")

            child = SubtaskLoop(
                agent_id=agent_id,
                parent=parent,
                call_id=call_id,
                goal=str(st["goal"]),
                profile_name=str(profile_name) if profile_name else None,
                client=_build_child_client(parent, model),
                asset_ids=[str(a) for a in (st.get("asset_ids") or [])],
                max_steps=int(st.get("max_steps", DEFAULT_MAX_STEPS)),
            )
            children.append(child)
            tasks.append(asyncio.ensure_future(_run_child(child, results_by_agent)))

        if tasks:
            await asyncio.wait(tasks, timeout=deadline)

    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for child in children:
            snap = child.guard.snapshot()
            terminal = results_by_agent.get(child.agent_id)
            if terminal is None:
                terminal = {
                    "agent_id": child.agent_id, "status": "timeout",
                    "summary": "cancelled before completion",
                    "asset_ids": list(child._new_asset_ids),
                    "data": {}, "steps": child.steps,
                    "spent_usd": snap["spent_usd"], "spent_seconds": snap["spent_seconds"],
                }
                results_by_agent[child.agent_id] = terminal
            try:
                child._emit_result(terminal, elapsed_seconds=snap.get("elapsed_seconds", 0.0))
            except Exception:  # noqa: BLE001
                pass

    # Ordered results (sub_1, sub_2, …) capped at 16 KB total.
    ordered = [
        results_by_agent[f"sub_{i + 1}"]
        for i in range(n)
        if f"sub_{i + 1}" in results_by_agent
    ]
    ordered = _cap_results(ordered)
    payload = {
        "summary": _batch_summary(ordered),
        "subtasks": ordered,
        "count": len(ordered),
    }
    failed = [r for r in ordered if r.get("status") != "ok"]
    if failed:
        payload.update(
            {
                "status": "failed",
                "error": f"{len(failed)} subtask(s) did not complete successfully",
                "error_code": "E_SUBTASK_FAILED",
            }
        )
    return payload


async def _run_child(
    child: SubtaskLoop, sink: dict[str, dict[str, Any]]
) -> None:
    """Run a child; on cancellation record a terminal 'timeout' result so the
    finally settlement never overwrites a real one and a subagent_result exists.

    The result is written to ``sink`` (not returned) so the finally block can see
    completions even for tasks it later gathers after cancelling."""
    try:
        result = await child.run()
        sink[child.agent_id] = result
    except asyncio.CancelledError:
        # Straggler past the deadline or parent-error unwind. Record a terminal
        # timeout status here; the subagent_result emit + settlement happen in
        # the dispatcher's finally (which owns the reservation).
        snap = child.guard.snapshot()
        sink.setdefault(child.agent_id, {
            "agent_id": child.agent_id, "status": "timeout",
            "summary": "sub-agent cancelled (deadline or parent error)",
            "asset_ids": list(child._new_asset_ids), "data": {},
            "steps": child.steps,
            "spent_usd": snap["spent_usd"], "spent_seconds": snap["spent_seconds"],
        })
        raise


def _cap_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Truncate summaries round-robin until the whole payload is ≤16 KB (§4.3)."""
    def size(rs: list[dict[str, Any]]) -> int:
        return len(json.dumps(rs, ensure_ascii=False, default=str).encode("utf-8"))

    if size(results) <= _RESULT_CAP:
        return results
    # Round-robin shave 200 chars off the longest summary until we fit (or all
    # summaries are minimal).
    guard = 0
    while size(results) > _RESULT_CAP and guard < 10_000:
        guard += 1
        longest = max(results, key=lambda r: len(r.get("summary", "")))
        s = longest.get("summary", "")
        if len(s) <= 40:
            break
        longest["summary"] = s[: max(40, len(s) - 200)] + "…"
    return results


def _batch_summary(results: list[dict[str, Any]]) -> str:
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    parts = [f"{v} {k}" for k, v in sorted(by_status.items())]
    total_usd = round(sum(float(r.get("spent_usd", 0.0)) for r in results), 4)
    total_sec = round(sum(float(r.get("spent_seconds", 0.0)) for r in results), 2)
    return (
        f"{len(results)} sub-agent(s): {', '.join(parts)}; "
        f"spent ${total_usd} / {total_sec}s total"
    )


__all__ = [
    "PROFILE_ANNOTATE",
    "PROFILE_PROBE",
    "PROFILES",
    "FORBIDDEN_IN_CHILDREN",
    "FORBIDDEN_IN_ANY_PROFILE",
    "DEFAULT_MAX_STEPS",
    "HARD_MAX_STEPS",
    "DEFAULT_DEADLINE_SEC",
    "HARD_DEADLINE_SEC",
    "SubtaskLoop",
    "SubtaskError",
    "dispatch",
]
