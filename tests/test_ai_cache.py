"""Tests for the in-memory plan cache layer."""
from __future__ import annotations

import time

import pytest

from gemia.ai.cache import PlanCache, make_plan_key, plan_cache


class TestPlanCacheBasics:
    def test_set_and_get_returns_value(self) -> None:
        c = PlanCache(ttl_sec=60, max_entries=4, enabled=True)
        c.set("k1", {"version": "2.0", "steps": []})
        out = c.get("k1")
        assert out is not None
        assert out["version"] == "2.0"

    def test_miss_returns_none(self) -> None:
        c = PlanCache(ttl_sec=60, max_entries=4, enabled=True)
        assert c.get("missing") is None
        assert c.stats()["misses"] == 1

    def test_disabled_cache_never_stores(self) -> None:
        c = PlanCache(ttl_sec=60, max_entries=4, enabled=False)
        c.set("k1", {"steps": []})
        assert c.get("k1") is None

    def test_ttl_expiry(self) -> None:
        c = PlanCache(ttl_sec=0.05, max_entries=4, enabled=True)
        c.set("k1", {"steps": [1]})
        time.sleep(0.1)
        assert c.get("k1") is None

    def test_lru_eviction(self) -> None:
        c = PlanCache(ttl_sec=60, max_entries=2, enabled=True)
        c.set("a", {"v": 1})
        c.set("b", {"v": 2})
        # Touching 'a' makes it most-recently-used; 'b' should evict on next set.
        assert c.get("a") is not None
        c.set("c", {"v": 3})
        assert c.get("b") is None
        assert c.get("a") is not None
        assert c.get("c") is not None

    def test_get_returns_deepcopy(self) -> None:
        c = PlanCache(ttl_sec=60, max_entries=4, enabled=True)
        original = {"steps": [{"id": "s1"}]}
        c.set("k", original)
        cached = c.get("k")
        assert cached is not None
        cached["steps"][0]["id"] = "MUTATED"
        # Mutation should not leak back.
        assert c.get("k")["steps"][0]["id"] == "s1"


class TestMakePlanKey:
    def test_same_inputs_produce_same_key(self) -> None:
        a = make_plan_key(
            backend="gemini_native",
            model="gemini-3.1-pro-preview",
            request="warm grade",
            input_path="/tmp/a.mp4",
            output_path="/tmp/out.mp4",
            answers={"q1": "warm"},
            project_state=None,
        )
        b = make_plan_key(
            backend="gemini_native",
            model="gemini-3.1-pro-preview",
            request="warm grade",
            input_path="/tmp/a.mp4",
            output_path="/tmp/out.mp4",
            answers={"q1": "warm"},
            project_state=None,
        )
        assert a == b

    def test_answer_order_does_not_matter(self) -> None:
        a = make_plan_key(
            backend="gemini_native", model="m", request="r",
            input_path="/i", output_path="/o",
            answers={"q1": "1", "q2": "2"}, project_state=None,
        )
        b = make_plan_key(
            backend="gemini_native", model="m", request="r",
            input_path="/i", output_path="/o",
            answers={"q2": "2", "q1": "1"}, project_state=None,
        )
        assert a == b

    def test_different_request_changes_key(self) -> None:
        a = make_plan_key(
            backend="gemini_native", model="m", request="warm grade",
            input_path="/i", output_path="/o", answers=None, project_state=None,
        )
        b = make_plan_key(
            backend="gemini_native", model="m", request="cool grade",
            input_path="/i", output_path="/o", answers=None, project_state=None,
        )
        assert a != b


class TestModuleSingleton:
    def test_singleton_clear_resets_state(self) -> None:
        plan_cache.clear()
        plan_cache.set("test_singleton_key", {"steps": []})
        assert plan_cache.get("test_singleton_key") is not None
        plan_cache.clear()
        assert plan_cache.get("test_singleton_key") is None
