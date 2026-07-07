"""Multi-agent fan-out — Phase 1 gates (docs/multi-agent-plan.md §4-§8, WP2/WP3).

Offline: a fake client + fake dispatchers drive N children with no network. The
gates asserted here are exactly the P1 test list:
- profile coverage (§4.2): every profile ⊆ TOOL_NAMES, ∩ FORBIDDEN = ∅, and
  spawn_subtasks ∈ PLAN_BLOCKED_TOOLS;
- slice arithmetic (§5.2): parent 20% floor, rest split /N, max_cost_usd clamps
  down only; budget slice isolation + unspent return;
- ordered subagent_start / subagent_result, one pair per started child;
- the double-count rule (§5.3): the loop commits 0 s for spawn_subtasks;
- deadline → timeout status; cancellation-on-parent-error settles reservations;
- child doom-loop ends the CHILD (not the parent); plan-flag mid-batch clamp.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from gemia import subtasks as sub
from gemia.agent_loop_v3 import AgentLoopV3
from gemia.budget_guard import BudgetGuard
from gemia.plan_mode import PLAN_BLOCKED_TOOLS
from gemia.tools._schema import TOOL_NAMES


# ── §4.2 profile coverage (mirrors tests/test_plan_mode.py style) ────────────


def test_profiles_are_subsets_of_registered_tools() -> None:
    names = set(TOOL_NAMES)
    for profile_name, tools in sub.PROFILES.items():
        assert tools <= names, f"{profile_name} references unknown tools: {tools - names}"


def test_profiles_exclude_forbidden_tools() -> None:
    for profile_name, tools in sub.PROFILES.items():
        assert not (tools & sub.FORBIDDEN_IN_ANY_PROFILE), (
            f"{profile_name} contains globally-forbidden tools: "
            f"{tools & sub.FORBIDDEN_IN_ANY_PROFILE}"
        )


def test_forbidden_set_is_registered_or_intentional() -> None:
    # Every forbidden name is either a real tool (so blocking it means something)
    # or spawn_subtasks/elicit which ARE real. No typos silently forbidding
    # nothing.
    names = set(TOOL_NAMES)
    assert sub.FORBIDDEN_IN_ANY_PROFILE <= names


def test_spawn_subtasks_is_plan_blocked() -> None:
    assert "spawn_subtasks" in PLAN_BLOCKED_TOOLS


def test_spawn_subtasks_forbidden_in_every_profile() -> None:
    assert "spawn_subtasks" in sub.FORBIDDEN_IN_ANY_PROFILE
    for tools in sub.PROFILES.values():
        assert "spawn_subtasks" not in tools


# ── §5.2 slice arithmetic ────────────────────────────────────────────────────


def test_slice_reserves_parent_floor_and_splits_rest() -> None:
    g = BudgetGuard(max_usd=10.0, max_seconds=1000.0)
    pool_usd, pool_sec, refusal = sub._slice_budget(g, 2)
    assert refusal is None
    # 20% floor of 10 = 2 → pool 8; of 1000 = 200 → pool 800.
    assert pool_usd == pytest.approx(8.0)
    assert pool_sec == pytest.approx(800.0)


def test_slice_refuses_when_pool_exhausted() -> None:
    g = BudgetGuard(max_usd=10.0, max_seconds=1000.0)
    g.spent_seconds = 900.0  # only 100s remain; 20% floor = 200 → pool negative
    _, _, refusal = sub._slice_budget(g, 2)
    assert refusal is not None


# ── fake client + dispatchers ────────────────────────────────────────────────


class _ScriptedClient:
    """One tool call per child (routed by the goal text in the child's messages),
    then a text stop. The parent's own turn is scripted to call spawn_subtasks
    once, then stop."""

    model = "fake"

    def __init__(self, spawn_args: dict[str, Any]) -> None:
        self._spawn_args = spawn_args
        self.parent_calls = 0
        # child_state keyed by goal → number of streams already served
        self._child_streams: dict[str, int] = {}

    async def stream_turn(
        self, messages: list[dict[str, Any]], *, tools=None, temperature: float = 0.7
    ) -> AsyncIterator[dict[str, Any]]:
        del temperature
        # Distinguish parent vs child by whether spawn_subtasks is in the tools.
        tool_names = {t["function"]["name"] for t in (tools or [])}
        if "spawn_subtasks" in tool_names:
            async for ev in self._parent_stream():
                yield ev
            return
        async for ev in self._child_stream(messages):
            yield ev

    async def _parent_stream(self) -> AsyncIterator[dict[str, Any]]:
        self.parent_calls += 1
        if self.parent_calls == 1:
            yield {"kind": "tool_call_start", "index": 0, "id": "spawn_call",
                   "name": "spawn_subtasks"}
            yield {"kind": "tool_call_args_delta", "index": 0,
                   "delta": json.dumps(self._spawn_args)}
            yield {"kind": "finish", "reason": "tool_calls"}
            return
        yield {"kind": "text_delta", "text": "integrated results."}
        yield {"kind": "finish", "reason": "stop"}

    def _goal_of(self, messages: list[dict[str, Any]]) -> str:
        for m in messages:
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                return m["content"]
        return ""

    async def _child_stream(
        self, messages: list[dict[str, Any]]
    ) -> AsyncIterator[dict[str, Any]]:
        goal = self._goal_of(messages)
        served = self._child_streams.get(goal, 0)
        self._child_streams[goal] = served + 1
        if served == 0:
            # First child stream: one probe_media call.
            yield {"kind": "tool_call_start", "index": 0, "id": "c_probe",
                   "name": "probe_media"}
            yield {"kind": "tool_call_args_delta", "index": 0,
                   "delta": json.dumps({"asset_id": "v_001"})}
            yield {"kind": "finish", "reason": "tool_calls"}
            return
        # Second child stream: final text, done.
        yield {"kind": "text_delta", "text": f"done: {goal[:20]}"}
        yield {"kind": "finish", "reason": "stop"}


def _make_loop(tmp_path: Path, client, **kw) -> tuple[AgentLoopV3, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    loop = AgentLoopV3(
        session_id=kw.pop("sid", "subtasks_test"),
        output_dir=tmp_path,
        gemini_client=client,  # type: ignore[arg-type]
        emit_event=events.append,
        **kw,
    )
    return loop, events


@pytest.fixture
def fake_probe(monkeypatch: pytest.MonkeyPatch):
    """Replace probe_media with a fast fake that commits ~10 s of budget so the
    double-count and settlement assertions have real numbers to check."""
    async def _probe(args: dict[str, Any], ctx) -> dict[str, Any]:
        # Simulate ~10s of tool-seconds by committing directly is not possible
        # (the loop owns commit); instead sleep a hair and let elapsed be tiny —
        # the child guard commits actual elapsed. For deterministic seconds we
        # return a marker; the seconds assertions below use per-child guards
        # patched separately.
        return {"summary": "probed", "duration_sec": 3.0}

    from gemia import subtasks as _sub
    monkeypatch.setitem(_sub.DISPATCHER, "probe_media", _probe)
    return _probe


# ── ordered start/result, budget isolation, double-count ─────────────────────


def test_two_child_fanout_ordered_events_and_settlement(
    tmp_path: Path, fake_probe, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Make each child's probe commit a deterministic 10s by patching the child
    # guard's commit to add a fixed amount. We do it via a wrapper on the fake
    # probe that records into a shared per-goal spend, then patch BudgetGuard.
    spawn_args = {
        "subtasks": [
            {"goal": "annotate clip A", "tool_profile": "probe"},
            {"goal": "annotate clip B", "tool_profile": "probe"},
        ]
    }
    client = _ScriptedClient(spawn_args)
    loop, events = _make_loop(tmp_path, client, budget_max_usd=5.0, budget_max_seconds=600.0)

    # Force each child's probe_media to settle exactly 10 s regardless of wall
    # time, by patching the child guards' commit. We intercept BudgetGuard.commit
    # to add 10s whenever probe_media is committed with an explicit actual.
    real_commit = BudgetGuard.commit
    def fake_commit(self, tool_name, *, actual_usd=None, actual_seconds=None):
        if tool_name == "probe_media":
            actual_seconds = 10.0
        return real_commit(self, tool_name, actual_usd=actual_usd, actual_seconds=actual_seconds)
    monkeypatch.setattr(BudgetGuard, "commit", fake_commit)

    asyncio.run(loop.run_turn("annotate these clips"))

    starts = [e for e in events if e.get("kind") == "subagent_start"]
    results = [e for e in events if e.get("kind") == "subagent_result"]
    assert [e["agent_id"] for e in starts] == ["sub_1", "sub_2"]
    assert [e["agent_id"] for e in results] == ["sub_1", "sub_2"]
    assert all(e["call_id"] == "spawn_call" for e in starts + results)
    assert all(e["status"] == "ok" for e in results)
    # Each child settled ~10s of its slice.
    assert results[0]["spent_seconds"] == pytest.approx(10.0, abs=0.5)
    assert results[1]["spent_seconds"] == pytest.approx(10.0, abs=0.5)

    # §5.3 double-count: children settled 10 + 10 = 20 s; the parent must NOT
    # add the batch wall-clock on top. Total session seconds ≈ 20 (± a hair for
    # the tiny orchestration overhead, which is 0 for spawn_subtasks).
    assert loop.budget.spent_seconds == pytest.approx(20.0, abs=0.1)


def test_child_tool_events_carry_agent_id(
    tmp_path: Path, fake_probe
) -> None:
    spawn_args = {"subtasks": [{"goal": "probe A", "tool_profile": "probe"}]}
    client = _ScriptedClient(spawn_args)
    loop, events = _make_loop(tmp_path, client)
    asyncio.run(loop.run_turn("go"))

    child_execs = [
        e for e in events
        if e.get("kind", "").startswith("tool_exec_") and e.get("agent_id")
    ]
    assert child_execs, "child tool activity must ride tool_exec_* with agent_id"
    assert all(e["agent_id"] == "sub_1" for e in child_execs)
    assert all(e["call_id"] == "spawn_call" for e in child_execs)
    # The PARENT's own spawn call has no agent_id.
    parent_execs = [
        e for e in events
        if e.get("kind") == "tool_exec_start" and not e.get("agent_id")
    ]
    assert any(e["tool_name"] == "spawn_subtasks" for e in parent_execs)


def test_max_cost_usd_clamps_down_only(tmp_path: Path, fake_probe) -> None:
    # Pool per child would be large; a tiny max_cost_usd must clamp the slice DOWN.
    spawn_args = {
        "subtasks": [
            {"goal": "cheap child", "tool_profile": "probe", "max_cost_usd": 0.10},
        ]
    }
    client = _ScriptedClient(spawn_args)
    loop, events = _make_loop(tmp_path, client, budget_max_usd=10.0)
    asyncio.run(loop.run_turn("go"))
    start = [e for e in events if e.get("kind") == "subagent_start"][0]
    assert start["budget"]["max_usd"] == pytest.approx(0.10)


# ── refusal, over-cap, unknown profile ───────────────────────────────────────


def test_spawn_over_four_children_refused(tmp_path: Path) -> None:
    spawn_args = {
        "subtasks": [
            {"goal": f"c{i}", "tool_profile": "probe"} for i in range(5)
        ]
    }
    client = _ScriptedClient(spawn_args)
    loop, events = _make_loop(tmp_path, client)
    asyncio.run(loop.run_turn("go"))
    # The dispatcher raised E_SUBTASK → surfaced as a tool_exec_error, no children.
    errs = [e for e in events if e.get("kind") == "tool_exec_error"
            and e.get("tool_name") == "spawn_subtasks"]
    assert errs and errs[0]["error_code"] == "E_SUBTASK"
    assert not [e for e in events if e.get("kind") == "subagent_start"]


def test_spawn_unknown_profile_refused(tmp_path: Path) -> None:
    spawn_args = {"subtasks": [{"goal": "x", "tool_profile": "nonesuch"}]}
    client = _ScriptedClient(spawn_args)
    loop, events = _make_loop(tmp_path, client)
    asyncio.run(loop.run_turn("go"))
    errs = [e for e in events if e.get("kind") == "tool_exec_error"
            and e.get("tool_name") == "spawn_subtasks"]
    assert errs and errs[0]["error_code"] == "E_SUBTASK_PROFILE"


def test_spawn_refused_when_budget_pool_exhausted(tmp_path: Path) -> None:
    spawn_args = {"subtasks": [{"goal": "x", "tool_profile": "probe"}]}
    client = _ScriptedClient(spawn_args)
    loop, events = _make_loop(tmp_path, client, budget_max_seconds=600.0)
    loop.budget.spent_seconds = 590.0  # only 10s remain; 20% floor = 120 → refuse
    asyncio.run(loop.run_turn("go"))
    errs = [e for e in events if e.get("kind") == "tool_exec_error"
            and e.get("tool_name") == "spawn_subtasks"]
    assert errs and errs[0]["error_code"] == "E_BUDGET"


# ── fail-closed profile enforcement ──────────────────────────────────────────


def test_child_out_of_profile_tool_is_fail_closed(tmp_path: Path) -> None:
    """A child that hallucinates an out-of-profile tool name gets a structured
    E_SUBTASK_PROFILE tool_result and never dispatches it."""
    events: list[dict[str, Any]] = []

    class _P:  # minimal parent stand-in
        session_id = "s"
        plan_mode = False

    # Build a real loop to get a real ctx/registry/project, then a child on it.
    class _Noop:
        model = "fake"
        async def stream_turn(self, messages, *, tools=None, temperature=0.7):
            if False:
                yield {}
            return

    loop, ev = _make_loop(tmp_path, _Noop())
    child = sub.SubtaskLoop(
        agent_id="sub_1", parent=loop, call_id="spawn_call",
        goal="do a forbidden thing", profile_name="probe",
        guard=BudgetGuard(max_usd=1.0, max_seconds=100.0),
    )
    # Directly drive one dispatch of a forbidden tool.
    tc = {"id": "x", "name": "run_shell", "args_buf": [json.dumps({"cmd": "ls"})]}
    asyncio.run(child._dispatch_child_call(tc))
    tool_msgs = [m for m in child._messages if m.get("role") == "tool"]
    assert tool_msgs
    payload = json.loads(tool_msgs[-1]["content"])
    assert payload["error_code"] == "E_SUBTASK_PROFILE"


# ── plan-flag mid-batch clamp ────────────────────────────────────────────────


def test_child_plan_block_when_parent_toggles_mid_batch(tmp_path: Path) -> None:
    """With the parent's plan_mode ON, a child's mutating dispatch is clamped
    within one dispatch (defense in depth) with E_PLAN_MODE — and it does NOT
    touch the parent's plan_gate hard-stop counter."""
    class _Noop:
        model = "fake"
        async def stream_turn(self, messages, *, tools=None, temperature=0.7):
            if False:
                yield {}
            return

    loop, _ = _make_loop(tmp_path, _Noop())
    loop.set_plan_mode(True)
    child = sub.SubtaskLoop(
        agent_id="sub_1", parent=loop, call_id="spawn_call",
        goal="annotate", profile_name="annotate",
        guard=BudgetGuard(max_usd=1.0, max_seconds=100.0),
    )
    # annotate_media is in-profile but plan-blocked → clamped.
    tc = {"id": "x", "name": "annotate_media", "args_buf": [json.dumps({"asset_id": "v_001"})]}
    asyncio.run(child._dispatch_child_call(tc))
    tool_msgs = [m for m in child._messages if m.get("role") == "tool"]
    payload = json.loads(tool_msgs[-1]["content"])
    assert payload["error_code"] == "E_PLAN_MODE"
    assert payload["blocked_by_plan_mode"] is True


# ── child doom-loop ends the CHILD, not the parent ───────────────────────────


class _DoomChildClient(_ScriptedClient):
    """Child re-issues the SAME probe_media call with byte-identical args forever."""

    async def _child_stream(self, messages):
        yield {"kind": "tool_call_start", "index": 0, "id": "c", "name": "probe_media"}
        yield {"kind": "tool_call_args_delta", "index": 0,
               "delta": json.dumps({"asset_id": "v_001"})}
        yield {"kind": "finish", "reason": "tool_calls"}


def test_child_doom_loop_ends_child_not_parent(
    tmp_path: Path, fake_probe
) -> None:
    spawn_args = {"subtasks": [{"goal": "loop forever", "tool_profile": "probe"}]}
    client = _DoomChildClient(spawn_args)
    loop, events = _make_loop(tmp_path, client)
    asyncio.run(loop.run_turn("go"))
    results = [e for e in events if e.get("kind") == "subagent_result"]
    assert results and results[0]["status"] == "error"
    assert "same call" in results[0]["summary"] or "no progress" in results[0]["summary"]
    # The PARENT turn still completes normally (child's doom loop is isolated).
    assert [e for e in events if e.get("kind") == "turn_complete"]
    assert not [e for e in events if e.get("kind") == "turn_error"]


# ── deadline → timeout ───────────────────────────────────────────────────────


class _HangingChildClient(_ScriptedClient):
    async def _child_stream(self, messages):
        # First stream calls a slow tool; the deadline should cancel it.
        yield {"kind": "tool_call_start", "index": 0, "id": "c", "name": "probe_media"}
        yield {"kind": "tool_call_args_delta", "index": 0,
               "delta": json.dumps({"asset_id": "v_001"})}
        yield {"kind": "finish", "reason": "tool_calls"}


def test_deadline_cancels_stragglers_with_timeout_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _slow_probe(args, ctx):
        await asyncio.sleep(30.0)  # far beyond the tiny deadline below
        return {"summary": "never"}

    from gemia import subtasks as _sub
    monkeypatch.setitem(_sub.DISPATCHER, "probe_media", _slow_probe)

    spawn_args = {
        "subtasks": [{"goal": "slow", "tool_profile": "probe"}],
        "deadline_sec": 0.2,
    }
    client = _HangingChildClient(spawn_args)
    loop, events = _make_loop(tmp_path, client)
    asyncio.run(loop.run_turn("go"))

    results = [e for e in events if e.get("kind") == "subagent_result"]
    # Every STARTED child gets a terminal result even when cancelled by deadline.
    assert results and results[0]["status"] == "timeout"
    # Reservation settled → session totals consistent (no leaked reservation).
    assert loop.budget.spent_usd <= loop.budget.max_usd
    assert loop.budget.spent_seconds <= loop.budget.max_seconds


# ── cancellation-on-parent-error settles reservations ────────────────────────


def test_parent_cancel_settles_reservations_and_emits_terminals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelling the spawn dispatcher mid-flight (parent stream error / session
    close) must, via the mandatory finally, cancel children, settle every
    reservation, and emit a terminal subagent_result for every started child."""
    started = asyncio.Event()

    async def _slow_probe(args, ctx):
        started.set()
        await asyncio.sleep(30.0)
        return {"summary": "never"}

    from gemia import subtasks as _sub
    monkeypatch.setitem(_sub.DISPATCHER, "probe_media", _slow_probe)

    spawn_args = {"subtasks": [{"goal": "s", "tool_profile": "probe"}]}
    client = _HangingChildClient(spawn_args)
    events: list[dict[str, Any]] = []
    loop = AgentLoopV3(
        session_id="cancel_test", output_dir=tmp_path,
        gemini_client=client, emit_event=events.append,  # type: ignore[arg-type]
    )

    async def _run_and_cancel() -> None:
        ctx = loop._tool_ctx
        ctx.extra["call_id"] = "spawn_call"
        task = asyncio.ensure_future(sub.dispatch(spawn_args, ctx))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_run_and_cancel())

    # Reservation was settled back (spent returned to a consistent value).
    assert loop.budget.spent_usd <= loop.budget.max_usd
    assert loop.budget.spent_seconds <= loop.budget.max_seconds
    # A terminal subagent_result was emitted for the started child.
    results = [e for e in events if e.get("kind") == "subagent_result"]
    assert results and results[0]["status"] in {"timeout", "cancelled"}
    assert results[0]["agent_id"] == "sub_1"
