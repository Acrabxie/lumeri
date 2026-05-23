from __future__ import annotations

from gemia.ai.clarification_policy import (
    build_clarification_policy,
    maybe_default_plan_for_ask,
)
from gemia.ai.primitive_specs import primitive_specs_for_skills


def test_policy_marks_defaultable_local_primitives() -> None:
    specs = primitive_specs_for_skills(["color-grade"])
    policy = build_clarification_policy(["color-grade"], specs)

    assert policy["mode"] == "default_first"
    assert policy["has_defaultable_executable_primitive"] is True
    assert "gemia.picture.color.color_grade" in policy["defaultable_primitives"]
    assert "style, intensity, duration" in policy["do_not_ask_for"][0]


def test_defaultable_ask_becomes_single_step_default_plan() -> None:
    specs = primitive_specs_for_skills(["color-grade"])
    plan = maybe_default_plan_for_ask(
        {"ask": True, "questions": [{"id": "style", "text": "你想要什么风格和强度？"}]},
        effective_request="暖色调色",
        selected_skills=["color-grade"],
        active_specs=specs,
    )

    assert plan is not None
    assert "ask" not in plan
    assert plan["steps"][0]["function"] == "gemia.picture.color.color_grade"
    assert plan["steps"][0]["input"] == "$input"


def test_stock_media_query_still_asks_when_required() -> None:
    specs = primitive_specs_for_skills(["stock-media"])
    plan = maybe_default_plan_for_ask(
        {"ask": True, "questions": [{"id": "query", "text": "你要搜索什么素材？"}]},
        effective_request="去 Pexels 搜索素材",
        selected_skills=["stock-media"],
        active_specs=specs,
    )

    assert plan is None


def test_transition_defaults_when_project_has_two_sources(tmp_path) -> None:
    source_a = tmp_path / "a.mp4"
    source_b = tmp_path / "b.mp4"
    source_a.write_bytes(b"a")
    source_b.write_bytes(b"b")
    specs = primitive_specs_for_skills(["transition"])

    plan = maybe_default_plan_for_ask(
        {"ask": True, "questions": [{"id": "type", "text": "使用哪种转场类型和时长？"}]},
        effective_request="加转场",
        selected_skills=["transition"],
        active_specs=specs,
        project_state={
            "clips": [
                {"serverPath": str(source_a)},
                {"serverPath": str(source_b)},
            ]
        },
    )

    assert plan is not None
    assert plan["steps"][0]["function"] == "gemia.video.transitions.transition_dissolve"
    assert plan["steps"][0]["args"] == {"duration_sec": 0.5}
    assert plan["steps"][0]["input"] == [str(source_a), str(source_b)]
