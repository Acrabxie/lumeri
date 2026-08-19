"""Budget reservation / settlement API (docs/multi-agent-plan.md §5, WP1).

Ports the reservation-math assertions from the stranded RC3 parallel-dispatch
branch (d7f941c) onto the current BudgetGuard, plus the new amount-based
reserve_amount used by the subtask fan-out. Covers:
- reserve() adds estimates up front (so N concurrent launches can't all pass a
  stale check), settle returns the delta, and an unspent reservation credits
  the totals back down;
- reserve_amount() with the same atomic check-then-add semantics, plus refusal
  when a slice would breach either cap;
- existing commit() behavior stays intact.
"""
from __future__ import annotations

from gemia.budget_guard import BudgetGuard, BudgetReservation


# ── ported d7f941c reservation math ──────────────────────────────────────────


def test_reserve_adds_estimate_up_front() -> None:
    """reserve() must add the estimate to spent_* immediately so a second
    concurrent reserve sees the first one's claim (no stale-total overrun)."""
    g = BudgetGuard(max_usd=5.0, max_seconds=600.0)
    decision, res = g.reserve("generate_image")  # $0.101 / 10s
    assert decision.ok
    assert isinstance(res, BudgetReservation)
    assert res.tool_name == "generate_image"
    assert g.spent_usd == res.estimated_cost_usd == 0.101
    assert g.spent_seconds == res.estimated_eta_sec == 10.0


def test_reserve_refuses_over_cap_and_reserves_nothing() -> None:
    g = BudgetGuard(max_usd=0.05, max_seconds=600.0)
    decision, res = g.reserve("generate_image")  # $0.101 > $0.05 cap
    assert not decision.ok
    assert res is None
    assert g.spent_usd == 0.0  # nothing claimed on refusal


def test_reserve_prevents_stale_total_overrun() -> None:
    """Three $2.80 video reservations against a $5 cap: only the first can be
    reserved because each reserve() adds its estimate before the next checks."""
    g = BudgetGuard(max_usd=5.0, max_seconds=100_000.0)
    d0, r0 = g.reserve("generate_video")
    d1, r1 = g.reserve("generate_video")
    assert d0.ok and r0 is not None
    assert not d1.ok and r1 is None
    assert g.spent_usd == 2.80


def test_commit_reserved_settles_actual_and_returns_unspent() -> None:
    """settlement is spent += actual - estimated; a lower actual returns the
    unspent slice automatically (negative delta)."""
    g = BudgetGuard(max_usd=5.0, max_seconds=600.0)
    _, res = g.reserve("generate_video")  # reserves $2.80 / 120s
    assert g.spent_usd == 2.80 and g.spent_seconds == 120.0
    # Actual came in cheaper/faster than the estimate.
    g.commit_reserved(res, actual_usd=1.50, actual_seconds=40.0)
    assert round(g.spent_usd, 4) == 1.50
    assert round(g.spent_seconds, 2) == 40.0


def test_commit_reserved_charges_overrun() -> None:
    g = BudgetGuard(max_usd=50.0, max_seconds=600.0)
    _, res = g.reserve("generate_video")  # $2.80 / 120s
    g.commit_reserved(res, actual_usd=4.00, actual_seconds=200.0)
    assert round(g.spent_usd, 4) == 4.00
    assert round(g.spent_seconds, 2) == 200.0


def test_commit_reserved_defaults_to_estimate_when_actual_missing() -> None:
    g = BudgetGuard(max_usd=5.0, max_seconds=600.0)
    _, res = g.reserve("generate_image")  # $0.101 / 10s
    g.commit_reserved(res)  # no actuals → settles at the estimate, net zero delta
    assert round(g.spent_usd, 4) == 0.101
    assert round(g.spent_seconds, 2) == 10.0


# ── new amount-based reservation (subtask slices) ────────────────────────────


def test_reserve_amount_adds_and_returns_reservation() -> None:
    g = BudgetGuard(max_usd=5.0, max_seconds=600.0)
    decision, res = g.reserve_amount("spawn_subtasks:sub_1", usd=1.0, seconds=100.0)
    assert decision.ok and res is not None
    assert res.tool_name == "spawn_subtasks:sub_1"
    assert g.spent_usd == 1.0 and g.spent_seconds == 100.0


def test_reserve_amount_refuses_over_usd_cap() -> None:
    g = BudgetGuard(max_usd=1.0, max_seconds=600.0)
    decision, res = g.reserve_amount("slice", usd=2.0, seconds=10.0)
    assert not decision.ok and res is None
    assert "cost" in decision.reason
    assert g.spent_usd == 0.0 and g.spent_seconds == 0.0


def test_reserve_amount_refuses_over_seconds_cap() -> None:
    g = BudgetGuard(max_usd=5.0, max_seconds=100.0)
    decision, res = g.reserve_amount("slice", usd=0.0, seconds=200.0)
    assert not decision.ok and res is None
    assert "time" in decision.reason
    assert g.spent_seconds == 0.0


def test_reserve_amount_slice_roundtrip_returns_unspent() -> None:
    """The subtask flow: reserve a slice, the child spends less, settle → the
    session totals credit back exactly the unspent portion."""
    g = BudgetGuard(max_usd=5.0, max_seconds=600.0)
    _, res = g.reserve_amount("spawn_subtasks:sub_1", usd=2.0, seconds=200.0)
    assert g.spent_usd == 2.0 and g.spent_seconds == 200.0
    # Child actually spent only a fraction of its slice.
    g.commit_reserved(res, actual_usd=0.0, actual_seconds=71.2)
    assert round(g.spent_usd, 4) == 0.0
    assert round(g.spent_seconds, 2) == 71.2


def test_two_slices_sum_and_settle_independently() -> None:
    g = BudgetGuard(max_usd=5.0, max_seconds=600.0)
    _, r1 = g.reserve_amount("sub_1", usd=1.0, seconds=100.0)
    _, r2 = g.reserve_amount("sub_2", usd=1.0, seconds=100.0)
    assert g.spent_usd == 2.0 and g.spent_seconds == 200.0
    g.commit_reserved(r1, actual_usd=0.5, actual_seconds=40.0)
    g.commit_reserved(r2, actual_usd=0.5, actual_seconds=60.0)
    assert round(g.spent_usd, 4) == 1.0
    assert round(g.spent_seconds, 2) == 100.0


# ── existing behavior intact ─────────────────────────────────────────────────


def test_plain_commit_still_uses_estimate_or_actual() -> None:
    g = BudgetGuard(max_usd=5.0, max_seconds=600.0)
    g.commit("generate_image")  # estimate path
    assert round(g.spent_usd, 4) == 0.101
    g.commit("analyze_media", actual_seconds=2.0)  # actual path
    assert round(g.spent_seconds, 2) == 12.0  # 10.0 (image eta) + 2.0


def test_spawn_subtasks_cost_row_is_near_free() -> None:
    g = BudgetGuard(max_usd=5.0, max_seconds=600.0)
    usd, eta = g.estimate("spawn_subtasks")
    assert usd == 0.0
    assert eta == 1.0


def test_verified_model_usage_is_counted_and_converted_to_real_cost() -> None:
    g = BudgetGuard(max_usd=5.0, max_seconds=600.0)
    settled = g.record_model_usage(
        {
            "prompt_tokens": 100_000,
            "completion_tokens": 10_000,
            "prompt_tokens_details": {"cached_tokens": 20_000},
            "completion_tokens_details": {"reasoning_tokens": 4_000},
        },
        provider="openai",
        model="gpt-5.6-sol",
    )
    assert settled["pricing_source"] == "verified_model_price"
    assert settled["cost_usd"] == 0.71
    snapshot = g.snapshot()
    assert snapshot["input_tokens"] == 100_000
    assert snapshot["cached_input_tokens"] == 20_000
    assert snapshot["output_tokens"] == 10_000
    assert snapshot["reasoning_tokens"] == 4_000
    assert snapshot["model_spent_usd"] == 0.71
    assert snapshot["tool_spent_usd"] == 0.0


def test_provider_billed_cost_wins_and_unknown_model_stays_honest() -> None:
    g = BudgetGuard(max_usd=5.0, max_seconds=600.0)
    settled = g.record_model_usage(
        {"input_tokens": 1200, "output_tokens": 300, "cost": 0.1234},
        provider="openrouter",
        model="vendor/future-model",
    )
    assert settled["pricing_source"] == "provider"
    assert settled["cost_usd"] == 0.1234
    assert g.snapshot()["unpriced_tokens"] == 0

    unpriced = BudgetGuard(max_usd=5.0, max_seconds=600.0)
    settled_unpriced = unpriced.record_model_usage(
        {"prompt_tokens": 100, "completion_tokens": 20},
        provider="custom",
        model="unknown-model",
    )
    assert settled_unpriced["pricing_source"] == "unpriced"
    assert settled_unpriced["cost_usd"] is None
    assert unpriced.snapshot()["unpriced_tokens"] == 120


def test_warning_threshold_emits_once_and_rearms_when_raised() -> None:
    events = []
    g = BudgetGuard(max_usd=5.0, warning_usd=0.10, max_seconds=600.0)
    g.bind_warning_sink(events.append)
    g.commit("generate_image")
    g.commit("generate_image")
    assert len(events) == 1
    assert events[0]["warning_reached"] is True

    g.set_limits(max_usd=5.0, warning_usd=1.0)
    assert len(events) == 1
    g.record_model_usage(
        {"prompt_tokens": 100_000, "completion_tokens": 30_000},
        provider="openai",
        model="gpt-5.6-sol",
    )
    assert len(events) == 2


def test_blank_warning_is_disabled_and_exposed_as_null() -> None:
    events = []
    g = BudgetGuard(max_usd=5.0, warning_usd=None, max_seconds=600.0)
    g.bind_warning_sink(events.append)
    g.commit("generate_image")
    assert events == []
    assert g.snapshot()["warning_usd"] is None
    assert g.snapshot()["warning_reached"] is False


def test_enabling_warning_below_spend_emits_immediately() -> None:
    events = []
    g = BudgetGuard(max_usd=5.0, warning_usd=None, max_seconds=600.0)
    g.bind_warning_sink(events.append)
    g.spent_usd = 1.0
    g.set_limits(max_usd=5.0, warning_usd=0.5)
    assert len(events) == 1
    assert events[0]["warning_usd"] == 0.5
    assert events[0]["warning_reached"] is True


def test_model_preflight_caps_output_to_remaining_real_budget() -> None:
    g = BudgetGuard(max_usd=0.05, max_seconds=600.0)
    allowance = g.prepare_model_call(
        [{"role": "user", "content": "hello"}],
        model="gpt-5.6-sol",
        tools=[],
    )
    assert allowance["ok"] is True
    assert 1 <= allowance["max_output_tokens"] < 8096

    g.spent_usd = g.max_usd
    blocked = g.prepare_model_call([], model="gpt-5.6-sol")
    assert blocked["ok"] is False
