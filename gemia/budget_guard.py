"""Budget guard for the Lumeri v3 agent loop.

The only host-side gate. Tracks per-session cumulative cost and elapsed
time; returns either ``ok`` or a fixed-limit block. The model chooses an
in-budget alternative or reports the blocker — the host does not auto-pick a
cheaper tool and an approval cannot raise the cap.

There is no capability gate, no stability gate, no approval stub. The
model holds the wheel; the host only reports real money and real time.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


# Verified public API prices, USD per 1M tokens.  Keep this table deliberately
# narrow: an unknown model remains metered-but-unpriced instead of inheriting a
# plausible-looking rate from a similarly named model.  GPT-5.6 prices were
# verified against the official OpenAI model pages on 2026-07-21.
_MODEL_TOKEN_PRICES: dict[str, dict[str, float]] = {
    "gpt-5.6": {"input": 5.00, "cached_input": 0.50, "output": 30.00},
    "gpt-5.6-sol": {"input": 5.00, "cached_input": 0.50, "output": 30.00},
    "gpt-5.6-terra": {"input": 2.50, "cached_input": 0.25, "output": 15.00},
    "gpt-5.6-luna": {"input": 1.00, "cached_input": 0.10, "output": 6.00},
}


_TOOL_COSTS: dict[str, dict[str, float]] = {
    # Provider-backed (real money). Numbers verified in doc 07 from
    # Google's public pricing pages (2026-05-30 snapshot).
    "generate_image":     {"usd": 0.101, "eta_sec": 10.0},   # Nano Banana 2, 2K
    "generate_video":     {"usd": 2.80,  "eta_sec": 120.0},  # Veo 3.1 fast, 8s
    "generate_audio":     {"usd": 0.00,  "eta_sec": 45.0},   # Lyria 3 clip — preview, currently free
    "analyze_media":      {"usd": 0.01,  "eta_sec": 4.0},    # rough Gemini text estimate
    # Audio analysis (zero money, time only).
    "align_audio":        {"usd": 0.00, "eta_sec": 2.0},    # cross-correlation
    "detect_beats":       {"usd": 0.00, "eta_sec": 2.0},    # librosa beat detection
    # Local ffmpeg (zero money, time only).
    "edit_image":         {"usd": 0.00, "eta_sec": 4.0},
    "edit_video":         {"usd": 0.00, "eta_sec": 10.0},
    "composite":          {"usd": 0.00, "eta_sec": 8.0},
    "color_grade":        {"usd": 0.00, "eta_sec": 8.0},
    "adjust_media":       {"usd": 0.00, "eta_sec": 6.0},
    "paint_overlay":      {"usd": 0.00, "eta_sec": 1.0},
    "paint_mask_effect":  {"usd": 0.00, "eta_sec": 12.0},
    "add_overlay":        {"usd": 0.00, "eta_sec": 6.0},
    "arrange_timeline":   {"usd": 0.00, "eta_sec": 12.0},
    "mix_audio":          {"usd": 0.00, "eta_sec": 6.0},
    "edit_audio":         {"usd": 0.00, "eta_sec": 4.0},
    "transform_geometry": {"usd": 0.00, "eta_sec": 5.0},
    "smart_reframe":      {"usd": 0.00, "eta_sec": 8.0},
    "extract_frame":      {"usd": 0.00, "eta_sec": 1.0},
    "probe_media":        {"usd": 0.00, "eta_sec": 0.2},
    "inspect_lottie":     {"usd": 0.00, "eta_sec": 1.0},
    "search_library":     {"usd": 0.00, "eta_sec": 0.5},
    "search_media":       {"usd": 0.00, "eta_sec": 0.5},
    "search_frames":      {"usd": 0.00, "eta_sec": 3.0},
    "draft_shotlist":     {"usd": 0.00, "eta_sec": 0.3},
    "set_shotlist":       {"usd": 0.00, "eta_sec": 0.2},
    "update_shot":        {"usd": 0.00, "eta_sec": 0.2},
    "get_shotlist":       {"usd": 0.00, "eta_sec": 0.1},
    "draft_quanta":         {"usd": 0.00, "eta_sec": 0.3},
    "set_quanta":           {"usd": 0.00, "eta_sec": 0.2},
    "update_quantum":       {"usd": 0.00, "eta_sec": 0.2},
    "refine_quantum":       {"usd": 0.00, "eta_sec": 5.0},
    "get_quanta":           {"usd": 0.00, "eta_sec": 0.1},
    "refine_shot":        {"usd": 0.00, "eta_sec": 3.0},
    "assemble_shotlist":  {"usd": 0.00, "eta_sec": 8.0},
    "assemble_quanta":      {"usd": 0.00, "eta_sec": 12.0},
    "narrate":            {"usd": 0.00, "eta_sec": 4.0},
    "subtitle":           {"usd": 0.00, "eta_sec": 4.0},
    "animate_captions":   {"usd": 0.00, "eta_sec": 5.0},
    "annotate_media":     {"usd": 0.00, "eta_sec": 8.0},
    "prepare_roughcut":   {"usd": 0.00, "eta_sec": 30.0},
    "get_media_annotations": {"usd": 0.00, "eta_sec": 0.2},
    "write_media_annotation": {"usd": 0.00, "eta_sec": 0.2},
    "export":             {"usd": 0.00, "eta_sec": 20.0},
    # M3 verbs (host-side internet + sandbox bash).
    "web_search":         {"usd": 0.00, "eta_sec": 3.0},     # Host-side search, compact results
    "web_open":           {"usd": 0.00, "eta_sec": 5.0},     # Host-side page text extraction
    "fetch":              {"usd": 0.00, "eta_sec": 5.0},     # Host-side https download
    "run_shell":          {"usd": 0.00, "eta_sec": 10.0},    # Sandboxed bash execution
    "file_list":          {"usd": 0.00, "eta_sec": 0.2},     # Host-side file inspection
    "file_read":          {"usd": 0.00, "eta_sec": 0.2},
    "file_write":         {"usd": 0.00, "eta_sec": 0.2},
    "file_copy":          {"usd": 0.00, "eta_sec": 0.5},
    "file_move":          {"usd": 0.00, "eta_sec": 0.5},
    "file_delete":        {"usd": 0.00, "eta_sec": 0.2},
    # M4 verbs (async code execution + job polling + skill persistence).
    "build":              {"usd": 0.00, "eta_sec": 15.0},    # Async Python sandbox submit
    "check_job":          {"usd": 0.00, "eta_sec": 0.5},     # Job status poll
    "wait_for_job":       {"usd": 0.00, "eta_sec": 30.0},    # Blocking job wait
    "kill_job":           {"usd": 0.00, "eta_sec": 0.5},     # Kill a running background job
    "save_skill":         {"usd": 0.00, "eta_sec": 0.5},     # Skill file copy + metadata
    # Timeline v1 verbs (document mutation = pure in-process patch; near-free).
    "get_timeline":             {"usd": 0.00, "eta_sec": 0.2},
    "timeline_insert_clip":     {"usd": 0.00, "eta_sec": 0.5},
    "timeline_delete_clip":     {"usd": 0.00, "eta_sec": 0.2},
    "timeline_move_clip":       {"usd": 0.00, "eta_sec": 0.2},
    "timeline_trim_clip":       {"usd": 0.00, "eta_sec": 0.2},
    "timeline_split_clip":      {"usd": 0.00, "eta_sec": 0.2},
    "timeline_set_clip_time":   {"usd": 0.00, "eta_sec": 0.2},
    "timeline_add_transition":  {"usd": 0.00, "eta_sec": 0.2},
    "timeline_set_clip_effects": {"usd": 0.00, "eta_sec": 0.2},
    "timeline_add_track":       {"usd": 0.00, "eta_sec": 0.2},
    "timeline_set_track":       {"usd": 0.00, "eta_sec": 0.2},
    "timeline_undo":            {"usd": 0.00, "eta_sec": 0.2},
    "inspect_timeline":         {"usd": 0.00, "eta_sec": 12.0},  # render proxy + sampled frames
    "get_safe_areas":           {"usd": 0.00, "eta_sec": 0.1},
    "render_preview":           {"usd": 0.00, "eta_sec": 20.0},  # ffmpeg low-res proxy
    "project_export":           {"usd": 0.00, "eta_sec": 60.0},  # full-quality multi-track export
    # Lumenframe layer/time verbs (pure document patch unless rendering).
    "get_lumenframe":           {"usd": 0.00, "eta_sec": 0.2},
    "lumen_patch":              {"usd": 0.00, "eta_sec": 0.2},
    "lumen_add_layer":          {"usd": 0.00, "eta_sec": 0.2},
    "lumen_set_transform":      {"usd": 0.00, "eta_sec": 0.2},
    "lumen_set_opacity":        {"usd": 0.00, "eta_sec": 0.2},
    "lumen_delete_layer":       {"usd": 0.00, "eta_sec": 0.2},
    "lumen_move_layer":         {"usd": 0.00, "eta_sec": 0.2},
    "lumen_set_visibility":     {"usd": 0.00, "eta_sec": 0.2},
    "lumen_select":             {"usd": 0.00, "eta_sec": 0.2},
    "lumen_set_mask":           {"usd": 0.00, "eta_sec": 0.2},
    "lumen_key":                {"usd": 0.00, "eta_sec": 0.2},
    "lumen_set_range":          {"usd": 0.00, "eta_sec": 0.2},
    "lumen_set_lane":           {"usd": 0.00, "eta_sec": 0.2},
    "lumen_retime_segment":     {"usd": 0.00, "eta_sec": 0.2},
    "lumen_reverse":            {"usd": 0.00, "eta_sec": 0.2},
    "lumen_time_remap":         {"usd": 0.00, "eta_sec": 0.2},
    "lumen_speed_ramp":         {"usd": 0.00, "eta_sec": 0.2},
    "lumen_ripple_delete":      {"usd": 0.00, "eta_sec": 0.2},
    "lumen_merge_compositions": {"usd": 0.00, "eta_sec": 0.2},
    "lumen_set_work_area":      {"usd": 0.00, "eta_sec": 0.2},
    "lumen_render":             {"usd": 0.00, "eta_sec": 20.0},
    "lumen_seek":               {"usd": 0.00, "eta_sec": 1.0},
    # Renders each layer's alpha once, so it scales with layer count rather
    # than being free; still local compute, no provider spend.
    "lumen_stage":              {"usd": 0.00, "eta_sec": 2.0},
    "lumen_render_range":       {"usd": 0.00, "eta_sec": 5.0},
    "lumen_comp_to_timeline":   {"usd": 0.00, "eta_sec": 10.0},
    # Vector motion design: create/adjust only compile SVG + patch the doc —
    # the actual html→mp4 render cost is paid later by lumen_render/seek.
    "vector_motion":            {"usd": 0.00, "eta_sec": 1.0},
    # Creative point libraries: create/adjust only compute a deterministic
    # recipe/scene/plan (+ kinetic_type patches the doc); render cost is paid
    # later by lumen_render/seek. catalog is free vocabulary.
    "grade":                    {"usd": 0.00, "eta_sec": 0.3},
    "kinetic_type":             {"usd": 0.00, "eta_sec": 1.0},
    "edit_grammar":             {"usd": 0.00, "eta_sec": 0.3},
    "camera":                   {"usd": 0.00, "eta_sec": 0.3},
    "compose":                  {"usd": 0.00, "eta_sec": 0.3},
    "rhythm_edit":              {"usd": 0.00, "eta_sec": 0.3},
    # Multi-agent fan-out: the verb itself is near-free orchestration; the real
    # cost of children flows through per-child budget reservations (see
    # gemia/subtasks.py + docs/multi-agent-plan.md §5). The loop special-cases
    # spawn_subtasks to commit actual_seconds=0.0 so the batch wall-clock is not
    # double-counted on top of the children's settled seconds (§5.3).
    "spawn_subtasks":           {"usd": 0.00, "eta_sec": 1.0},
}


@dataclass
class BudgetDecision:
    ok: bool
    estimated_cost_usd: float
    estimated_eta_sec: float
    reason: str = ""
    alternatives: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "estimated_cost_usd": self.estimated_cost_usd,
            "estimated_eta_sec": self.estimated_eta_sec,
            "reason": self.reason,
            "alternatives": list(self.alternatives),
        }


@dataclass(frozen=True)
class BudgetReservation:
    """A budget slice claimed up front by ``reserve()`` / ``reserve_amount()``.

    Salvaged from the stranded RC3 parallel-dispatch branch
    (``d7f941c:gemia/budget_guard.py:84``). ``tool_name`` is a free-form label
    for snapshots/logs (it is a real tool name for ``reserve()``, and a label
    like ``"spawn_subtasks:sub_1"`` for ``reserve_amount()``); the settlement
    math in ``commit_reserved`` never looks at it.
    """

    tool_name: str
    estimated_cost_usd: float
    estimated_eta_sec: float


class BudgetGuard:
    """Per-session cumulative cost + time tracker."""

    def __init__(
        self,
        *,
        max_usd: float = 5.0,
        max_seconds: float = 600.0,
        warning_usd: float | None = None,
    ) -> None:
        self.max_usd = float(max_usd)
        # ``None`` is the public "no warning" state.  Internally zero keeps the
        # hot-path comparisons numeric while snapshots expose it as JSON null.
        self.warning_usd = float(0.0 if warning_usd is None else warning_usd)
        self.max_seconds = float(max_seconds)
        self.spent_usd = 0.0
        self.tool_spent_usd = 0.0
        self.model_spent_usd = 0.0
        self.spent_seconds = 0.0
        self.input_tokens = 0
        self.cached_input_tokens = 0
        self.output_tokens = 0
        self.reasoning_tokens = 0
        self.unpriced_tokens = 0
        self._pricing_models: set[str] = set()
        self._warning_sink: Callable[[dict[str, Any]], None] | None = None
        self._warning_emitted = False
        self._started_at = time.monotonic()

    def bind_warning_sink(
        self, sink: Callable[[dict[str, Any]], None] | None
    ) -> None:
        """Bind the session event sink used for one-shot threshold warnings."""

        self._warning_sink = sink
        self._notify_warning_if_needed()

    def set_limits(self, *, max_usd: float, warning_usd: float) -> None:
        """Atomically update the user-facing cost cap and warning threshold."""

        self.max_usd = float(max_usd)
        self.warning_usd = float(warning_usd)
        # Raising the warning above current spend arms it again.  Lowering the
        # threshold below current spend emits an immediate, honest warning.
        self._warning_emitted = (
            self.warning_usd > 0
            and self.spent_usd + 1e-9 >= self.warning_usd
        )
        if self._warning_emitted and self._warning_sink is not None:
            self._warning_sink(self.snapshot())

    def _notify_warning_if_needed(self) -> None:
        if self._warning_emitted or self.warning_usd <= 0:
            return
        if self.spent_usd + 1e-9 < self.warning_usd:
            return
        self._warning_emitted = True
        if self._warning_sink is not None:
            self._warning_sink(self.snapshot())

    @staticmethod
    def _model_key(model: str) -> str:
        return str(model or "").strip().lower().split("/")[-1]

    @classmethod
    def token_prices(cls, model: str) -> dict[str, float] | None:
        prices = _MODEL_TOKEN_PRICES.get(cls._model_key(model))
        return dict(prices) if prices is not None else None

    def record_model_usage(
        self,
        usage: Mapping[str, Any],
        *,
        provider: str,
        model: str,
    ) -> dict[str, Any]:
        """Settle one provider-reported model usage record.

        Token counts are always retained.  Money prefers a provider-returned
        billed cost (OpenRouter and compatible gateways); only when it is absent
        do we calculate from an exact, verified model-price row.  Unknown models
        stay visibly unpriced rather than borrowing another model's rate.
        """

        input_tokens = _usage_int(
            usage, "prompt_tokens", "input_tokens", "promptTokenCount"
        )
        output_tokens = _usage_int(
            usage, "completion_tokens", "output_tokens", "candidatesTokenCount"
        )
        prompt_details = usage.get("prompt_tokens_details")
        completion_details = usage.get("completion_tokens_details")
        cached_tokens = _mapping_int(
            prompt_details, "cached_tokens"
        ) or _usage_int(
            usage,
            "cache_read_input_tokens",
            "cached_input_tokens",
            "cachedContentTokenCount",
        )
        cache_write_tokens = _usage_int(usage, "cache_creation_input_tokens")
        reasoning_tokens = _mapping_int(completion_details, "reasoning_tokens") or _usage_int(
            usage, "reasoning_tokens", "thoughtsTokenCount"
        )
        cached_tokens = min(max(cached_tokens, 0), max(input_tokens, 0))

        billed_cost = _usage_float(usage, "cost", "total_cost", "billed_cost_usd")
        prices = self.token_prices(model)
        calculated_cost: float | None = None
        pricing_source = "provider"
        if billed_cost is None and prices is not None:
            uncached_tokens = max(input_tokens - cached_tokens - cache_write_tokens, 0)
            input_multiplier = 2.0 if input_tokens > 272_000 and self._model_key(model).startswith("gpt-5.6") else 1.0
            output_multiplier = 1.5 if input_tokens > 272_000 and self._model_key(model).startswith("gpt-5.6") else 1.0
            calculated_cost = (
                uncached_tokens * prices["input"] * input_multiplier
                + cached_tokens * prices["cached_input"] * input_multiplier
                + cache_write_tokens * prices["input"] * 1.25 * input_multiplier
                + output_tokens * prices["output"] * output_multiplier
            ) / 1_000_000.0
            pricing_source = "verified_model_price"

        model_cost = billed_cost if billed_cost is not None else calculated_cost
        self.input_tokens += max(input_tokens, 0)
        self.cached_input_tokens += max(cached_tokens, 0)
        self.output_tokens += max(output_tokens, 0)
        self.reasoning_tokens += max(reasoning_tokens, 0)
        model_key = self._model_key(model)
        if model_cost is None:
            self.unpriced_tokens += max(input_tokens, 0) + max(output_tokens, 0)
            pricing_source = "unpriced"
        else:
            model_cost = max(float(model_cost), 0.0)
            self.model_spent_usd += model_cost
            self.spent_usd += model_cost
            if model_key:
                self._pricing_models.add(model_key)
        self._notify_warning_if_needed()
        return {
            "provider": str(provider or ""),
            "model": model_key,
            "input_tokens": max(input_tokens, 0),
            "cached_input_tokens": max(cached_tokens, 0),
            "output_tokens": max(output_tokens, 0),
            "reasoning_tokens": max(reasoning_tokens, 0),
            "cost_usd": round(model_cost, 8) if model_cost is not None else None,
            "pricing_source": pricing_source,
        }

    def prepare_model_call(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        tools: list[dict[str, Any]] | None = None,
        default_max_output_tokens: int = 8096,
    ) -> dict[str, Any]:
        """Return an affordable output-token ceiling for the next model call.

        Input tokens cannot be known exactly before the provider tokenizes the
        request, so the estimate is intentionally conservative for CJK and JSON.
        The final provider usage always replaces this estimate in accounting.
        """

        remaining = max(self.max_usd - self.spent_usd, 0.0)
        prices = self.token_prices(model)
        if prices is None:
            # Test/dry-run clients intentionally have no model identity and
            # therefore no billable model surface.  Preserve their legacy tool
            # budget semantics; a named-but-unpriced production model still
            # stops once the already-settled total reaches the cap.
            model_is_named = bool(self._model_key(model))
            return {
                "ok": (not model_is_named) or remaining > 1e-9,
                "max_output_tokens": int(default_max_output_tokens),
                "estimated_input_tokens": None,
                "estimated_input_cost_usd": None,
                "pricing_available": False,
                "reason": (
                    "model price is unavailable and the settled budget is exhausted"
                    if model_is_named and remaining <= 1e-9
                    else ""
                ),
            }

        serialized = json.dumps(
            {"messages": messages, "tools": tools or []},
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        cjk_like = sum(1 for ch in serialized if ord(ch) > 127)
        ascii_like = len(serialized) - cjk_like
        input_tokens_est = max(int(math.ceil((cjk_like + ascii_like / 4.0) * 1.12)), 1)
        long_context = input_tokens_est > 272_000 and self._model_key(model).startswith("gpt-5.6")
        input_multiplier = 2.0 if long_context else 1.0
        output_multiplier = 1.5 if long_context else 1.0
        input_cost = input_tokens_est * prices["input"] * input_multiplier / 1_000_000.0
        output_budget = remaining - input_cost
        if output_budget <= 0:
            return {
                "ok": False,
                "max_output_tokens": 0,
                "estimated_input_tokens": input_tokens_est,
                "estimated_input_cost_usd": input_cost,
                "pricing_available": True,
                "reason": (
                    f"model input would exceed cap: ${self.spent_usd + input_cost:.4f} "
                    f"> ${self.max_usd:.4f}"
                ),
            }
        affordable_output = int(
            math.floor(output_budget * 1_000_000.0 / (prices["output"] * output_multiplier))
        )
        if affordable_output < 1:
            return {
                "ok": False,
                "max_output_tokens": 0,
                "estimated_input_tokens": input_tokens_est,
                "estimated_input_cost_usd": input_cost,
                "pricing_available": True,
                "reason": "no output-token budget remains for the model call",
            }
        return {
            "ok": True,
            "max_output_tokens": min(int(default_max_output_tokens), affordable_output),
            "estimated_input_tokens": input_tokens_est,
            "estimated_input_cost_usd": input_cost,
            "pricing_available": True,
            "reason": "",
        }

    def estimate(self, tool_name: str) -> tuple[float, float]:
        entry = _TOOL_COSTS.get(tool_name)
        if entry is None:
            return 0.0, 5.0
        return float(entry["usd"]), float(entry["eta_sec"])

    def check(self, tool_name: str) -> BudgetDecision:
        cost, eta = self.estimate(tool_name)
        projected_usd = self.spent_usd + cost
        # Use cumulative tool-execution time (spent_seconds), not wall-clock elapsed time.
        # This is symmetric with cost accounting: both measure actual committed resources,
        # not idle/streaming/waiting time. Idle time is borne by the host, not the session.
        projected_sec = self.spent_seconds + eta
        if projected_usd > self.max_usd:
            return BudgetDecision(
                ok=False,
                estimated_cost_usd=cost,
                estimated_eta_sec=eta,
                reason=f"session cost would exceed cap: ${projected_usd:.2f} > ${self.max_usd:.2f}",
                alternatives=_cheaper(tool_name),
            )
        if projected_sec > self.max_seconds:
            return BudgetDecision(
                ok=False,
                estimated_cost_usd=cost,
                estimated_eta_sec=eta,
                reason=f"session time would exceed cap: {projected_sec:.0f}s > {self.max_seconds:.0f}s",
                alternatives=_cheaper(tool_name),
            )
        return BudgetDecision(ok=True, estimated_cost_usd=cost, estimated_eta_sec=eta)

    def reserve(self, tool_name: str) -> tuple[BudgetDecision, BudgetReservation | None]:
        """Atomically check and reserve the estimated budget for a future dispatch.

        Parallel tool execution must account for estimates before launching work,
        otherwise N concurrent calls can all pass ``check()`` against the same
        stale totals and collectively exceed the session caps. The caller settles
        the reservation with ``commit_reserved()`` after the call finishes.

        No lock is needed: all callers live on the session event loop and there is
        no ``await`` between the ``check()`` read and the ``spent_*`` add, so the
        check-then-add is atomic (see docs/multi-agent-plan.md §5.1).
        """
        decision = self.check(tool_name)
        if not decision.ok:
            return decision, None
        reservation = BudgetReservation(
            tool_name=tool_name,
            estimated_cost_usd=decision.estimated_cost_usd,
            estimated_eta_sec=decision.estimated_eta_sec,
        )
        self.spent_usd += reservation.estimated_cost_usd
        self.tool_spent_usd += reservation.estimated_cost_usd
        self.spent_seconds += reservation.estimated_eta_sec
        self._notify_warning_if_needed()
        return decision, reservation

    def reserve_amount(
        self, label: str, *, usd: float, seconds: float
    ) -> tuple[BudgetDecision, BudgetReservation | None]:
        """Amount-based reservation for host capabilities (e.g. subtask slices)
        that are not a single ``_TOOL_COSTS`` row.

        Same atomic check-then-add as ``reserve()``, but the caller supplies the
        amounts directly. Refuses (and reserves nothing) if the requested slice
        would push either axis past the cap. ``label`` is echoed onto the
        reservation for snapshots/logs only.
        """
        usd = float(usd)
        seconds = float(seconds)
        projected_usd = self.spent_usd + usd
        projected_sec = self.spent_seconds + seconds
        if projected_usd > self.max_usd:
            return (
                BudgetDecision(
                    ok=False,
                    estimated_cost_usd=usd,
                    estimated_eta_sec=seconds,
                    reason=(
                        f"session cost would exceed cap: "
                        f"${projected_usd:.2f} > ${self.max_usd:.2f}"
                    ),
                ),
                None,
            )
        if projected_sec > self.max_seconds:
            return (
                BudgetDecision(
                    ok=False,
                    estimated_cost_usd=usd,
                    estimated_eta_sec=seconds,
                    reason=(
                        f"session time would exceed cap: "
                        f"{projected_sec:.0f}s > {self.max_seconds:.0f}s"
                    ),
                ),
                None,
            )
        reservation = BudgetReservation(
            tool_name=label,
            estimated_cost_usd=usd,
            estimated_eta_sec=seconds,
        )
        self.spent_usd += usd
        self.tool_spent_usd += usd
        self.spent_seconds += seconds
        self._notify_warning_if_needed()
        return (
            BudgetDecision(ok=True, estimated_cost_usd=usd, estimated_eta_sec=seconds),
            reservation,
        )

    def commit_reserved(
        self,
        reservation: BudgetReservation,
        *,
        actual_usd: float | None = None,
        actual_seconds: float | None = None,
    ) -> None:
        """Settle a prior reservation with actual observed resource usage.

        ``spent += actual - estimated`` on each axis. An unspent slice returns
        automatically: a lower actual produces a negative delta that credits the
        session totals back down. Falls back to the reserved estimate when an
        axis's actual is not supplied.
        """
        actual_cost = (
            float(actual_usd)
            if actual_usd is not None
            else reservation.estimated_cost_usd
        )
        actual_sec = (
            float(actual_seconds)
            if actual_seconds is not None
            else reservation.estimated_eta_sec
        )
        self.spent_usd += actual_cost - reservation.estimated_cost_usd
        self.tool_spent_usd += actual_cost - reservation.estimated_cost_usd
        self.spent_seconds += actual_sec - reservation.estimated_eta_sec
        self._notify_warning_if_needed()

    def commit(
        self,
        tool_name: str,
        *,
        actual_usd: float | None = None,
        actual_seconds: float | None = None,
    ) -> None:
        cost, eta = self.estimate(tool_name)
        settled_cost = float(actual_usd) if actual_usd is not None else cost
        self.spent_usd += settled_cost
        self.tool_spent_usd += settled_cost
        self.spent_seconds += float(actual_seconds) if actual_seconds is not None else eta
        self._notify_warning_if_needed()

    def snapshot(self) -> dict[str, Any]:
        return {
            "max_usd": self.max_usd,
            "warning_usd": self.warning_usd if self.warning_usd > 0 else None,
            "max_seconds": self.max_seconds,
            "spent_usd": round(self.spent_usd, 4),
            "tool_spent_usd": round(self.tool_spent_usd, 4),
            "model_spent_usd": round(self.model_spent_usd, 6),
            "spent_seconds": round(self.spent_seconds, 2),
            "elapsed_seconds": round(time.monotonic() - self._started_at, 2),
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "unpriced_tokens": self.unpriced_tokens,
            "pricing_models": sorted(self._pricing_models),
            "warning_reached": (
                self.warning_usd > 0
                and self.spent_usd + 1e-9 >= self.warning_usd
            ),
        }


def _mapping_int(value: Any, key: str) -> int:
    if not isinstance(value, Mapping):
        return 0
    return _coerce_int(value.get(key))


def _usage_int(usage: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        if key in usage:
            return _coerce_int(usage.get(key))
    return 0


def _coerce_int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _usage_float(usage: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key not in usage:
            continue
        try:
            value = float(usage.get(key))
        except (TypeError, ValueError):
            continue
        if value >= 0:
            return value
    return None


def tool_cost_usd(tool_name: str) -> float:
    """Module-level cost lookup for callers that need the estimate without a
    BudgetGuard instance (e.g. the generate_image dispatcher's audit record)."""
    entry = _TOOL_COSTS.get(tool_name)
    return float(entry["usd"]) if entry else 0.0


def _cheaper(tool_name: str) -> list[str]:
    if tool_name == "generate_video":
        return ["search_library", "generate_image"]
    if tool_name in {"generate_image", "generate_audio"}:
        return ["search_library"]
    return []


__all__ = ["BudgetGuard", "BudgetDecision", "BudgetReservation", "tool_cost_usd"]
