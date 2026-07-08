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
    or see→modify→rerun build loop legitimately needs many steps. If the
    SAME tool fails repeatedly with the SAME error class, the host feeds
    Gemini an explicit "change approach" nudge; it does not hard-stop the
    turn. A successful dispatch of that tool resets its streak. Real
    cost/time stay bounded by ``BudgetGuard`` ($ + execution seconds).
      * ``visual_inspections_this_turn``   — capped at
        ``max_visual_inspections`` (incremented ONLY when an
        ``analyze_media`` call actually produces a thumbnail for the
        next user message). Independent of repeated-failure nudges.

  - It implements Plan-B visual feedback: when a dispatcher returns
    ``thumbnail_for_next_message=True``, the loop appends a multimodal
    user message with the thumbnail image_url before the next model
    call. This path is ONLY triggered by a dispatcher-flagged result,
    which today only ``analyze_media`` produces. There is no keyword
    detection. Mid-turn, the host never decides to show the model a
    thumbnail because the user "seemed to want it"; the model has to ask
    via ``analyze_media`` explicitly. The single sanctioned exception is
    the pre-delivery gate below.

  - When the model emits no tool_calls and the stream ends, the loop
    runs a ONE-SHOT pre-delivery gate (per turn) before the honest stop:
    it injects a synthetic user message composed of (a) a visual
    self-check with 512px thumbnails of the visual assets this turn
    produced, (b) an explicit failure-disclosure list when tool calls
    failed this turn (including async jobs that came back
    status="failed"), and (c) the RC4 goal-completion check — then calls
    the model once more so it can revise or honestly disclose. The
    thumbnails are shown to the model exactly once: right after that
    call they are replaced in history with a text placeholder so base64
    payloads never ride the rolling window. After the gate (or when it
    is disabled), the loop emits ``turn_complete`` with the asset_ids
    produced during this turn and returns. It does not retry, does not
    "ask the user", does not synthesize a follow-up.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from gemia import memory as _memory
from gemia.budget_guard import BudgetGuard
from gemia.env_probe import format_environment_summary
from gemia.errors import GemiaError, RECOVERY_FIX_ARGS, RECOVERY_TRANSIENT_RETRY
from gemia.gemini_client import GeminiClientV3
from gemia.plan_mode import (
    PLAN_GATE_TURN_LIMIT,
    PLAN_MODE_PROMPT,
    is_plan_safe,
    plan_gate_message,
)
from gemia.project_store import ProjectHandle
from gemia.tools import DISPATCHER, TOOL_SCHEMAS, AssetRegistry, ToolContext
from gemia.tools._ask_bridge import AskBridge
from gemia.tools._context import ProgressUpdate
from gemia.transport.sse import REGISTRY as SSE_REGISTRY


_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "system_v3.md"
_ROLLING_USER_TURNS = 8

# RC4: one-shot completion-check gate before ending on no-tool-calls
COMPLETION_CHECK_ENABLED = True

# Pre-delivery gate extensions. Both ride the SAME one-shot RC4 gate round —
# they add sections to its injected message, so the worst case stays "one
# extra model call per turn":
#   * visual self-check — when this turn registered new visual assets, attach
#     512px thumbnails of the newest few so the model REVIEWS ITS OWN OUTPUT
#     before delivering (a default cube presented as a "house scene" is
#     exactly what this catches). Kill switch: LUMERI_V3_VISUAL_SELFCHECK=0.
#   * failure disclosure — when tool calls actually failed this turn, list
#     them and require the final reply to disclose the failures instead of
#     dressing a silent fallback up as success. Kill switch:
#     LUMERI_V3_FAILURE_DISCLOSURE=0.
VISUAL_SELFCHECK_ENABLED = os.environ.get(
    "LUMERI_V3_VISUAL_SELFCHECK", "1"
).lower() not in {"0", "false", "no", "off"}
FAILURE_DISCLOSURE_ENABLED = os.environ.get(
    "LUMERI_V3_FAILURE_DISCLOSURE", "1"
).lower() not in {"0", "false", "no", "off"}
# Newest visual assets attached at the gate. 512px thumbs keep the payload
# small; the cap keeps a batch-generating turn from flooding the context.
_SELFCHECK_MAX_THUMBS = 3
# Asset ids listed in the gate TEXT are capped separately — a batch turn can
# register dozens of assets and the prose must not balloon with them.
_SELFCHECK_MAX_LISTED = 8
_VISUAL_ASSET_KINDS = {"image", "video", "lottie"}


_GATE_VOICE_TEXT = (
    "表达方式：以上核对是你交付前的内部检查步骤，不是回复的模板。"
    "最终回复要像一个同事交活那样自然地说话，按这一回合实际发生的事"
    "来组织内容：做了什么、结果怎么样、有什么值得注意的地方；有失败就如实说明。"
    "不要按固定的标题或清单结构逐项汇报，不要把内部检查清单本身复述给用户，"
    "也不要用「项目已圆满完成」这类礼节性套话收尾。用用户最新消息的语言回复。"
)


def _goal_check_text(pinned_intent: str | None, plan_mode: bool) -> str:
    """The RC4 goal-check wording, shared by the gate builder and its
    degraded fallback so the two can never drift apart."""
    pinned_summary = (
        f"原始请求：{pinned_intent}" if pinned_intent else "原始请求：（未提供）"
    )
    if plan_mode:
        # In plan mode, presenting the plan IS the successful outcome —
        # telling the model to "keep calling tools" would only walk it into
        # plan_gate blocks.
        return (
            f"目标核对：{pinned_summary}。"
            "当前处于计划模式：若计划已完整，直接以文字呈现完整计划并请用户确认"
            "——呈现计划本身就是本回合的成功产出，不要调用会被计划模式拦截的修改类工具。"
            "若计划还缺关键信息，先用只读工具补齐再完成计划。"
            "若确实被卡住需要用户输入，明确说明。"
            "\n\n" + _GATE_VOICE_TEXT
        )
    return (
        f"目标核对：{pinned_summary}。"
        "若已完全完成，简短确认做了什么然后停下。"
        "若未完成，立刻继续调用下一个工具——不要等用户、不要重复已完成的工作。"
        "若确实被卡住需要用户输入，明确说明。"
        "\n\n" + _GATE_VOICE_TEXT
    )


def _strip_gate_images(msg: dict[str, Any]) -> None:
    """Replace the image parts of an already-consumed gate message with a
    compact text placeholder, IN PLACE.

    The model must see the self-check thumbnails exactly once (the gate
    round). Left as-is, the base64 payload would ride the rolling window
    into every subsequent model call — bandwidth and tokens spent re-sending
    stale pixels. Text sections are preserved verbatim."""
    content = msg.get("content")
    if not isinstance(content, list):
        return
    n_images = sum(1 for p in content if p.get("type") == "image_url")
    if n_images == 0:
        return
    texts = [p.get("text", "") for p in content if p.get("type") == "text"]
    texts.append(f"[{n_images} 张自检预览图已发送并从上下文回收]")
    msg["content"] = "\n\n".join(t for t in texts if t)

# Repeated-failure nudge: there is no cap on the TOTAL number of tool steps in a
# turn. If the SAME (tool, error-class) repeats this many times in a row, append
# a model-facing "change approach" prompt. This threshold is guidance for Gemini,
# not a host-side stop condition. A model that reads a typed error and ADAPTS —
# a different error_code, or switching tools — is treated as progress, not
# runaway: its streak soft-resets. A successful dispatch clears the streak
# entirely. Genuine cost/time stays bounded by BudgetGuard.
_REPEATED_FAILURE_NUDGE_THRESHOLD = 5
# Backward-compatible constant name for older imports/tests. It is no longer a
# host-side maximum.
_MAX_CONSECUTIVE_TOOL_FAILURES = _REPEATED_FAILURE_NUDGE_THRESHOLD
# Transient failures (recovery="transient_retry", e.g. a flaky network blip)
# get more headroom before the nudge appears, because re-issuing the identical
# call is the *correct* move for them — they are not a logic error the model
# needs to fix.
_TRANSIENT_RETRY_NUDGE_THRESHOLD = 8

# Success-BLIND doom-loop guard (ported from opencode processor.ts,
# DOOM_LOOP_THRESHOLD=3). The per-(tool, error_code) nudge above only tracks
# FAILURES. But a loop can also get stuck repeating a call that keeps
# "succeeding" — or whose result the model ignores — and re-issuing the exact
# same tool with byte-identical arguments forever. That is not progress, it is a
# stuck model echoing itself. Independent of success/failure: if the last
# ``_DOOM_LOOP_THRESHOLD`` tool calls in a turn are the SAME tool name with the
# SAME (byte-identical) raw args JSON, the turn is looping on itself — emit a
# structured turn_error and stop. Distinct args (real progress / different work)
# never trip it.
_DOOM_LOOP_THRESHOLD = 3

# Post-edit self-correction (ported from opencode pattern #2: append LSP
# diagnostics to a tool_result right after an edit). After a SUCCESSFUL
# *mutating* lumenframe verb, we append a compact POST-STATE digest (the
# resulting layer-tree summary + any lumenframe validate_doc warnings) to that
# tool's tool_result text the model reads next. This grounds the model in the
# new layer state at the exact moment it edits, so it self-corrects instead of
# editing blind. A "mutating" lumen verb is any tool whose name starts with
# "lumen_" EXCEPT the read-only ones below.
_LUMEN_TOOL_PREFIX = "lumen_"
_LUMEN_READONLY_TOOLS = frozenset({"lumen_get", "lumen_render"})


def _is_mutating_lumen_tool(name: str) -> bool:
    """True for a lumenframe verb that changes the layer document.

    Mutating == tool name starts with ``lumen_`` and is NOT one of the
    read-only verbs (``lumen_get``, ``lumen_render``). The read-only get verb
    is actually registered as ``get_lumenframe`` (no ``lumen_`` prefix) so it is
    excluded automatically; ``lumen_render`` is excluded explicitly because it
    only rasterises and does not edit the tree.
    """
    return name.startswith(_LUMEN_TOOL_PREFIX) and name not in _LUMEN_READONLY_TOOLS


# Tools whose children ALREADY settle their own seconds via BudgetGuard
# reservation/settlement (gemia/subtasks.py). Committing the batch wall-clock on
# top of that would double-count, so the loop commits 0.0 seconds for them and
# lets the children's settlements be the truth (docs/multi-agent-plan.md §5.3).
# The ~1 s orchestration overhead is covered by the tool's _TOOL_COSTS eta row.
_SELF_SETTLING_TOOLS = frozenset({"spawn_subtasks"})


def _commit_seconds(tool_name: str, elapsed: float) -> float:
    """Wall-elapsed seconds to commit for ``tool_name``. Zero for self-settling
    tools (their children already committed the real seconds)."""
    return 0.0 if tool_name in _SELF_SETTLING_TOOLS else elapsed


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
        # Plan mode: while True, only plan_mode.PLAN_ALLOWED_TOOLS dispatch;
        # everything else is gated (see the plan-mode block in _drive_turn).
        self.plan_mode: bool = False
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
        # The spawn_subtasks host verb needs a handle back to this loop (to share
        # the client / registry / project with its children and read plan_mode
        # live). Children strip ask_bridge from their own ctx.extra, so a child
        # cannot elicit; agent_loop is present in the PARENT ctx only for the
        # spawn dispatcher.
        _extra.setdefault("agent_loop", self)
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
            "max_turns": None,  # no fixed per-turn tool-step cap
            "ai_model": self.client.model,
            "created_at": now,
            "updated_at": now,
            "status": "running",
            "turn_count": int(turn_count),
            "loop_version": "v3",
            "plan_mode": bool(self.plan_mode),
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

    def set_plan_mode(self, enabled: bool) -> bool:
        """Toggle plan mode and broadcast the change. Returns the new state.

        Thread-safe like ``deliver_ask_answer``: a bool flip is atomic and the
        SSE registry accepts emits from any thread. The flag is read once per
        tool call in ``_drive_turn``, so a mid-turn toggle simply applies from
        the next tool call onward.
        """
        enabled = bool(enabled)
        if enabled != self.plan_mode:
            self.plan_mode = enabled
            self._emit({"kind": "plan_mode_changed", "enabled": enabled})
            if self.sessions_root is not None:
                self._write_session_meta(turn_count=self._turn_count)
        return self.plan_mode

    def add_external_asset(self, path: Path, *, summary: str = "") -> str:
        record = self.registry.add_external(Path(path), summary=summary or None)
        return record.asset_id

    # Cap for the injected layer-tree summary: a deep comp tree must not
    # balloon every prompt; past this the model should lumen_get for detail.
    _LUMENFRAME_PROMPT_CAP = 3500

    def _get_lumenframe_prompt_text(self) -> str:
        """Get lumenframe document summary for prompt injection.

        Reads the session's REAL document via ``layer.peek_lumendoc`` (in v3
        that is ``<project_dir>/lumenframe.json`` — ``ctx.project`` is always
        set, so the old ``_DOC_CACHE`` lookup never saw saved edits and the
        slot was permanently empty in real sessions). Read-only: never creates
        the file. Size-capped so a deep tree cannot balloon every prompt.
        """
        try:
            from gemia.tools import layer as _layer

            doc = _layer.peek_lumendoc(self._tool_ctx)
            if doc is None:
                return "(no lumenframe document in session yet)"
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
            text = "\n".join(lines) if lines else "(empty document)"
            if len(text) > self._LUMENFRAME_PROMPT_CAP:
                text = (
                    text[: self._LUMENFRAME_PROMPT_CAP]
                    + "\n… (layer tree truncated — use lumen_get for the full document)"
                )
            return text
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

        Like ``_get_lumenframe_prompt_text`` this reads the persisted document
        (``peek_lumendoc``), not the legacy in-memory cache.
        """
        try:
            from gemia.tools import layer as _layer
            from lumenframe import describe_ops

            doc = _layer.peek_lumendoc(self._tool_ctx)
            if doc is not None:
                root = doc.get("root", {})
                children = root.get("children", [])
                # Non-empty doc: inject full ops catalog
                if children:
                    return describe_ops()

            # Empty or missing doc: minimal pointer
            return "Layer editing available via lumen_* tools — call lumen_get to start."
        except (ImportError, Exception):
            return "(lumenframe operations not available)"

    def _memory_for_prompt(self) -> str:
        """Compact, size-capped durable-memory block for the ``{{memory}}`` slot.

        Delegates to ``gemia.memory.format_memory_for_prompt`` (which reads
        MEMORY.md + the model-profile digest, capped at a few KB) and never
        raises: any failure degrades to a short placeholder so prompt assembly
        cannot break on a missing or unreadable memory store.
        """
        try:
            return _memory.format_memory_for_prompt()
        except Exception:  # noqa: BLE001 — memory must never break prompt build
            return "(durable memory unavailable this session)"

    def _auto_log_turn(
        self,
        *,
        tools_succeeded: int,
        tools_failed: int,
        assets_produced: int,
    ) -> None:
        """Append ONE concise line to today's daily log at turn end.

        Records the user's ask (truncated) + what was done (tool / asset
        counts). Best-effort and non-fatal by contract: secret-looking content
        is dropped inside ``append_daily_entry`` and the whole thing is wrapped
        in try/except so a logging failure can never break the turn. Skips
        cleanly when there is no pinned intent (nothing to log)."""
        try:
            ask = (self._pinned_intent or "").strip()
            if not ask:
                return
            ask = " ".join(ask.split())
            if len(ask) > 140:
                ask = ask[:139].rstrip() + "…"

            done_bits: list[str] = []
            if tools_succeeded:
                done_bits.append(f"{tools_succeeded} tool call(s)")
            if assets_produced:
                done_bits.append(f"{assets_produced} asset(s)")
            if tools_failed:
                done_bits.append(f"{tools_failed} failure(s)")
            done = ", ".join(done_bits) if done_bits else "no tool calls"

            _memory.append_daily_entry(f"v3 turn — ask: {ask} | done: {done}")
        except Exception:  # noqa: BLE001 — logging must never break the turn
            pass

    def render_messages(self) -> list[dict[str, Any]]:
        """Build the messages list for the next model call.

        System prompt = ``system_v3.md`` with the placeholders filled in:
        ``{{environment}}`` from a live probe of the running interpreter and
        installed dependencies (gemia.env_probe.format_environment_summary),
        ``{{memory}}`` from the durable Gemia memory store
        (gemia.memory.format_memory_for_prompt — MEMORY.md + model defaults),
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
            .replace("{{plan_mode}}", PLAN_MODE_PROMPT if self.plan_mode else "")
            .replace("{{environment}}", format_environment_summary())
            .replace("{{memory}}", self._memory_for_prompt())
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
        if self.plan_mode:
            # Reinforcement only — the authoritative instructions live in the
            # system prompt's {{plan_mode}} slot. This line rides the recency
            # digest so the model is reminded right where it attends most.
            snaps.append(
                "Plan mode: ON — inspect and plan only; mutating tools are "
                "blocked until the user approves the plan."
            )
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
            "diverged. Narrate and reply in the USER's language (match their latest "
            "message) from the first line of the turn — no stock English openers, and "
            "vary your phrasing: never open every narration line with the same formula "
            "(e.g. 「我将…」/'I will …').]\n"
            + "\n".join(snaps)
        )

    def _append_tool_result(self, call_id: str, payload: Any) -> None:
        if isinstance(payload, str):
            content = payload
        else:
            content = json.dumps(payload, ensure_ascii=False, default=str)
        self._messages.append(
            {"role": "tool", "tool_call_id": call_id, "content": content}
        )

    def _lumen_post_state_digest(self) -> str:
        """Build a compact POST-STATE digest of the live lumenframe document.

        Reuses the compact tree-summary helper in ``gemia.tools.layer`` and runs
        lumenframe's ``validate_doc`` to surface any structural warnings. Returns
        a short text block (layer-tree summary + warning line) or ``""`` when
        there is no document / lumenframe is unavailable. This is the LSP-style
        feedback opencode appends after an edit, adapted to the layer tree.
        """
        from gemia.tools import layer as _layer

        # Resolve the current doc the same way the layer dispatchers do, so the
        # digest reflects exactly what the just-applied edit produced. Prefer the
        # project-backed doc; fall back to the session memory cache.
        doc: dict[str, Any] | None = None
        try:
            doc = _layer._lumendoc(self._tool_ctx)
        except Exception:
            cache = getattr(_layer, "_DOC_CACHE", {})
            doc = cache.get(self.session_id)
        if not isinstance(doc, dict):
            return ""

        root = doc.get("root", {})
        tree = (
            _layer._compact_tree_summary(root)
            if root
            else "(empty composition)"
        )
        selection = doc.get("selection", []) or []

        # Run lumenframe's own validator; it raises on any invariant violation.
        # A clean doc => "ok"; a violation => the structured message as a warning.
        try:
            from lumenframe import validate_doc as _validate_doc

            _validate_doc(doc)
            warnings = "none"
        except Exception as exc:  # LayerPatchError or anything validate raises
            code = getattr(exc, "code", None)
            msg = getattr(exc, "message", None) or str(exc)
            warnings = f"{code}: {msg}" if code else str(msg)

        lines = [
            "[POST-EDIT STATE — the layer document AFTER this edit. Verify it "
            "matches your intent before the next step.]",
            "Layer tree:",
            tree,
        ]
        if selection:
            lines.append(
                "Selection: " + ", ".join(str(s)[:12] for s in selection)
            )
        lines.append(f"Validate: {warnings}")
        return "\n".join(lines)

    def _append_lumen_post_state(self, call_id: str) -> None:
        """ADDITIVE post-edit feedback: append the POST-STATE digest to the
        tool_result the model just received for ``call_id``.

        Mirrors opencode pattern #2 (appending LSP diagnostics after an edit):
        right after a successful mutating lumen verb, fold the resulting
        layer-tree summary + validate warnings into that exact tool message's
        text so the model is grounded in the new state. Fully wrapped in
        try/except by the caller — must never break the loop.
        """
        digest = self._lumen_post_state_digest()
        if not digest:
            return
        # The success path appended this tool_result as the last message. Find
        # it by call_id (robust even if ordering ever changes) and append the
        # digest to its text content. Keep it additive: we never replace.
        for msg in reversed(self._messages):
            if (
                msg.get("role") == "tool"
                and msg.get("tool_call_id") == call_id
                and isinstance(msg.get("content"), str)
            ):
                existing = msg["content"]
                msg["content"] = (
                    f"{existing}\n\n{digest}" if existing else digest
                )
                return

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
        the streak resets to 1 rather than climbing toward the nudge threshold.
        Returns ``(should_nudge, streak)`` where ``should_nudge`` is True once
        ``streak`` reaches ``limit``. This never means "stop the turn"; it means
        "tell Gemini to change approach."
        """
        last_code, streak = fail_state.get(name, ("", 0))
        streak = streak + 1 if code == last_code else 1
        fail_state[name] = (code, streak)
        return streak >= limit, streak

    def _append_repeated_failure_nudge(self, name: str, code: str, count: int) -> None:
        self._messages.append(
            {
                "role": "user",
                "content": (
                    f"Repeated tool failure guidance: `{name}` has failed with "
                    f"`{code}` {count} times in a row in this turn. Do not call the "
                    "identical failing tool with the same arguments again. Read the "
                    "structured error, then change arguments, switch tools, inspect "
                    "state with a cheaper/read-only tool, or clearly explain the "
                    "blocker if no safe path remains."
                ),
            }
        )

    def _new_visual_records(self, pre_asset_ids: set[str]) -> list[Any]:
        """Visual assets (image/video/lottie) registered during this turn,
        in registry (oldest→newest) order."""
        return [
            r
            for r in self.registry.list_records()
            if r.asset_id not in pre_asset_ids and r.kind in _VISUAL_ASSET_KINDS
        ]

    async def _selfcheck_thumbnails(
        self, records: list[Any]
    ) -> list[tuple[Any, Path]]:
        """Best-effort 512px thumbnails for the pre-delivery visual self-check,
        as ``(record, thumbnail_path)`` pairs so the gate can label each image
        with its asset id.

        Reuses analyze_media's ffmpeg thumbnailer, off-thread like
        analyze_media itself (ffmpeg/ffprobe must not block this session's
        event loop). Videos get a mid-point frame — the first frame is often a
        black fade-in and would read as a false "empty frame" defect. Any
        per-asset failure is skipped — the gate degrades to a text-only review
        rather than breaking the turn.
        """
        from gemia.tools._ffmpeg import ffprobe_duration
        from gemia.tools.analyze_media import _make_thumbnail

        loop = asyncio.get_running_loop()
        thumb_dir = self.output_dir / "thumbnails"
        thumb_dir.mkdir(parents=True, exist_ok=True)
        pairs: list[tuple[Any, Path]] = []
        for record in records[-_SELFCHECK_MAX_THUMBS:]:
            dst = thumb_dir / f"selfcheck_{record.asset_id}.png"
            duration = 0.0
            if record.kind == "video":
                try:
                    duration = float(
                        await loop.run_in_executor(
                            None, ffprobe_duration, record.path
                        )
                    )
                except Exception:  # noqa: BLE001 — fall back to frame 0
                    duration = 0.0
            try:
                await loop.run_in_executor(
                    None, _make_thumbnail, record.kind, record.path, dst, duration
                )
            except Exception:  # noqa: BLE001 — degrade, never break the gate
                continue
            if dst.exists():
                pairs.append((record, dst))
        return pairs

    async def _build_predelivery_gate(
        self,
        *,
        pre_asset_ids: set[str],
        failed_call_log: list[tuple[str, str]],
    ) -> tuple[Any, list[str]]:
        """Compose the one-shot pre-delivery gate message (extended RC4).

        Returns ``(content, sections)``: ``content`` is a plain string, or a
        multimodal parts list when self-check thumbnails are attached;
        ``sections`` names the blocks included (surfaced on the
        ``completion_check`` event for observability and tests).
        """
        sections: list[str] = []
        blocks: list[str] = []
        shown: list[tuple[Any, Path]] = []

        visual_records = (
            self._new_visual_records(pre_asset_ids)
            if VISUAL_SELFCHECK_ENABLED
            else []
        )
        if visual_records:
            sections.append("visual_selfcheck")
            # provider=claude cannot take OpenAI-style image_url parts (same
            # limitation as Plan-B thumbnails); the gate stays text-only there.
            if getattr(self.client, "provider", "") != "claude":
                shown = await self._selfcheck_thumbnails(visual_records)
            ids = [f"{r.asset_id}（{r.kind}）" for r in visual_records]
            listed = "、".join(ids[:_SELFCHECK_MAX_LISTED])
            if len(ids) > _SELFCHECK_MAX_LISTED:
                listed += f" 等共 {len(ids)} 个"
            coverage = (
                f"（下方附了其中 {len(shown)}/{len(visual_records)} 张预览，"
                "每张图前标注了对应的 asset id；未附预览的资产用 analyze_media 自查。）"
                if shown
                else "（本次没有附上预览图，请用 analyze_media 逐个自查后再收尾。）"
            )
            blocks.append(
                f"视觉自检：本回合新产出了视觉资产 {listed}。"
                "交付前先审视这些产出：内容是不是用户要的东西？"
                "有没有明显缺陷——空画面、默认物体、构图失衡、穿帮、与描述不符？"
                "发现问题就立刻调用工具修复后再交付；确认合格才可以收尾。"
                + coverage
            )

        if FAILURE_DISCLOSURE_ENABLED and failed_call_log:
            sections.append("failure_disclosure")
            tally: dict[tuple[str, str], int] = {}
            for name, code in failed_call_log:
                tally[(name, code)] = tally.get((name, code), 0) + 1
            listed = "、".join(
                f"`{name}`({code})×{n}" for (name, code), n in tally.items()
            )
            blocks.append(
                f"失败披露：本回合有工具调用失败过：{listed}。"
                "你的最终回复必须如实说明这些失败及其对结果的影响。"
                "如果你换了替代方案，必须明说原方案失败了、现在交付的是替代产物——"
                "禁止把失败包装成成功。若失败已被后续的成功修复，简要说明即可。"
            )

        sections.append("goal_check")
        blocks.append(_goal_check_text(self._pinned_intent, self.plan_mode))

        text = "\n\n".join(blocks)
        if not shown:
            return text, sections

        parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for record, p in shown:
            # Label BEFORE each image so the model can attribute defects to
            # the right asset even when some thumbnails were skipped.
            parts.append(
                {
                    "type": "text",
                    "text": f"预览 {record.asset_id}（{record.kind}）：",
                }
            )
            data = Path(p).read_bytes()
            b64 = base64.b64encode(data).decode("ascii")
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                }
            )
        return parts, sections

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
    def _synthesize_wrapup_message(
        reason: str,
        *,
        tools_succeeded: int,
        tools_failed: int,
        assets_produced: int,
        tool_name: str | None = None,
    ) -> str:
        """Build a short 'stopped because X; here's what was / wasn't done'
        summary LOCALLY from the known stop reason and the turn's tool / asset
        counts. No model API call — this is a cheap deterministic synthesis so a
        budget / doom-loop / stream-error stop is *explained*
        to the user instead of being a bare silent halt.

        ``reason`` is a short machine code (e.g. ``"doom_loop"``,
        ``"budget_exhausted"``, ``"stream_error"``); the rest
        is synthesized from the turn state that is already on hand at the exit
        point."""
        why = {
            "doom_loop": (
                f"tool '{tool_name}' was called repeatedly with identical "
                f"arguments (a doom loop) and was stopped"
                if tool_name
                else "the same call was repeated with identical arguments "
                "(a doom loop) and was stopped"
            ),
            "budget_exhausted": "the per-turn budget (cost / time) was exhausted",
            "stream_error": "the model stream errored out",
            "plan_gate_limit": (
                "plan mode kept blocking mutating tool calls; the plan should "
                "be presented as text and approved before execution"
            ),
        }.get(reason, f"the turn stopped ({reason})")

        done_bits: list[str] = []
        if assets_produced:
            done_bits.append(
                f"{assets_produced} asset" + ("s" if assets_produced != 1 else "")
            )
        if tools_succeeded:
            done_bits.append(
                f"{tools_succeeded} tool call"
                + ("s" if tools_succeeded != 1 else "")
                + " succeeded"
            )
        done = ", ".join(done_bits) if done_bits else "nothing was completed"

        not_done = (
            f"{tools_failed} tool call"
            + ("s" if tools_failed != 1 else "")
            + " failed"
            if tools_failed
            else "no failures were recorded before the stop"
        )

        return (
            f"Stopped because {why}. "
            f"What was done: {done}. "
            f"What wasn't: {not_done}. "
            "Ask me to continue or adjust the approach if you'd like more."
        )

    def _emit_turn_wrapup(
        self,
        reason: str,
        *,
        tools_succeeded: int,
        tools_failed: int,
        assets_produced: int,
        tool_name: str | None = None,
    ) -> None:
        """ADDITIVE graceful wrap-up (ported from opencode pattern #5): at a
        non-success exit point (budget exhaustion, doom loop, stream error)
        emit a short assistant-facing ``turn_wrapup`` event that explains the
        stop, *in addition to* the existing turn_error
        event — so the user gets a 'stopped because X; here's what was / wasn't
        done' summary instead of a bare halt.

        Cheap and non-fatal by contract: the message is synthesized LOCALLY
        (no extra model call) and the whole thing is wrapped in try/except so a
        failure here can never break the loop. ``WHEN`` the turn stops is
        unchanged — this only ADDS the explanatory emission."""
        try:
            message = self._synthesize_wrapup_message(
                reason,
                tools_succeeded=tools_succeeded,
                tools_failed=tools_failed,
                assets_produced=assets_produced,
                tool_name=tool_name,
            )
            self._emit(
                {
                    "kind": "turn_wrapup",
                    "reason": reason,
                    "message": message,
                    "tools_succeeded": tools_succeeded,
                    "tools_failed": tools_failed,
                    "assets_produced": assets_produced,
                    **({"tool_name": tool_name} if tool_name else {}),
                }
            )
        except Exception:  # noqa: BLE001 — wrap-up must never break the turn
            pass

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
        and ``tool_fail_counts`` drives repeated-failure nudges. Genuine
        cost/time stay bounded by BudgetGuard.
        """
        pre_asset_ids = {r.asset_id for r in self.registry.list_records()}
        visual_inspections_this_turn = 0
        # Blocked-by-plan-mode calls this turn. Gated calls never reach the
        # doom-loop history (they don't dispatch), so this counter is the
        # host-side stop for a model that keeps hammering blocked tools.
        plan_gates_this_turn = 0
        # name → (last_failure_code, consecutive_streak) for repeated-failure nudges.
        tool_fail_counts: dict[str, tuple[str, int]] = {}
        # Rolling history of (tool_name, raw-args-JSON) for THIS turn, used by the
        # success-blind doom-loop guard. We only need the last few entries, but a
        # plain list is simplest; it stays tiny because the doom-loop guard still
        # stops byte-identical successful repeats.
        recent_tool_calls: list[tuple[str, str]] = []
        # Running tallies for the graceful wrap-up summary at non-success exits
        # (opencode pattern #5). Cheap counters, no extra model call: a
        # dispatch that returned is a success; any error / gate / parse-fail is
        # a failure. Asset count is computed from the registry at exit time.
        tools_succeeded = 0
        tools_failed = 0
        # (tool_name, error_code) for every REAL execution failure this turn —
        # bad-JSON args or a dispatcher exception. Gates (plan/budget/visual
        # cap) are excluded: they have their own user-visible semantics and are
        # not "the model's approach failed". Drives the failure-disclosure
        # section of the pre-delivery gate.
        failed_call_log: list[tuple[str, str]] = []

        def _assets_produced() -> int:
            return sum(
                1
                for r in self.registry.list_records()
                if r.asset_id not in pre_asset_ids
            )

        self._emit({"kind": "turn_start"})

        # RC4: per-turn one-shot completion check guard (not per-loop iteration).
        completion_check_done = False
        # Gate message whose thumbnails still need reclaiming after the model
        # has seen them once (see _strip_gate_images).
        gate_msg_pending_strip: dict[str, Any] | None = None
        # Async jobs whose failure has already been logged for disclosure —
        # a failed job polled repeatedly must be disclosed once, not N times.
        failed_job_ids: set[str] = set()

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
                    self._emit_turn_wrapup(
                        "stream_error",
                        tools_succeeded=tools_succeeded,
                        tools_failed=tools_failed,
                        assets_produced=_assets_produced(),
                    )
                    return

            # Reclaim gate thumbnails now that the model has seen them once —
            # the base64 payload must not ride the rolling window into every
            # subsequent model call (bandwidth + tokens on stale pixels).
            if gate_msg_pending_strip is not None:
                _strip_gate_images(gate_msg_pending_strip)
                gate_msg_pending_strip = None

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

            # ---- model called no tools → pre-delivery gate (RC4+) ----
            if not accum.tool_calls:
                if COMPLETION_CHECK_ENABLED and not completion_check_done:
                    # One-shot gate before honest stop. Extends the RC4
                    # completion check with (a) a visual self-check with
                    # thumbnails when this turn produced visual assets and
                    # (b) explicit failure disclosure when tool calls failed.
                    # Gate composition is best-effort: any failure degrades to
                    # the plain RC4 wording — it must never kill the turn.
                    try:
                        gate_content, gate_sections = (
                            await self._build_predelivery_gate(
                                pre_asset_ids=pre_asset_ids,
                                failed_call_log=failed_call_log,
                            )
                        )
                    except Exception:  # noqa: BLE001 — never break the turn
                        gate_content = _goal_check_text(
                            self._pinned_intent, self.plan_mode
                        )
                        gate_sections = ["goal_check"]
                    gate_msg = {"role": "user", "content": gate_content}
                    self._messages.append(gate_msg)
                    if isinstance(gate_content, list):
                        gate_msg_pending_strip = gate_msg
                    completion_check_done = True
                    self._emit(
                        {"kind": "completion_check", "sections": gate_sections}
                    )
                    continue
                else:
                    # Already did one-shot check or gate is disabled → honest stop.
                    final_ids = [
                        r.asset_id
                        for r in self.registry.list_records()
                        if r.asset_id not in pre_asset_ids
                    ]
                    # Auto daily-log ONE concise line for this turn (the user's
                    # ask + what was done). Best-effort: a logging failure must
                    # NOT break the turn, so it is fully wrapped.
                    self._auto_log_turn(
                        tools_succeeded=tools_succeeded,
                        tools_failed=tools_failed,
                        assets_produced=len(final_ids),
                    )
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
                    tools_failed += 1
                    failed_call_log.append((tc.name, "E_BAD_ARG"))
                    should_nudge, streak = self._note_tool_failure(
                        tool_fail_counts, tc.name, "E_BAD_ARG",
                        limit=_REPEATED_FAILURE_NUDGE_THRESHOLD,
                    )
                    if should_nudge:
                        self._append_repeated_failure_nudge(tc.name, "E_BAD_ARG", streak)
                    continue

                # Plan-mode gate: while ON, only read-only inspection tools
                # dispatch. Same emit/append/continue shape as the budget gate
                # below, with its own event kind so both frontends can render
                # it distinctly. Checked BEFORE the budget gate: a blocked tool
                # is blocked no matter how affordable it is.
                if self.plan_mode and not is_plan_safe(tc.name):
                    plan_gates_this_turn += 1
                    gate_msg = plan_gate_message(tc.name)
                    self._emit(
                        {
                            "kind": "plan_gate",
                            "call_id": tc.id,
                            "tool_name": tc.name,
                            "message": gate_msg,
                        }
                    )
                    self._append_tool_result(
                        tc.id,
                        {
                            "blocked_by_plan_mode": True,
                            "error_code": "E_PLAN_MODE",
                            "message": gate_msg,
                        },
                    )
                    tools_failed += 1
                    if plan_gates_this_turn >= PLAN_GATE_TURN_LIMIT:
                        # Hard stop: gated calls cost nothing, so neither
                        # BudgetGuard nor the doom-loop guard would ever end
                        # a turn that spins on blocked tools.
                        self._emit(
                            {
                                "kind": "turn_error",
                                "reason": "plan_gate_limit",
                                "tool_name": tc.name,
                                "error": (
                                    f"plan mode blocked {plan_gates_this_turn} tool "
                                    "calls this turn; stopping. Present the plan as "
                                    "text instead of calling mutating tools."
                                ),
                            }
                        )
                        self._emit_turn_wrapup(
                            "plan_gate_limit",
                            tools_succeeded=tools_succeeded,
                            tools_failed=tools_failed,
                            assets_produced=_assets_produced(),
                            tool_name=tc.name,
                        )
                        return
                    should_nudge, streak = self._note_tool_failure(
                        tool_fail_counts, tc.name, "E_PLAN_MODE",
                        limit=_REPEATED_FAILURE_NUDGE_THRESHOLD,
                    )
                    if should_nudge:
                        self._append_repeated_failure_nudge(
                            tc.name, "E_PLAN_MODE", streak
                        )
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
                    tools_failed += 1
                    should_nudge, streak = self._note_tool_failure(
                        tool_fail_counts, tc.name, "E_BUDGET",
                        limit=_REPEATED_FAILURE_NUDGE_THRESHOLD,
                    )
                    if should_nudge:
                        self._append_repeated_failure_nudge(tc.name, "E_BUDGET", streak)
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
                    tools_failed += 1
                    should_nudge, streak = self._note_tool_failure(
                        tool_fail_counts, tc.name, "E_VISUAL_CAP",
                        limit=_REPEATED_FAILURE_NUDGE_THRESHOLD,
                    )
                    if should_nudge:
                        self._append_repeated_failure_nudge(tc.name, "E_VISUAL_CAP", streak)
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
                # The spawn_subtasks dispatcher anchors its children's SSE events
                # (subagent_start/result + child tool_exec_*) to THIS call's id.
                self._tool_ctx.extra["call_id"] = tc.id

                start_ts = time.monotonic()
                try:
                    result = await DISPATCHER[tc.name](parsed_args, self._tool_ctx)
                except Exception as exc:
                    elapsed = time.monotonic() - start_ts
                    self.budget.commit(
                        tc.name, actual_seconds=_commit_seconds(tc.name, elapsed)
                    )
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
                    tools_failed += 1
                    failed_call_log.append((tc.name, err_code))
                    limit = (
                        _TRANSIENT_RETRY_NUDGE_THRESHOLD
                        if recovery == RECOVERY_TRANSIENT_RETRY
                        else _REPEATED_FAILURE_NUDGE_THRESHOLD
                    )
                    should_nudge, streak = self._note_tool_failure(
                        tool_fail_counts, tc.name, err_code, limit=limit
                    )
                    if should_nudge:
                        self._append_repeated_failure_nudge(tc.name, err_code, streak)
                    continue

                elapsed = time.monotonic() - start_ts
                self.budget.commit(
                    tc.name, actual_seconds=_commit_seconds(tc.name, elapsed)
                )
                # Successful dispatch — clear this tool's failure streak entirely.
                tool_fail_counts.pop(tc.name, None)
                tools_succeeded += 1

                # Async-job failures never raise: generate_video/build submit a
                # job and the FAILURE surfaces later as a perfectly normal
                # check_job/wait_for_job result with status="failed". That is
                # the single most likely thing a model quietly papers over with
                # a fallback — log it for the disclosure gate (once per job,
                # however many times it gets polled).
                if (
                    tc.name in {"check_job", "wait_for_job"}
                    and isinstance(result, dict)
                    and result.get("status") == "failed"
                ):
                    job_id = str(result.get("job_id") or "unknown")
                    if job_id not in failed_job_ids:
                        failed_job_ids.add(job_id)
                        failed_call_log.append((f"job:{job_id}", "E_JOB_FAILED"))

                # ---- success-blind doom-loop guard -------------------
                # Ported from opencode processor.ts (DOOM_LOOP_THRESHOLD=3): the
                # per-tool repeated-failure nudge above only tracks FAILURES, but a
                # turn can also get stuck re-issuing a call that keeps DISPATCHING
                # (succeeding, or returning a result the model ignores) with the
                # exact same arguments forever — pure echo, no progress. Record
                # each dispatched call as (tool_name, byte-identical raw args).
                # If the last _DOOM_LOOP_THRESHOLD dispatched calls are identical,
                # the turn is looping on itself — emit a structured turn_error and
                # stop. This is independent of the RESULT content (success-blind):
                # distinct args (real work) never trip it. Like opencode, a call
                # that did not actually dispatch (raised / gated / bad-JSON args)
                # is not recorded here — those stay owned by repeated-failure nudges.
                recent_tool_calls.append((tc.name, tc.args))
                if self._is_doom_loop(recent_tool_calls):
                    self._emit_doom_loop(tc.name, _DOOM_LOOP_THRESHOLD)
                    self._emit_turn_wrapup(
                        "doom_loop",
                        tools_succeeded=tools_succeeded,
                        tools_failed=tools_failed,
                        assets_produced=_assets_produced(),
                        tool_name=tc.name,
                    )
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

                # ---- post-edit self-correction (opencode pattern #2) ----
                # After a SUCCESSFUL *mutating* lumen verb, append a compact
                # POST-STATE digest (layer-tree summary + validate_doc
                # warnings) to the tool_result the model just received, so it is
                # grounded in the new layer state right where it edited —
                # mirroring opencode appending LSP diagnostics after edits.
                # ADDITIVE, cheap, and non-fatal: any failure here must never
                # break the loop, so the whole thing is wrapped in try/except.
                if _is_mutating_lumen_tool(tc.name):
                    try:
                        self._append_lumen_post_state(tc.id)
                    except Exception:  # noqa: BLE001 — never break the turn
                        pass

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
