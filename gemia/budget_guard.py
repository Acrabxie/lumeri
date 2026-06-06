"""Budget guard for the Lumeri v3 agent loop.

The only host-side gate. Tracks per-session cumulative cost and elapsed
time; returns either ``ok`` or ``needs_approval``. The model decides what
to do with ``needs_approval`` — host does not auto-fallback, auto-pick a
cheaper tool, or auto-ask the user.

There is no capability gate, no stability gate, no approval stub. The
model holds the wheel; the host only reports real money and real time.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


_TOOL_COSTS: dict[str, dict[str, float]] = {
    # Provider-backed (real money). Numbers verified in doc 07 from
    # Google's public pricing pages (2026-05-30 snapshot).
    "generate_image":     {"usd": 0.101, "eta_sec": 10.0},   # Nano Banana 2, 2K
    "generate_video":     {"usd": 2.80,  "eta_sec": 120.0},  # Veo 3.1 fast, 8s
    "generate_audio":     {"usd": 0.00,  "eta_sec": 45.0},   # Lyria 3 clip — preview, currently free
    "analyze_media":      {"usd": 0.01,  "eta_sec": 4.0},    # rough Gemini text estimate
    # Local ffmpeg (zero money, time only).
    "edit_image":         {"usd": 0.00, "eta_sec": 4.0},
    "edit_video":         {"usd": 0.00, "eta_sec": 10.0},
    "composite":          {"usd": 0.00, "eta_sec": 8.0},
    "color_grade":        {"usd": 0.00, "eta_sec": 8.0},
    "add_overlay":        {"usd": 0.00, "eta_sec": 6.0},
    "arrange_timeline":   {"usd": 0.00, "eta_sec": 12.0},
    "mix_audio":          {"usd": 0.00, "eta_sec": 6.0},
    "transform_geometry": {"usd": 0.00, "eta_sec": 5.0},
    "extract_frame":      {"usd": 0.00, "eta_sec": 1.0},
    "search_library":     {"usd": 0.00, "eta_sec": 0.5},
    "export":             {"usd": 0.00, "eta_sec": 20.0},
    # M3 verbs (networking + sandbox bash).
    "fetch":              {"usd": 0.00, "eta_sec": 5.0},     # Host-side https download
    "run_shell":          {"usd": 0.00, "eta_sec": 10.0},    # Sandboxed bash execution
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


class BudgetGuard:
    """Per-session cumulative cost + time tracker."""

    def __init__(self, *, max_usd: float = 5.0, max_seconds: float = 600.0) -> None:
        self.max_usd = float(max_usd)
        self.max_seconds = float(max_seconds)
        self.spent_usd = 0.0
        self.spent_seconds = 0.0
        self._started_at = time.monotonic()

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

    def commit(
        self,
        tool_name: str,
        *,
        actual_usd: float | None = None,
        actual_seconds: float | None = None,
    ) -> None:
        cost, eta = self.estimate(tool_name)
        self.spent_usd += float(actual_usd) if actual_usd is not None else cost
        self.spent_seconds += float(actual_seconds) if actual_seconds is not None else eta

    def snapshot(self) -> dict[str, Any]:
        return {
            "max_usd": self.max_usd,
            "max_seconds": self.max_seconds,
            "spent_usd": round(self.spent_usd, 4),
            "spent_seconds": round(self.spent_seconds, 2),
            "elapsed_seconds": round(time.monotonic() - self._started_at, 2),
        }


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


__all__ = ["BudgetGuard", "BudgetDecision", "tool_cost_usd"]
