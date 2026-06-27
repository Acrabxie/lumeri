"""Lumeri v3 agent loop.

Contract — what this loop is and what it is NOT:

  - It streams from Gemini through the v3 OpenRouter client and forwards
    every real chunk to the SSE transport. ``model_text_delta`` events
    ONLY come from real Gemini text chunks. The host never fabricates
    status narration of its own. If the model is silent, the user-facing
    stream stays silent.

  - It accumulates function-calling tool_call args across stream
    chunks, then dispatches each call via ``gemia.tools.DISPATCHER``.
    Errors raised by a dispatcher are caught here, surfaced as
    ``tool_exec_error`` events, fed back to the model as a tool_result,
    and the loop continues. We do not swallow errors — every except
    block emits an event and appends a structured tool_result for the
    model to read.

  - It does NOT cap the total number of tool steps per turn. A research
    or see→modify→rerun build loop legitimately needs many steps, so the
    only runaway guard is a per-tool circuit breaker: if the SAME tool
    fails to dispatch ``_MAX_CONSECUTIVE_TOOL_FAILURES`` times in a row
    (error, bad-JSON args, or budget/visual gate), the turn stops. A
    successful dispatch of that tool resets its streak. Real cost/time
    stay bounded by ``BudgetGuard`` ($ + execution seconds).
      * ``visual_inspections_this_turn``   — capped at
        ``max_visual_inspections`` (incremented ONLY when an
        ``analyze_media`` call actually produces a thumbnail for the
        next user message). Independent of the failure breaker.

  - It implements Plan-B visual feedback: when a dispatcher returns
    ``thumbnail_for_next_message=True``, the loop appends a multimodal
    user message with the thumbnail image_url before the next model
    call. This path is ONLY triggered by a dispatcher-flagged result,
    which today only ``analyze_media`` produces. There is no keyword
    detection. The host never decides to show the model a thumbnail
    because the user "seemed to want it"; the model has to ask via
    ``analyze_media`` explicitly.

  - When the model emits no tool_calls and the stream ends, the loop
    emits ``turn_complete`` with the asset_ids produced during this
    turn and returns. It does not retry, does not "ask the user", does
    not synthesize a follow-up.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from gemia.budget_guard import BudgetGuard
from gemia.errors import GemiaError, RECOVERY_FIX_ARGS, RECOVERY_TRANSIENT_RETRY
from gemia.gemini_client import GeminiClientV3
from gemia.project_store import ProjectHandle
from gemia.tools import DISPATCHER, TOOL_SCHEMAS, AssetRegistry, ToolContext
from gemia.tools._ask_bridge import AskBridge
from gemia.tools._context import ProgressUpdate
from gemia.transport.sse import REGISTRY as SSE_REGISTRY


_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "system_v3.md"
_ROLLING_USER_TURNS = 8

# RC4: one-shot completion-check gate before ending on no-tool-calls
COMPLETION_CHECK_ENABLED = True

# Circuit breaker: there is no cap on the TOTAL number of tool steps in a turn
# (a research + see→modify→rerun build loop legitimately needs many). Instead,
# we trip only when the SAME (tool, error-class) repeats this many times in a
# row — that is the real runaway signature (the model stuck hammering the
# identical broken/over-budget call). A model that reads a typed error and
# ADAPTS — a different error_code, or switching tools — is treated as progress,
# not runaway: its streak soft-resets. A successful dispatch clears the streak
# entirely. Genuine cost/time stays bounded by BudgetGuard.
_MAX_CONSECUTIVE_TOOL_FAILURES = 5
# Transient failures (recovery="transient_retry", e.g. a flaky network blip)
# get more headroom, because re-issuing the identical call is the *correct*
# move for them — they are not a logic error the model needs to fix.
_MAX_TRANSIENT_RETRIES = 8

# Success-BLIND doom-loop guard (ported from opencode processor.ts,
# DOOM_LOOP_THRESHOLD=3). The per-(tool, error_code) circuit breaker above only
# trips on FAILURES. But a loop can also get stuck repeating a call that keeps
# "succeeding" — or whose result the model ignores — and re-issuing the exact
# same tool with byte-identical arguments forever. That is not progress, it is a
# stuck model echoing itself. Independent of success/failure: if the last
# ``_DOOM_LOOP_THRESHOLD`` tool calls in a turn are the SAME tool name with the
# SAME (byte-identical) raw args JSON, the turn is looping on itself — emit a
# structured turn_error and stop. Distinct args (real progress / different work)
# never trip it.
_DOOM_LOOP_THRESHOLD = 3


# ──────────────────────────────────────────────────────────────────────
# Stream accumulators (one stream = one model call)
# ──────────────────────────────────────────────────────────────────────


@dataclass
class _ToolCallAccumulator:
    """One tool call accumulated across many stream chunks."""

    index: int
    id: str
    name: str
    extra_content: Any | None = None
    args_buf: list[str] = field(default_factory=list)

    @property
    def args(self) -> str:
        return "".join(self.args_buf)


@dataclass
class _StreamAccumulator:
    """Everything collected from one model stream until it ends."""

    text_buf: list[str] = field(default_factory=list)
    tool_calls_by_index: dict[int, _ToolCallAccumulator] = field(default_factory=dict)
    finish_reason: str | None = None

    @property
    def text(self) -> str:
        return "".join(self.text_buf)

    @property
    def tool_calls(self) -> list[_ToolCallAccumulator]:
        return [self.tool_calls_by_index[k] for k in sorted(self.tool_calls_by_index)]


# ──────────────────────────────────────────────────────────────────────
# Pure helpers
# ──────────────────────────────────────────────────────────────────────


def _load_system_template() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _parse_args(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse JSON tool-call args. Returns (parsed, None) or (None, error_message)."""
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


def _tool_call_message(tc: _ToolCallAccumulator) -> dict[str, Any]:
    message = {
        "id": tc.id,
        "type": "function",
        "function": {"name": tc.name, "arguments": tc.args},
    }
    if tc.extra_content is not None:
        # Gemini/OpenRouter tool calls can carry provider metadata such as
        # thought_signature. The follow-up request must echo it on the
        # assistant tool_call part, or Gemini rejects the next model call.
        message["extra_content"] = tc.extra_content
    return message


def _thumbnail_user_content(paths: list[Path]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": "Thumbnails for the analyze_media call(s) you just made are attached below.",
        }
    ]
    for p in paths:
        data = Path(p).read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            }
        )
    return parts


# ──────────────────────────────────────────────────────────────────────
# Main loop
# ──────────────────────────────────────────────────────────────────────


EventSink = Callable[[dict[str, Any]], None]


class AgentLoopV3:
    def __init__(
        self,
        *,
        session_id: str,
        output_dir: Path,
        max_visual_inspections: int = 3,
        budget_max_usd: float = 5.0,
        budget_max_seconds: float = 600.0,
        gemini_client: GeminiClientV3 | None = None,
        emit_event: EventSink | None = None,
        sessions_root: Path | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.session_id = session_id
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._max_visual_inspections = int(max_visual_inspections)

        self.registry = AssetRegistry()
        self.budget = BudgetGuard(max_usd=budget_max_usd, max_seconds=budget_max_seconds)
        self.client = gemini_client or GeminiClientV3()

        self._messages: list[dict[str, Any]] = []
        self._pinned_intent: str | None = None
        self._pending_thumbnails: list[Path] = []
        self._turn_count = 0
        self._system_template = _load_system_template()
        self._emit: EventSink = emit_event or self._emit_via_sse_registry

        self.project = ProjectHandle.open(
            self.output_dir / "project",
            session_id,
            session_id=session_id,
            on_patch=lambda info: self._emit({"kind": "timeline_op", **info}),
        )

        # Human-in-the-loop bridge for the ``elicit`` verb: lets a tool dispatcher
        # emit an ask_question event and await the user's answer (delivered from the
        # HTTP thread via deliver_ask_answer).
        self._ask_bridge = AskBridge(self._emit)

        _extra = dict(extra or {})
        _extra.setdefault("ask_bridge", self._ask_bridge)
        self._tool_ctx = ToolContext(
            session_id=session_id,
            output_dir=self.output_dir,
            registry=self.registry,
            emit_progress=lambda _u: None,
            extra=_extra,
            project=self.project,
        )

        self.sessions_root = Path(sessions_root) if sessions_root else None
        if self.sessions_root is not None:
            self._write_session_meta(turn_count=0)

    def _write_session_meta(self, *, turn_count: int) -> None:
        """Write a v2-SessionStore-compatible meta.json so legacy loaders can read it."""
        sdir = self.sessions_root / self.session_id
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / "turns").mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()
        meta = {
            "session_id": self.session_id,
            "project_id": "v3-session",
            "goal": self._pinned_intent or "",
            "max_turns": None,  # no fixed per-turn tool-step cap (failure breaker instead)
            "ai_model": self.client.model,
            "created_at": now,
            "updated_at": now,
            "status": "running",
            "turn_count": int(turn_count),
            "loop_version": "v3",
        }
        (sdir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ── plumbing ─────────────────────────────────────────────────────

    def _emit_via_sse_registry(self, event: dict[str, Any]) -> None:
        SSE_REGISTRY.emit(self.session_id, event)

    def deliver_ask_answer(self, question_id: str, answers: dict[str, Any]) -> bool:
        """Deliver a user's answer to a pending ``elicit`` question.

        Thread-safe: called from the HTTP handler thread; the bridge hops back onto
        this session's event loop to resolve the awaiting future. Returns True if a
        matching pending question was found.
        """
        return self._ask_bridge.deliver(question_id, answers)

    def add_external_asset(self, path: Path, *, summary: str = "") -> str:
        record = self.registry.add_external(Path(path), summary=summary or None)
        return record.asset_id

    def _get_lumenframe_prompt_text(self) -> str:
        """Get lumenframe document summary for prompt injection.

        If lumenframe is available, returns a compact summary of the layer
        tree and selection. Otherwise returns a placeholder.
        """
        try:
            from gemia.tools import layer as _layer
            # Access the session-local document cache
            if not hasattr(_layer, "_DOC_CACHE") or self.session_id not in _layer._DOC_CACHE:
                return "(no lumenframe document in session yet)"
            doc = _layer._DOC_CACHE[self.session_id]
            root = doc.get("root", {})
            selection = doc.get("selection", [])
            canvas = doc.get("canvas", {})

            lines = []
            lines.append(f"Canvas: {canvas.get('width')}×{canvas.get('height')} @ {canvas.get('fps')} fps")
            if root:
                lines.append("Layer tree:")
                # Use the compact tree summary function from layer.py
                tree_summary = _layer._compact_tree_summary(root)
                lines.append(tree_summary)
            if selection:
                lines.append(f"Selection: {', '.join(str(id)[:12] for id in selection)}")
            return "\n".join(lines) if lines else "(empty document)"
        except (ImportError, AttributeError, KeyError):
            return "(lumenframe not available)"

    def _get_lumenframe_ops_catalog(self) -> str:
        """Get lumenframe operations catalog for prompt injection.

        **Conditional injection (on-demand):**
        - If the session has a non-empty lumenframe doc (root.children with ≥1 layer),
          inject the complete operation vocabulary from lumenframe.describe_ops().
        - If the doc is empty or absent, return a minimal one-line pointer instead
          of the full 3200+ character catalog, avoiding noise for non-layer tasks.
          Once the user starts a lumenframe edit, the full vocabulary auto-injects.
        """
        try:
            from gemia.tools import layer as _layer
            from lumenframe import describe_ops

            # Check if doc exists and has layers
            if hasattr(_layer, "_DOC_CACHE") and self.session_id in _layer._DOC_CACHE:
                doc = _layer._DOC_CACHE[self.session_id]
                root = doc.get("root", {})
                children = root.get("children", [])
                # Non-empty doc: inject full ops catalog
                if children:
                    return describe_ops()

            # Empty or missing doc: minimal pointer
            return "Layer editing available via lumen_* tools — call lumen_get to start."
        except (ImportError, Exception):
            return "(lumenframe operations not available)"

    def render_messages(self) -> list[dict[str, Any]]:
        """Build the messages list for the next model call.

        System prompt = ``system_v3.md`` with the placeholders filled in:
        ``{{asset_registry}}`` from the live AssetRegistry compact text,
        ``{{pending_jobs}}`` from the live JobRegistry compact text,
        ``{{lumenframe_ops}}`` from lumenframe.describe_ops() operation catalog,
        ``{{lumenframe}}`` from the session lumenframe document state (if any),
        ``{{timeline}}`` from the session project's compact timeline summary,
        and ``{{pinned_intent}}`` from the user's first message in this
        session. After the system message comes the rolling
        user/assistant/tool window in chronological order.
        """
        lumenframe_ops = self._get_lumenframe_ops_catalog()
        lumenframe_text = self._get_lumenframe_prompt_text()
        system_filled = (
            self._system_template
            .replace("{{asset_registry}}", self.registry.compact_text())
            .replace("{{pending_jobs}}", self._tool_ctx.jobs.compact_text_for_prompt())
            .replace("{{lumenframe_ops}}", lumenframe_ops)
            .replace("{{lumenframe}}", lumenframe_text)
            .replace("{{timeline}}", self.project.compact_text())
            .replace("{{pinned_intent}}", self._pinned_intent or "(not yet provided)")
        )
        msgs = [{"role": "system", "content": system_filled}, *self._messages]

        # Recency grounding: the live state already lives in the system prompt, but
        # it sits in a low-attention slot while the pinned first request is re-shown
        # every turn — so the model over-anchors on the original framing and
        # under-reads the current reality. Surface a short state digest in the most
        # RECENT message (the slot the model attends to most) so each next step is
        # grounded in what is actually there now. We append into the last message's
        # content rather than adding a message, to preserve the alternating-role
        # contract the client requires (consecutive tool results fold into one user
        # message; a second user message would break it).
        digest = self._env_recency_digest()
        if digest and len(msgs) > 1:
            tail = msgs[-1]
            if tail.get("role") in ("user", "tool") and isinstance(tail.get("content"), str):
                tail = dict(tail)
                tail["content"] = f"{tail['content']}\n\n{digest}" if tail["content"] else digest
                msgs[-1] = tail
        return msgs

    def _env_recency_digest(self) -> str:
        """A short snapshot of the live state for the recency slot.

        Deliberately brief: the full Timeline / Layer Document / asset registry are
        in the system prompt. This is the *pointer* that pulls attention back to the
        present and tells the model to act on current reality, not on the pinned
        original request or its memory of earlier turns.
        """
        def _first(text: str) -> str:
            for line in (text or "").splitlines():
                line = line.strip()
                if line:
                    return line
            return ""

        snaps: list[str] = []
        tl = _first(self.project.compact_text())
        if tl:
            snaps.append(f"Timeline: {tl}")
        lf = self._get_lumenframe_prompt_text() or ""
        if lf and not lf.startswith("("):
            snaps.append("Layers: " + " | ".join(s.strip() for s in lf.splitlines() if s.strip())[:240])
        assets = [r.asset_id for r in self.registry.list_records()][-4:]
        if assets:
            snaps.append("Latest assets: " + ", ".join(assets))
        if not snaps:
            return ""
        return (
            "[Current state — ground your NEXT step in this present reality, not the "
            "original request or your memory of earlier turns. Re-read the full "
            "Timeline / Layer Document / asset registry above before a consequential "
            "step; after a change, confirm the result here and correct course if it "
            "diverged.]\n" + "\n".join(snaps)
        )

    def _append_tool_result(self, call_id: str, payload: Any) -> None:
        if isinstance(payload, str):
            content = payload
        else:
            content = json.dumps(payload, ensure_ascii=False, default=str)
        self._messages.append(
            {"role": "tool", "tool_call_id": call_id, "content": content}
        )

    def _note_tool_failure(
        self,
        fail_state: dict[str, tuple[str, int]],
        name: str,
        code: str,
        *,
        limit: int,
    ) -> tuple[bool, int]:
        """Record a non-successful call of ``name`` with failure class ``code``
        (error, parse failure, or gate).

        Counts only CONSECUTIVE failures of the same ``(name, code)``: if the
        code differs from this tool's last failure, the model is adapting, so
        the streak resets to 1 rather than climbing toward the breaker. Returns
        ``(tripped, streak)`` where ``tripped`` is True once ``streak`` reaches
        ``limit``."""
        last_code, streak = fail_state.get(name, ("", 0))
        streak = streak + 1 if code == last_code else 1
        fail_state[name] = (code, streak)
        return streak >= limit, streak

    def _emit_tool_breaker(self, name: str, count: int) -> None:
        self._emit(
            {
                "kind": "turn_error",
                "error": (
                    f"tool '{name}' hit the same failure {count} times in a row this "
                    f"turn; stopping. Change the approach or use a different tool."
                ),
            }
        )

    def _emit_doom_loop(self, name: str, count: int) -> None:
        """Success-blind doom-loop signal: the last ``count`` tool calls were the
        SAME tool with byte-identical args, so the turn is repeating itself
        (regardless of whether each call succeeded). Stop the turn."""
        self._emit(
            {
                "kind": "turn_error",
                "reason": "doom_loop",
                "tool_name": name,
                "repeat_count": count,
                "error": (
                    f"doom loop: tool '{name}' was called {count} times in a row with "
                    f"byte-identical arguments this turn; stopping. The loop is "
                    f"repeating itself — change the arguments or the approach."
                ),
            }
        )

    @staticmethod
    def _is_doom_loop(recent: list[tuple[str, str]]) -> bool:
        """True when the last ``_DOOM_LOOP_THRESHOLD`` recorded tool calls are the
        SAME (tool_name, raw-args-JSON) tuple, byte-for-byte. ``recent`` is the
        per-turn rolling history of dispatched (name, args) tuples."""
        if len(recent) < _DOOM_LOOP_THRESHOLD:
            return False
        window = recent[-_DOOM_LOOP_THRESHOLD:]
        return all(item == window[0] for item in window)

    def _trim_rolling_window(self) -> None:
        user_idx = [i for i, m in enumerate(self._messages) if m.get("role") == "user"]
        if len(user_idx) <= _ROLLING_USER_TURNS:
            return
        cutoff = user_idx[-_ROLLING_USER_TURNS]
        self._messages = self._messages[cutoff:]

    # ── public entrypoint ────────────────────────────────────────────

    async def run_turn(self, user_message: str) -> None:
        """Run one user turn until the model stops calling tools."""
        if self._pinned_intent is None:
            self._pinned_intent = user_message
        self._messages.append({"role": "user", "content": user_message})
        self._trim_rolling_window()
        try:
            await self._drive_turn()
        finally:
            self._turn_count += 1
            if self.sessions_root is not None:
                self._write_session_meta(turn_count=self._turn_count)

    # ── the loop ─────────────────────────────────────────────────────

    async def _drive_turn(self) -> None:
        """One turn: stream → dispatch any tool_calls → repeat → emit turn_complete.

        There is no fixed cap on the total number of tool steps in a turn.
        ``visual_inspections_this_turn`` still caps analyze_media thumbnails,
        and ``tool_fail_counts`` drives the per-tool consecutive-failure
        circuit breaker (see ``_MAX_CONSECUTIVE_TOOL_FAILURES``). Genuine
        cost/time stay bounded by BudgetGuard.
        """
        pre_asset_ids = {r.asset_id for r in self.registry.list_records()}
        visual_inspections_this_turn = 0
        # name → (last_failure_code, consecutive_streak) for the circuit breaker.
        tool_fail_counts: dict[str, tuple[str, int]] = {}
        # Rolling history of (tool_name, raw-args-JSON) for THIS turn, used by the
        # success-blind doom-loop guard. We only need the last few entries, but a
        # plain list is simplest; it never grows unbounded because the turn stops
        # the moment the breaker or doom-loop guard trips.
        recent_tool_calls: list[tuple[str, str]] = []

        self._emit({"kind": "turn_start"})

        # RC4: per-turn one-shot completion check guard (not per-loop iteration).
        completion_check_done = False

        while True:
            accum = _StreamAccumulator()
            messages = self.render_messages()

            # ---- stream from model ---------------------------------
            async for delta in self.client.stream_turn(messages, tools=TOOL_SCHEMAS):
                kind = delta["kind"]
                if kind == "text_delta":
                    accum.text_buf.append(delta["text"])
                    self._emit({"kind": "model_text_delta", "delta": delta["text"]})
                elif kind == "tool_call_start":
                    tc = _ToolCallAccumulator(
                        index=int(delta["index"]),
                        id=str(delta["id"] or f"call_{delta['index']}"),
                        name=str(delta["name"]),
                        extra_content=delta.get("extra_content"),
                    )
                    accum.tool_calls_by_index[tc.index] = tc
                    self._emit(
                        {
                            "kind": "model_tool_call_start",
                            "call_id": tc.id,
                            "tool_name": tc.name,
                        }
                    )
                elif kind == "tool_call_args_delta":
                    tc = accum.tool_calls_by_index.get(int(delta["index"]))
                    if tc is not None:
                        tc.args_buf.append(str(delta["delta"]))
                elif kind == "tool_call_extra":
                    tc = accum.tool_calls_by_index.get(int(delta["index"]))
                    if tc is not None:
                        tc.extra_content = delta.get("extra_content")
                elif kind == "finish":
                    accum.finish_reason = str(delta["reason"])
                elif kind == "error":
                    self._emit({"kind": "turn_error", "error": str(delta["error"])})
                    return

            # ---- persist the assistant message ---------------------
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": accum.text if accum.text else None,
            }
            if accum.tool_calls:
                assistant_msg["tool_calls"] = [
                    _tool_call_message(tc)
                    for tc in accum.tool_calls
                ]
            self._messages.append(assistant_msg)

            # ---- model called no tools → RC4 completion gate --------
            if not accum.tool_calls:
                if COMPLETION_CHECK_ENABLED and not completion_check_done:
                    # One-shot nudge before honest stop (RC4 host-side gate).
                    # Inject a user message prompting the model to verify completion.
                    pinned_summary = (
                        f"原始请求：{self._pinned_intent}"
                        if self._pinned_intent
                        else "原始请求：（未提供）"
                    )
                    nudge_msg = (
                        f"目标核对：{pinned_summary}。"
                        "若已完全完成，简短确认做了什么然后停下。"
                        "若未完成，立刻继续调用下一个工具——不要等用户、不要重复已完成的工作。"
                        "若确实被卡住需要用户输入，明确说明。"
                    )
                    self._messages.append({"role": "user", "content": nudge_msg})
                    completion_check_done = True
                    self._emit({"kind": "completion_check"})
                    continue
                else:
                    # Already did one-shot check or gate is disabled → honest stop.
                    final_ids = [
                        r.asset_id
                        for r in self.registry.list_records()
                        if r.asset_id not in pre_asset_ids
                    ]
                    self._emit(
                        {"kind": "turn_complete", "final_asset_ids": final_ids}
                    )
                    return

            # ---- dispatch each tool call sequentially --------------
            for tc in accum.tool_calls:
                parsed_args, parse_error = _parse_args(tc.args)

                self._emit(
                    {
                        "kind": "model_tool_call_ready",
                        "call_id": tc.id,
                        "tool_name": tc.name,
                        "args": (
                            parsed_args
                            if parse_error is None
                            else {"_raw": tc.args, "_parse_error": parse_error}
                        ),
                    }
                )

                if parse_error is not None:
                    self._emit(
                        {
                            "kind": "tool_exec_error",
                            "call_id": tc.id,
                            "tool_name": tc.name,
                            "error": f"tool args not valid JSON object: {parse_error}",
                            "error_code": "E_BAD_ARG",
                            "recovery": RECOVERY_FIX_ARGS,
                        }
                    )
                    self._append_tool_result(
                        tc.id,
                        {
                            "error": "arguments were not a valid JSON object",
                            "error_code": "E_BAD_ARG",
                            "recovery": RECOVERY_FIX_ARGS,
                            "parse_error": parse_error,
                            "raw_arguments": tc.args,
                        },
                    )
                    tripped, streak = self._note_tool_failure(
                        tool_fail_counts, tc.name, "E_BAD_ARG",
                        limit=_MAX_CONSECUTIVE_TOOL_FAILURES,
                    )
                    if tripped:
                        self._emit_tool_breaker(tc.name, streak)
                        return
                    continue

                # Budget gate (cost + time). Model decides what to do.
                decision = self.budget.check(tc.name)
                if not decision.ok:
                    self._emit(
                        {
                            "kind": "budget_gate",
                            "call_id": tc.id,
                            "tool_name": tc.name,
                            "reason": decision.reason,
                            "alternatives": decision.alternatives,
                            "estimated_cost_usd": decision.estimated_cost_usd,
                            "estimated_eta_sec": decision.estimated_eta_sec,
                        }
                    )
                    self._append_tool_result(
                        tc.id,
                        {
                            "needs_approval": True,
                            "reason": decision.reason,
                            "alternatives": decision.alternatives,
                            "estimated_cost_usd": decision.estimated_cost_usd,
                            "estimated_eta_sec": decision.estimated_eta_sec,
                        },
                    )
                    # A gate is a non-dispatch: count it so the model cannot
                    # spin forever re-requesting an over-budget tool.
                    tripped, streak = self._note_tool_failure(
                        tool_fail_counts, tc.name, "E_BUDGET",
                        limit=_MAX_CONSECUTIVE_TOOL_FAILURES,
                    )
                    if tripped:
                        self._emit_tool_breaker(tc.name, streak)
                        return
                    continue

                # Independent visual_inspections cap. Only enforced for
                # analyze_media.
                if (
                    tc.name == "analyze_media"
                    and visual_inspections_this_turn >= self._max_visual_inspections
                ):
                    cap_reason = (
                        f"max_visual_inspections={self._max_visual_inspections} "
                        f"already reached in this turn; no further thumbnails "
                        f"will be attached."
                    )
                    self._emit(
                        {
                            "kind": "budget_gate",
                            "call_id": tc.id,
                            "tool_name": tc.name,
                            "reason": cap_reason,
                            "alternatives": [],
                        }
                    )
                    self._append_tool_result(
                        tc.id, {"needs_approval": True, "reason": cap_reason}
                    )
                    tripped, streak = self._note_tool_failure(
                        tool_fail_counts, tc.name, "E_VISUAL_CAP",
                        limit=_MAX_CONSECUTIVE_TOOL_FAILURES,
                    )
                    if tripped:
                        self._emit_tool_breaker(tc.name, streak)
                        return
                    continue

                # Real dispatch ------------------------------------------------
                self._emit(
                    {
                        "kind": "tool_exec_start",
                        "call_id": tc.id,
                        "tool_name": tc.name,
                        "est_cost_usd": decision.estimated_cost_usd,
                        "eta_seconds": decision.estimated_eta_sec,
                    }
                )
                self._tool_ctx.emit_progress = self._make_progress_cb(tc.id, tc.name)

                start_ts = time.monotonic()
                try:
                    result = await DISPATCHER[tc.name](parsed_args, self._tool_ctx)
                except Exception as exc:
                    elapsed = time.monotonic() - start_ts
                    self.budget.commit(tc.name, actual_seconds=elapsed)
                    # Surface the failure with its structure intact. A GemiaError
                    # (incl. ToolError) carries error_code / recovery / valid_options
                    # / hint — exactly the material that lets the model self-correct
                    # precisely. Anything else falls back to a flat
                    # "TypeName: message" under error_code E_UNCAUGHT.
                    if isinstance(exc, GemiaError):
                        err_payload = exc.to_payload()
                        err_code = exc.code
                        recovery = getattr(exc, "recovery", None)
                    else:
                        err_payload = {
                            "error": f"{type(exc).__name__}: {exc}",
                            "error_code": "E_UNCAUGHT",
                        }
                        err_code = "E_UNCAUGHT"
                        recovery = None
                    self._emit(
                        {
                            "kind": "tool_exec_error",
                            "call_id": tc.id,
                            "tool_name": tc.name,
                            "elapsed_seconds": elapsed,
                            **err_payload,
                        }
                    )
                    self._append_tool_result(
                        tc.id, {**err_payload, "tool_name": tc.name}
                    )
                    limit = (
                        _MAX_TRANSIENT_RETRIES
                        if recovery == RECOVERY_TRANSIENT_RETRY
                        else _MAX_CONSECUTIVE_TOOL_FAILURES
                    )
                    tripped, streak = self._note_tool_failure(
                        tool_fail_counts, tc.name, err_code, limit=limit
                    )
                    if tripped:
                        self._emit_tool_breaker(tc.name, streak)
                        return
                    continue

                elapsed = time.monotonic() - start_ts
                self.budget.commit(tc.name, actual_seconds=elapsed)
                # Successful dispatch — clear this tool's failure streak entirely.
                tool_fail_counts.pop(tc.name, None)

                # ---- success-blind doom-loop guard -------------------
                # Ported from opencode processor.ts (DOOM_LOOP_THRESHOLD=3): the
                # per-tool failure breaker above only trips on FAILURES, but a
                # turn can also get stuck re-issuing a call that keeps DISPATCHING
                # (succeeding, or returning a result the model ignores) with the
                # exact same arguments forever — pure echo, no progress. Record
                # each dispatched call as (tool_name, byte-identical raw args).
                # If the last _DOOM_LOOP_THRESHOLD dispatched calls are identical,
                # the turn is looping on itself — emit a structured turn_error and
                # stop. This is independent of the RESULT content (success-blind):
                # distinct args (real work) never trip it. Like opencode, a call
                # that did not actually dispatch (raised / gated / bad-JSON args)
                # is not recorded here — those stay owned by the failure breaker.
                recent_tool_calls.append((tc.name, tc.args))
                if self._is_doom_loop(recent_tool_calls):
                    self._emit_doom_loop(tc.name, _DOOM_LOOP_THRESHOLD)
                    return

                # Model-facing tool_result: strip thumbnail_path (file
                # path leakage), keep thumbnail_for_next_message=False
                # in the model copy too (the thumbnail itself is going
                # in a separate user message; the model doesn't need a
                # flag).
                model_result = {
                    k: v
                    for k, v in result.items()
                    if k not in {"thumbnail_path", "thumbnail_for_next_message"}
                }
                # SSE result also strips file paths; replaces with a
                # preview_uri pointing at the produced asset's on-disk
                # path so the frontend can render a preview.
                event_result = dict(model_result)
                produced_id = result.get("asset_id")
                if produced_id and self.registry.contains(str(produced_id)):
                    event_result["preview_uri"] = str(
                        self.registry.get(str(produced_id)).path
                    )

                self._emit(
                    {
                        "kind": "tool_exec_result",
                        "call_id": tc.id,
                        "tool_name": tc.name,
                        "result": event_result,
                        "elapsed_seconds": elapsed,
                    }
                )
                self._append_tool_result(tc.id, model_result)

                # Plan-B visual feedback. ONLY triggered by a
                # dispatcher-flagged result — there is no keyword
                # detection here. Today this is exclusively
                # analyze_media; the host does not auto-decide to show
                # thumbnails for any other tool.
                if result.get("thumbnail_for_next_message") and result.get(
                    "thumbnail_path"
                ):
                    visual_inspections_this_turn += 1
                    self._pending_thumbnails.append(Path(result["thumbnail_path"]))

            # After the dispatch sub-loop, inject queued thumbnails as
            # a multimodal user message before the next model call.
            if self._pending_thumbnails:
                self._messages.append(
                    {
                        "role": "user",
                        "content": _thumbnail_user_content(self._pending_thumbnails),
                    }
                )
                self._pending_thumbnails = []

            # Loop: call the model again with updated messages.

    # ── progress callback factory ────────────────────────────────────

    def _make_progress_cb(
        self, call_id: str, tool_name: str
    ) -> Callable[[ProgressUpdate], None]:
        emit = self._emit

        def cb(update: ProgressUpdate) -> None:
            event: dict[str, Any] = {
                "kind": "tool_exec_progress",
                "call_id": call_id,
                "tool_name": tool_name,
            }
            if update.percent is not None:
                event["percent"] = update.percent
            if update.message:
                event["message"] = update.message
            if update.eta_sec is not None:
                event["eta_seconds"] = update.eta_sec
            emit(event)

        return cb


__all__ = ["AgentLoopV3"]
