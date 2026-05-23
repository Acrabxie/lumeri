from __future__ import annotations

import asyncio

from gemia.ai.ai_client import AIClient


class _SkillCaptureAdapter:
    provider = "openrouter"
    model = "google/gemini-3.1-pro-preview"

    def __init__(self) -> None:
        self.static_system_prompt = ""
        self.dynamic_system_prompt = ""
        self.payload = {}

    def can_read_video(self, _input_path: str) -> bool:
        return False

    async def generate_plan_json(
        self,
        system_prompt,
        user_payload,
        tag,
        *,
        attach_video=False,
        dynamic_system_prompt=None,
    ):
        self.static_system_prompt = system_prompt
        self.dynamic_system_prompt = dynamic_system_prompt or ""
        self.payload = user_payload
        return {
            "version": "2.0",
            "goal": "transition and grade",
            "steps": [
                {
                    "id": "step_1",
                    "function": "gemia.video.transitions.transition_dissolve",
                    "args": {"duration": 0.5},
                    "input": "$input",
                },
                {
                    "id": "step_2",
                    "function": "gemia.picture.color.color_grade",
                    "args": {"preset": "cool"},
                    "input": "$step_1",
                    "output": "$output",
                },
            ],
        }


class _AskCaptureAdapter(_SkillCaptureAdapter):
    async def generate_plan_json(
        self,
        system_prompt,
        user_payload,
        tag,
        *,
        attach_video=False,
        dynamic_system_prompt=None,
    ):
        self.static_system_prompt = system_prompt
        self.dynamic_system_prompt = dynamic_system_prompt or ""
        self.payload = user_payload
        return {
            "ask": True,
            "questions": [
                {"id": "style", "text": "你想要什么风格、强度和持续时间？", "input_type": "text"}
            ],
        }


def test_ai_client_uses_skill_router_and_stripped_payload() -> None:
    adapter = _SkillCaptureAdapter()
    client = AIClient(adapter=adapter)  # type: ignore[arg-type]

    plan = asyncio.run(
        client.plan_from_primitives(
            "加转场并做冷色调色",
            input_path="/tmp/source.mp4",
            output_path="/tmp/out.mp4",
            project_state={
                "clips": [
                    {
                        "id": "clip1",
                        "mediaKind": "video",
                        "serverPath": "/tmp/source.mp4",
                        "thumbnailStrip": ["x"] * 100,
                        "waveformPeaks": [0.1] * 1000,
                    }
                ]
            },
        )
    )

    assert plan["version"] == "2.0"
    assert adapter.payload["planning_mode"]["planner"] == "skills"
    assert adapter.payload["planning_mode"]["selected_skills"] == ["color-grade", "transition"]
    assert adapter.payload["runtime_envelope_version"] == "agent-runtime-0.1"
    assert adapter.payload["runtime_policy"]["plan_schema"] == "2.1"
    assert "active_primitive_specs" in adapter.payload
    selected_primitives = adapter.payload["planning_mode"]["selected_primitives"]
    assert "gemia.video.transitions.transition_dissolve" in selected_primitives
    assert "gemia.picture.color.color_grade" in selected_primitives
    transition_spec = next(
        spec for spec in adapter.payload["active_primitive_specs"]
        if spec["name"] == "gemia.video.transitions.transition_dissolve"
    )
    assert "input_a" not in transition_spec["args_schema"]["properties"]
    assert "output_path" not in transition_spec["args_schema"]["properties"]
    assert adapter.payload["planning_prompt_budget"]["total_tokens_est"] <= 8000
    assert adapter.payload["planning_prompt_budget"]["active_specs_tokens_est"] > 0
    assert "Skill index:" in adapter.static_system_prompt
    assert "Plan v2.1 schema" in adapter.static_system_prompt
    assert "payload.active_primitive_specs" in adapter.static_system_prompt
    assert "transition+color-grade" in adapter.dynamic_system_prompt
    assert "thumbnailStrip" not in adapter.payload["project_state"]["clips"][0]
    assert "waveformPeaks" not in adapter.payload["project_state"]["clips"][0]


def test_ai_client_defaults_low_risk_asks_instead_of_returning_ask() -> None:
    adapter = _AskCaptureAdapter()
    client = AIClient(adapter=adapter)  # type: ignore[arg-type]

    plan = asyncio.run(
        client.plan_from_primitives(
            "暖色调色，稍微提亮",
            input_path="/tmp/source.mp4",
            output_path="/tmp/out.mp4",
            project_state={"clips": [{"id": "clip1", "mediaKind": "video", "serverPath": "/tmp/source.mp4"}]},
        )
    )

    assert "ask" not in plan
    assert plan["version"] == "2.1"
    assert plan["steps"][0]["function"] == "gemia.picture.color.color_grade"
    assert adapter.payload["clarification_policy"]["mode"] == "default_first"
    assert adapter.payload["clarification_policy"]["has_defaultable_executable_primitive"] is True
    assert "gemia.picture.color.color_grade" in adapter.payload["clarification_policy"]["defaultable_primitives"]


class _MessyPlanAdapter(_SkillCaptureAdapter):
    async def generate_plan_json(
        self,
        system_prompt,
        user_payload,
        tag,
        *,
        attach_video=False,
        dynamic_system_prompt=None,
    ):
        self.static_system_prompt = system_prompt
        self.dynamic_system_prompt = dynamic_system_prompt or ""
        self.payload = user_payload
        return {
            "version": "2.1",
            "goal": "transition",
            "steps": [
                {
                    "id": "step_1",
                    "function": "gemia.video.transitions.transition_dissolve",
                    "args": {
                        "input_a": "/tmp/a.mp4",
                        "input_b": "/tmp/b.mp4",
                        "output_path": "/tmp/out.mp4",
                        "duration": "{duration|0.5}",
                    },
                }
            ],
        }


def test_ai_client_normalizes_plan_contract_before_returning() -> None:
    adapter = _MessyPlanAdapter()
    client = AIClient(adapter=adapter)  # type: ignore[arg-type]

    plan = asyncio.run(
        client.plan_from_primitives(
            "加一个溶解转场",
            input_path="/tmp/a.mp4",
            output_path="/tmp/out.mp4",
            project_state={
                "clips": [
                    {"id": "a", "mediaKind": "video", "serverPath": "/tmp/a.mp4"},
                    {"id": "b", "mediaKind": "video", "serverPath": "/tmp/b.mp4"},
                ]
            },
        )
    )

    step = plan["steps"][0]
    assert step["input"] == ["/tmp/a.mp4", "/tmp/b.mp4"]
    assert step["output"] == "$output"
    assert step["args"] == {"duration_sec": 0.5}
