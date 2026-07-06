from __future__ import annotations

from types import SimpleNamespace

import pytest

from gemia.engine import PlanEngine
from gemia.errors import UserInputError


def test_two_input_video_router_does_not_duplicate_explicit_input_a(tmp_path):
    calls: list[tuple[str, str, str, float]] = []

    def transition_dissolve(input_a: str, input_b: str, output_path: str, *, duration_sec: float) -> str:
        calls.append((input_a, input_b, output_path, duration_sec))
        return output_path

    engine = PlanEngine(root_dir=tmp_path)
    out = engine._call_video_func(
        transition_dissolve,
        SimpleNamespace(name="transition_dissolve"),
        "pipeline-current.mp4",
        "out.mp4",
        {"input_a": "clip-a.mp4", "input_b": "clip-b.mp4", "duration_sec": 0.4},
    )

    assert out == "out.mp4"
    assert calls == [("clip-a.mp4", "clip-b.mp4", "out.mp4", 0.4)]


def test_two_input_video_router_defaults_input_a_to_pipeline_input(tmp_path):
    calls: list[tuple[str, str, str]] = []

    def transition_wipe(input_a: str, input_b: str, output_path: str, *, duration_sec: float) -> str:
        calls.append((input_a, input_b, output_path))
        return output_path

    engine = PlanEngine(root_dir=tmp_path)
    out = engine._call_video_func(
        transition_wipe,
        SimpleNamespace(name="transition_wipe"),
        "pipeline-current.mp4",
        "out.mp4",
        {"input_b": "clip-b.mp4", "duration_sec": 0.5},
    )

    assert out == "out.mp4"
    assert calls == [("pipeline-current.mp4", "clip-b.mp4", "out.mp4")]


def test_two_input_video_router_accepts_input_path_pairs_and_none_return(tmp_path):
    calls: list[tuple[str, str, str]] = []

    def video_hstack(input_a: str, input_b: str, output_path: str) -> None:
        calls.append((input_a, input_b, output_path))

    engine = PlanEngine(root_dir=tmp_path)
    out = engine._call_video_func(
        video_hstack,
        SimpleNamespace(name="video_hstack"),
        ["clip-a.mp4", "clip-b.mp4"],
        "out.mp4",
        {},
    )

    assert out == "out.mp4"
    assert calls == [("clip-a.mp4", "clip-b.mp4", "out.mp4")]


def test_two_input_video_router_requires_second_input(tmp_path):
    def transition_dissolve(input_a: str, input_b: str, output_path: str, *, duration_sec: float) -> str:
        return output_path

    engine = PlanEngine(root_dir=tmp_path)

    with pytest.raises(UserInputError, match="input_a 和 input_b"):
        engine._call_video_func(
            transition_dissolve,
            SimpleNamespace(name="transition_dissolve"),
            "pipeline-current.mp4",
            "out.mp4",
            {"duration_sec": 0.4},
        )


def test_generate_broll_router_uses_script_text_without_pipeline_input(tmp_path):
    calls: list[tuple[str, str, dict]] = []

    def generate_broll(script_text: str, output_dir: str, **kwargs):
        calls.append((script_text, output_dir, kwargs))
        return [f"{output_dir}/broll_city.mp4"]

    engine = PlanEngine(root_dir=tmp_path)
    out = engine._call_video_func(
        generate_broll,
        SimpleNamespace(name="generate_broll"),
        "pipeline-current.mp4",
        str(tmp_path / "out.mp4"),
        {"script_text": "city night b-roll", "style": "cinematic", "duration": 3},
    )

    assert out == [str(tmp_path / "out" / "broll_city.mp4")]
    assert calls == [("city night b-roll", str(tmp_path / "out"), {"style": "cinematic"})]


def test_generate_broll_router_accepts_prompt_alias(tmp_path):
    calls: list[tuple[str, str]] = []

    def generate_broll(script_text: str, output_dir: str, **kwargs):
        calls.append((script_text, output_dir))
        return []

    engine = PlanEngine(root_dir=tmp_path)
    engine._call_video_func(
        generate_broll,
        SimpleNamespace(name="generate_broll"),
        "",
        str(tmp_path / "step_1.mp4"),
        {"prompt": "coffee shop morning"},
    )

    assert calls == [("coffee shop morning", str(tmp_path / "step_1"))]


def test_plan_engine_resolves_nested_step_references(tmp_path):
    engine = PlanEngine(root_dir=tmp_path)

    resolved = engine._resolve_ref(
        ["$input", {"insert": "$step_1"}],
        {"$input": "first.mp4", "$step_1": "stock.mp4"},
    )

    assert resolved == ["first.mp4", {"insert": "stock.mp4"}]


def test_plan_engine_implicit_input_skips_non_media_artifact_outputs(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake")
    dev_brief = tmp_path / "brief.lumeri-dev-brief.md"
    calls: list[tuple[str, str]] = []

    def fake_execute(self, fqn, args, input_val, output_path):
        calls.append((fqn, input_val))
        if fqn == "gemia.video.creative_runtime.write_development_patch_brief":
            dev_brief.write_text("# brief\n", encoding="utf-8")
            return str(dev_brief)
        return output_path

    monkeypatch.setattr("gemia.engine.PlanEngine._execute_step", fake_execute)
    engine = PlanEngine(root_dir=tmp_path)
    plan = {
        "version": "2.1",
        "steps": [
            {
                "id": "step_1",
                "function": "gemia.video.creative_runtime.write_development_patch_brief",
                "args": {"feature_request": "add particle text"},
            },
            {
                "id": "step_2",
                "function": "gemia.video.layer_flow.render_layer_workflow",
                "args": {"title": "Acrab"},
                "output": "$output",
            },
        ],
    }

    result = engine.execute(plan, str(source), str(tmp_path / "final.mp4"))

    assert result == str(tmp_path / "final.mp4")
    assert calls == [
        ("gemia.video.creative_runtime.write_development_patch_brief", str(source)),
        ("gemia.video.layer_flow.render_layer_workflow", str(source)),
    ]


def test_plan_engine_explicit_video_input_skips_non_media_artifact(monkeypatch, tmp_path):
    dev_brief = tmp_path / "brief.lumeri-dev-brief.md"
    calls: list[tuple[str, str]] = []

    def fake_get_info(fqn):
        if fqn == "gemia.video.layer_flow.render_layer_workflow":
            return SimpleNamespace(domain="video")
        return SimpleNamespace(domain="video")

    def fake_execute(self, fqn, args, input_val, output_path):
        calls.append((fqn, input_val))
        if fqn == "gemia.video.creative_runtime.write_development_patch_brief":
            dev_brief.write_text("# brief\n", encoding="utf-8")
            return str(dev_brief)
        return output_path

    monkeypatch.setattr("gemia.engine.get_info", fake_get_info)
    monkeypatch.setattr("gemia.engine.PlanEngine._execute_step", fake_execute)
    engine = PlanEngine(root_dir=tmp_path)
    plan = {
        "version": "2.1",
        "steps": [
            {
                "id": "step_1",
                "function": "gemia.video.creative_runtime.write_development_patch_brief",
                "args": {"feature_request": "add particle text"},
            },
            {
                "id": "step_2",
                "function": "gemia.video.layer_flow.render_layer_workflow",
                "args": {"title": "Acrab"},
                "input": "$step_1",
                "output": "$output",
            },
        ],
    }

    result = engine.execute(plan, "", str(tmp_path / "final.mp4"))

    assert result == str(tmp_path / "final.mp4")
    assert calls == [
        ("gemia.video.creative_runtime.write_development_patch_brief", ""),
        ("gemia.video.layer_flow.render_layer_workflow", ""),
    ]


def test_plan_engine_normalizes_planner_contract_before_execution(monkeypatch, tmp_path):
    source_a = tmp_path / "a.mp4"
    source_b = tmp_path / "b.mp4"
    source_a.write_bytes(b"fake-a")
    source_b.write_bytes(b"fake-b")
    calls: list[tuple[str, dict, list[str], str]] = []

    def fake_execute(self, fqn, args, input_val, output_path):
        calls.append((fqn, args, input_val, output_path))
        return output_path

    monkeypatch.setattr("gemia.engine.PlanEngine._execute_step", fake_execute)
    engine = PlanEngine(root_dir=tmp_path)
    plan = {
        "version": "2.1",
        "steps": [
            {
                "id": "step_1",
                "function": "gemia.video.transitions.transition_dissolve",
                "args": {
                    "input_a": str(source_a),
                    "input_b": str(source_b),
                    "output_path": str(tmp_path / "final.mp4"),
                    "duration": "{duration|0.5}",
                    "unused": "drop me",
                },
            }
        ],
    }

    result = engine.execute(plan, str(source_a), str(tmp_path / "final.mp4"))

    assert result == str(tmp_path / "final.mp4")
    assert calls == [
        (
            "gemia.video.transitions.transition_dissolve",
            {"duration_sec": 0.5},
            [str(source_a), str(source_b)],
            str(tmp_path / "final.mp4"),
        )
    ]
