from __future__ import annotations

import pytest

from gemia.plan_contract import PlanContractError, normalize_plan_for_execution


def test_plan_contract_resolves_placeholders_and_moves_transition_inputs() -> None:
    plan = {
        "version": "2.1",
        "steps": [
            {
                "id": "step_1",
                "function": "gemia.video.transitions.transition_dissolve",
                "args": {
                    "input_a": "/tmp/a.mp4",
                    "input_b": "/tmp/b.mp4",
                    "output_path": "/tmp/out.mp4",
                    "duration": "{duration|0.5}",
                    "unused": "drop me",
                },
            }
        ],
    }

    normalized = normalize_plan_for_execution(plan, output_path="/tmp/out.mp4")
    step = normalized["steps"][0]

    assert step["input"] == ["/tmp/a.mp4", "/tmp/b.mp4"]
    assert step["output"] == "$output"
    assert step["args"] == {"duration_sec": 0.5}
    warnings = normalized["plan_contract"]["warnings"]
    assert any(item["kind"] == "moved_media_args" for item in warnings)
    assert any(item["kind"] == "renamed_arg" and item["to"] == "duration_sec" for item in warnings)
    assert any(item["kind"] == "dropped_unsupported_args" for item in warnings)


def test_plan_contract_moves_single_input_path_out_of_args() -> None:
    plan = {
        "steps": [
            {
                "id": "step_1",
                "function": "gemia.video.timeline.cut",
                "args": {
                    "input_path": "/tmp/source.mp4",
                    "start": "{start|0}",
                    "end": "{end|1.5}",
                },
                "output": "$output",
            }
        ],
    }

    normalized = normalize_plan_for_execution(plan)
    step = normalized["steps"][0]

    assert step["input"] == "/tmp/source.mp4"
    assert step["args"] == {"start_sec": 0, "end_sec": 1.5}


def test_plan_contract_rejects_unresolved_placeholders() -> None:
    plan = {
        "steps": [
            {
                "id": "step_1",
                "function": "gemia.video.timeline.speed",
                "args": {"factor": "{speed}"},
            }
        ],
    }

    with pytest.raises(PlanContractError, match="模板占位符"):
        normalize_plan_for_execution(plan)


def test_plan_contract_rejects_unknown_primitive_in_strict_mode() -> None:
    plan = {
        "steps": [
            {
                "id": "step_1",
                "function": "gemia.video.blender_link.render_3d_scene",
                "args": {},
            }
        ],
    }

    with pytest.raises(PlanContractError, match="还没有注册"):
        normalize_plan_for_execution(plan)


def test_plan_contract_rejects_inactive_primitive_when_active_specs_are_supplied() -> None:
    plan = {
        "steps": [
            {
                "id": "step_1",
                "function": "gemia.video.blender_link.render_blender_link_operation",
                "args": {},
            }
        ],
    }

    with pytest.raises(PlanContractError, match="没有激活"):
        normalize_plan_for_execution(
            plan,
            active_specs=[{"name": "gemia.picture.color.color_grade"}],
        )


def test_plan_contract_resolves_legacy_function_aliases() -> None:
    plan = {
        "steps": [
            {
                "id": "step_1",
                "function": "gemia.video.effects.trim_clip",
                "args": {"start": 0, "end": 1.25},
            },
            {
                "id": "step_2",
                "function": "gemia.video.effects.color_grade",
                "args": {"preset": "cool"},
                "input": "$step_1",
            },
        ],
    }

    normalized = normalize_plan_for_execution(
        plan,
        active_specs=[
            {"name": "gemia.video.timeline.cut"},
            {"name": "gemia.picture.color.color_grade"},
        ],
    )

    assert normalized["steps"][0]["function"] == "gemia.video.timeline.cut"
    assert normalized["steps"][0]["args"] == {"start_sec": 0, "end_sec": 1.25}
    assert normalized["steps"][1]["function"] == "gemia.picture.color.color_grade"
    renamed = [item for item in normalized["plan_contract"]["warnings"] if item["kind"] == "renamed_function"]
    assert renamed == [
        {
            "step_id": "step_1",
            "kind": "renamed_function",
            "from": "gemia.video.effects.trim_clip",
            "to": "gemia.video.timeline.cut",
        },
        {
            "step_id": "step_2",
            "kind": "renamed_function",
            "from": "gemia.video.effects.color_grade",
            "to": "gemia.picture.color.color_grade",
        },
    ]


def test_plan_contract_resolves_transition_alias_before_active_spec_check() -> None:
    plan = {
        "steps": [
            {
                "id": "step_1",
                "function": "gemia.video.transition.crossfade",
                "args": {"input_a": "a.mp4", "input_b": "b.mp4", "duration": "{duration|0.5}"},
            }
        ],
    }

    normalized = normalize_plan_for_execution(
        plan,
        active_specs=[{"name": "gemia.video.transitions.transition_dissolve"}],
    )

    assert normalized["steps"][0]["function"] == "gemia.video.transitions.transition_dissolve"
    assert normalized["steps"][0]["input"] == ["a.mp4", "b.mp4"]
    assert normalized["steps"][0]["args"] == {"duration_sec": 0.5}
