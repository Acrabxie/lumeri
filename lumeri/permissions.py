"""Composable permission policy for the Lumeri runtime kernel.

This is intentionally minimal. It sits *above* the sandbox: the sandbox AST
checks are safety-critical and remain hardcoded. Permissions here gate
higher-level operations (turn execution, script generation, patch
application) so callers can build interactive, headless, or restricted
agents from the same loop code.

Decisions are one of:

- ``allow``  — proceed silently
- ``deny``   — refuse, agent loop stops the turn
- ``ask``    — caller must resolve (an EventBus subscriber typically). In
  headless mode the default resolution is ``deny``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ALLOW = "allow"
DENY = "deny"
ASK = "ask"
_VALID = {ALLOW, DENY, ASK}


_DEFAULT_RULES: dict[str, str] = {
    "agent:run_turn": ALLOW,
    "script:generate": ALLOW,
    "script:execute": ALLOW,
    "patch:apply": ALLOW,
    "agent:exceed_max_turns": DENY,
}


@dataclass
class PermissionDecision:
    action: str
    decision: str
    reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class PermissionError(RuntimeError):
    """Raised when an action is denied or unresolved."""


class PermissionSet:
    """A flat ``action -> decision`` table with an explicit default."""

    def __init__(
        self,
        rules: dict[str, str] | None = None,
        *,
        default: str = ALLOW,
        on_ask: str = DENY,
    ) -> None:
        if default not in _VALID:
            raise ValueError(f"invalid default decision: {default}")
        if on_ask not in {ALLOW, DENY}:
            raise ValueError(f"on_ask must be allow or deny, got {on_ask}")
        merged = dict(_DEFAULT_RULES)
        if rules:
            for key, value in rules.items():
                if value not in _VALID:
                    raise ValueError(f"invalid decision for {key}: {value}")
                merged[key] = value
        self._rules = merged
        self._default = default
        self._on_ask = on_ask

    def check(self, action: str) -> PermissionDecision:
        decision = self._rules.get(action, self._default)
        if decision == ASK:
            # Headless callers cannot prompt; resolve to ``on_ask``.
            return PermissionDecision(action=action, decision=self._on_ask, reason="asked-resolved")
        return PermissionDecision(action=action, decision=decision)

    def require(self, action: str) -> PermissionDecision:
        decision = self.check(action)
        if decision.decision != ALLOW:
            raise PermissionError(f"permission denied: {action} ({decision.decision})")
        return decision

    def as_dict(self) -> dict[str, str]:
        return dict(self._rules)
