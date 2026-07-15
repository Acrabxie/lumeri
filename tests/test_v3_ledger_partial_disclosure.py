"""FM3 (host side) gate — a recoverable library failure degrades to an honest
partial answer (charter §10 failure-mode-3, P10).

The lived bug: an editing turn hard-errored with "host acceptance ledger
remains incomplete" and DISCARDED the model's honest partial explanation ("I
removed the title but couldn't remove the original shape"), leaving the user an
opaque halt. The host turn-ledger's contract is that when a turn stops
incomplete AFTER doing work and the model's response carries prose, that prose
is DELIVERED (rendered softly, not as a red interrupt) alongside the incomplete
status — the library only *feeds* typed/recoverable errors. This test locks
that contract: it drives a real ``AgentLoopV3`` where a mutating tool fails
recoverably and the model then explains honestly, and asserts the explanation
reaches the user. Companion (library side): ``test_library_ledger_contract.py``.
See ``gemia/docs/point-library-charter.md`` §10 (failure mode 3).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator

import gemia.agent_loop_v3 as loop_mod
from gemia.agent_loop_v3 import AgentLoopV3


_HONEST_PARTIAL = (
    "我删掉了标题图层，但原始 demo 形状因为图层 ID 的问题没能删除——"
    "要我换个方式重试吗？"
)


async def _fake_edit_recoverable_failure(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """A mutating verb that fails with a typed, recoverable error (not a crash)."""
    del args, ctx
    return {
        "applied": False,
        "error_code": "E_NOT_FOUND",
        "error_message": "delete_layer: layer not found",
        "recovery": "none",
    }


class _FailsThenExplainsHonestly:
    """call 1 → a mutating tool call that fails recoverably; every call after →
    an honest partial explanation in prose, no tool calls (the model has done
    what it could and is reporting the shortfall)."""

    model = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def stream_turn(
        self, messages: list[dict[str, Any]], *, tools=None, temperature: float = 0.7
    ) -> AsyncIterator[dict[str, Any]]:
        del messages, tools, temperature
        self.calls += 1
        if self.calls == 1:
            yield {"kind": "tool_call_start", "index": 0, "id": "e1", "name": "fake_edit"}
            yield {"kind": "tool_call_args_delta", "index": 0, "delta": "{}"}
            yield {"kind": "finish", "reason": "tool_calls"}
            return
        yield {"kind": "text_delta", "text": _HONEST_PARTIAL}
        yield {"kind": "finish", "reason": "stop"}


def test_recoverable_library_failure_degrades_to_partial(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(loop_mod.DISPATCHER, "fake_edit", _fake_edit_recoverable_failure)

    client = _FailsThenExplainsHonestly()
    events: list[dict[str, Any]] = []
    loop = AgentLoopV3(
        session_id="fm3_partial_disclosure",
        output_dir=tmp_path,
        gemini_client=client,  # type: ignore[arg-type]
        emit_event=events.append,
    )

    # An actionable edit request → ledger_enforced; the failed mutation leaves
    # the ledger incomplete, so the turn stops as incomplete_goal.
    asyncio.run(loop.run_turn("删除标题图层并重新导出"))

    # The turn honestly stops incomplete (it did NOT fake a green completion).
    assert any(
        e.get("kind") == "turn_error" and e.get("reason") == "incomplete_goal"
        for e in events
    ), "an unfinished edit turn must stop as incomplete_goal, not fake completion"
    assert not [e for e in events if e.get("kind") == "turn_complete"]

    # …AND the model's honest partial explanation was DELIVERED, not discarded.
    delivered = "".join(
        e.get("delta", "") for e in events if e.get("kind") == "model_text_delta"
    )
    assert _HONEST_PARTIAL in delivered, (
        "the model's honest partial explanation was discarded — the user only got "
        f"an opaque halt. delivered text deltas = {delivered!r}"
    )

    # A graceful wrap-up summary also accompanies the stop (never a bare halt).
    assert any(e.get("kind") == "turn_wrapup" for e in events)
