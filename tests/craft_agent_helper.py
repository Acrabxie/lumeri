"""Stand in for the agent when exercising the two-step adjust contract.

`op:"adjust"` no longer sizes a move from wording — it resolves the *direction*
and hands the degree back, because how far "a bit warmer" goes depends on where
the axis already sits and what the user has already pushed back on, none of
which the engine can see. Tests still want to assert "this phrase moves that
axis that way", so this helper plays the part the agent plays in production:
read the proposal, pick a number, call back with it.

Its own simplicity is the point — a real agent weighs the conversation here.
"""
from __future__ import annotations

from typing import Any, Callable


def decide(proposal: dict[str, Any], step: float = 0.25) -> dict[str, float]:
    """Turn a degree proposal into absolute axis values, moving one step."""
    params: dict[str, float] = {}
    for reading in proposal.get("readings") or []:
        for axis, target in (reading.get("targets") or {}).items():
            current = float(target["current"])
            if target["direction"] == "up":
                params[axis] = round(min(1.0, current + step), 4)
            else:
                params[axis] = round(max(0.0, current - step), 4)
    return params


def agent_adjust(
    adjust: Callable[..., dict[str, Any]],
    brief: dict[str, Any],
    phrases: list[str],
    step: float = 0.25,
) -> dict[str, Any]:
    """Propose, choose a degree, apply — the full round trip an agent makes."""
    proposal = adjust(brief, phrases)
    if proposal.get("needs") != "degree":
        return proposal
    params = decide(proposal, step)
    if not params:
        return proposal          # nothing readable; caller asserts on unknown
    return adjust(brief, phrases, params)


async def agent_dispatch(
    dispatch: Callable[..., Any],
    args: dict[str, Any],
    step: float = 0.25,
) -> dict[str, Any]:
    """The same round trip through a tool's ``dispatch``."""
    first = await dispatch(args)
    if first.get("needs") != "degree":
        return first
    params = decide(first, step)
    if not params:
        return first
    return await dispatch({**args, "params": params})
