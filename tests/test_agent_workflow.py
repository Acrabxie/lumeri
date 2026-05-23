from __future__ import annotations

import sys
import json
from types import ModuleType
from pathlib import Path

import pytest

from gemia.agent_workflow import (
    build_agent_context,
    collect_materials,
    read_material,
    run_agent_workflow,
    select_target_materials,
    should_include_media_library,
    wants_all_materials,
    _requested_total_duration_sec,
    _requested_segment_durations_sec,
    _prompt_requests_hard_cut,
    _requested_timeline_broll_clip_transforms,
    _render_timeline_broll_preview,
    _render_timeline_kept_clip_merge,
)
from gemia.plan_contract import PlanContractError
from gemia.project_model import IMAGE_DURATION


def test_collect_materials_dedupes_timeline_and_library(monkeypatch, tmp_path: Path) -> None:
    timeline_path = tmp_path / "clip.mp4"
    library_path = tmp_path / "audio.wav"

    project_state = {
        "clips": [
            {
                "id": "clip_1",
                "assetId": "asset_same",
                "name": "Timeline Clip",
                "serverPath": str(timeline_path),
                "mediaKind": "video",
                "duration": 2.0,
            }
        ],
        "selectedClipId": "clip_1",
    }

    def fake_list_assets(account_id: str, limit: int = 300):
        assert account_id == "acct_1"
        assert limit == 300
        return [
            {
                "asset_id": "asset_same",
                "name": "Duplicate Library Clip",
                "source_path": str(timeline_path),
                "media_kind": "video",
            },
            {
                "asset_id": "asset_audio",
                "name": "Library Audio",
                "source_path": str(library_path),
                "media_kind": "audio",
            },
        ]

    fake_module = ModuleType("gemia.media_library")
    fake_module.list_assets = fake_list_assets  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gemia.media_library", fake_module)

    materials = collect_materials(project_state, account_id="acct_1")

    assert [item["asset_id"] for item in materials] == ["asset_same", "asset_audio"]
    assert materials[0]["origin"] == "timeline"
    assert materials[0]["selected"] is True
    assert materials[1]["origin"] == "library"


def test_media_library_requires_explicit_library_intent() -> None:
    assert should_include_media_library("对素材池所有素材做锐化", "auto") is True
    assert should_include_media_library("Use the media library clips", "auto") is True
    assert should_include_media_library("只用 public-a.mp4 和 public-b.mp4 剪成 3 秒硬切小片", "auto") is True
    assert should_include_media_library(
        "当前时间线如果为空，请不要从媒体素材池/历史会话/library/旧会话替代任何素材。"
        "不要使用 public-a.mp4，也不要使用 public-b.mp4。",
        "auto",
    ) is False
    assert should_include_media_library("创建 3 秒 prompt-only MG 标题动画，不要外部素材", "auto") is False
    assert should_include_media_library("创建 3 秒空画布标题动画", "auto") is False
    assert should_include_media_library("创建任意动画", "library") is True


def test_agent_workflow_needs_input_for_empty_current_timeline_no_library_fallback(
    monkeypatch, tmp_path: Path
) -> None:
    fake_module = ModuleType("gemia.media_library")

    def fail_list_assets(*args, **kwargs):
        raise AssertionError("empty current-timeline prompt must not read the media library")

    fake_module.list_assets = fail_list_assets  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gemia.media_library", fake_module)

    class FakeOrch:
        outputs_dir = tmp_path

        def plan_from_primitives(self, *args, **kwargs) -> dict:
            raise AssertionError("empty current-timeline prompt should ask for media before planning")

    events: list[dict] = []
    prompt = (
        "Round38 新会话空时间线测试：当前时间线如果为空，请不要从媒体素材池/历史会话/library/旧会话替代任何素材。"
        "不要使用 01_pair_of_soccer_courts_games_from_above_mixkit_41371_720p.mp4，"
        "也不要使用 ui-replace-synthetic-blue-orbit.mp4。如果当前时间线没有媒体，请明确提示需要先添加媒体，不要生成 MP4。"
    )
    result = run_agent_workflow(
        FakeOrch(),
        prompt=prompt,
        input_path=None,
        account_id="acct_1",
        project_state={"clips": [], "selectedClipId": ""},
        event_callback=events.append,
    )

    assert result["status"] == "needs_input"
    assert result["outputs"] == []
    assert result["targets"] == []
    assert "当前时间线没有媒体" in result["pending_ask"]["questions"][0]["text"]
    assert any(
        event.get("phase") == "ask"
        and event.get("status") == "asking"
        and "当前时间线" in event.get("body", "")
        for event in events
    )


def test_read_material_uses_local_metadata_fallback_for_unprobeable_image(monkeypatch, tmp_path: Path) -> None:
    image_path = tmp_path / "still.png"
    image_path.write_bytes(b"not a real image")
    monkeypatch.setattr("gemia.agent_workflow.probe_media", lambda path: (_ for _ in ()).throw(RuntimeError("bad media")))

    material = read_material(
        {
            "material_id": "m1",
            "name": "still.png",
            "source_path": str(image_path),
            "media_kind": "image",
            "metadata": {},
        }
    )

    assert material["read_status"] == "partial"
    assert material["media_kind"] == "image"
    assert material["duration"] == IMAGE_DURATION
    assert material["metadata"]["file_size_bytes"] == len(b"not a real image")


def test_target_selection_prefers_all_scope_then_selected(tmp_path: Path) -> None:
    one = {
        "material_id": "m1",
        "asset_id": "a1",
        "source_path": str(tmp_path / "one.mp4"),
        "selected": True,
    }
    two = {
        "material_id": "m2",
        "asset_id": "a2",
        "source_path": str(tmp_path / "two.mp4"),
        "selected": False,
    }

    assert wants_all_materials("对所有素材执行锐化", "auto") is True
    assert select_target_materials([one, two], prompt="对所有素材执行锐化", scope="auto") == [one, two]
    assert select_target_materials([one, two], prompt="锐化当前素材", scope="auto") == [one]
    assert select_target_materials([one, two], input_path=two["source_path"], prompt="锐化", scope="auto") == [two]
    assert wants_all_materials("使用当前时间线里的两个媒体池视频，剪成一个 3 秒 B-roll", "auto") is True
    assert select_target_materials([one, two], prompt="使用当前时间线里的两个媒体池视频，剪成一个 3 秒 B-roll", scope="auto") == [one, two]


def test_target_selection_honors_explicit_filenames_over_current_timeline(tmp_path: Path) -> None:
    pexels = {
        "material_id": "m1",
        "asset_id": "a1",
        "source_path": str(tmp_path / "pexels-abstract-lights.mp4"),
        "name": "pexels-abstract-lights.mp4",
        "media_kind": "video",
        "origin": "timeline",
        "selected": False,
    }
    mechanical = {
        "material_id": "m2",
        "asset_id": "a2",
        "source_path": str(tmp_path / "174f721de3b2f58d090220f91852a967.mp4"),
        "name": "174f721de3b2f58d090220f91852a967.mp4",
        "media_kind": "video",
        "origin": "timeline",
        "selected": False,
    }
    synthetic = {
        "material_id": "m3",
        "asset_id": "a3",
        "source_path": str(tmp_path / "ui-replace-synthetic-blue-orbit.mp4"),
        "name": "ui-replace-synthetic-blue-orbit.mp4",
        "media_kind": "video",
        "origin": "timeline",
        "selected": True,
    }
    prompt = (
        "基于当前时间线和刚通过更换导入的 ui-replace-synthetic-blue-orbit.mp4，"
        "再加上 174f721de3b2f58d090220f91852a967.mp4，导出严格 3 秒横版本地 MP4。"
    )

    assert wants_all_materials(prompt, "auto") is True
    assert select_target_materials([pexels, mechanical, synthetic], prompt=prompt, scope="auto") == [synthetic, mechanical]


def test_transition_target_selection_uses_adjacent_timeline_pair(tmp_path: Path) -> None:
    one = {
        "material_id": "m1",
        "asset_id": "a1",
        "clip_id": "clip_1",
        "source_path": str(tmp_path / "one.mp4"),
        "media_kind": "video",
        "origin": "timeline",
        "selected": False,
    }
    two = {
        "material_id": "m2",
        "asset_id": "a2",
        "clip_id": "clip_2",
        "source_path": str(tmp_path / "two.mp4"),
        "media_kind": "video",
        "origin": "timeline",
        "selected": True,
    }
    three = {
        "material_id": "m3",
        "asset_id": "a3",
        "clip_id": "clip_3",
        "source_path": str(tmp_path / "three.mp4"),
        "media_kind": "video",
        "origin": "timeline",
        "selected": False,
    }

    assert select_target_materials([one, two, three], prompt="在这里添加转场", scope="auto") == [one, two]
    assert select_target_materials([one, two, three], input_path=two["source_path"], prompt="加一个溶解转场", scope="auto") == [one, two]
    assert select_target_materials([one, two, three], prompt="给所有素材添加转场", scope="auto") == [one, two, three]
    assert select_target_materials([one, two, three], prompt="锐化当前素材", scope="auto") == [two]


def test_agent_workflow_uses_local_timeline_broll_concat_for_two_current_clips(monkeypatch, tmp_path: Path) -> None:
    one = tmp_path / "one.mp4"
    two = tmp_path / "two.mp4"
    one.write_bytes(b"one")
    two.write_bytes(b"two")
    output_seen: dict[str, object] = {}

    def fake_probe(path: str) -> dict:
        return {
            "duration": 6.0 if path == str(one) else 8.0,
            "media_kind": "video",
            "mime_type": "video/mp4",
            "width": 1280,
            "height": 720,
            "fps": 30.0,
            "codec": "h264",
            "audio_codec": "aac",
            "has_audio": True,
            "file_size_bytes": 3,
        }

    def fake_render(targets, *, output_path: str, duration_sec: float, transition_sec: float, **kwargs) -> dict:
        Path(output_path).write_bytes(b"rendered")
        output_seen["targets"] = [item["source_path"] for item in targets]
        output_seen["duration_sec"] = duration_sec
        output_seen["transition_sec"] = transition_sec
        return {
            "kind": "timeline_broll_preview",
            "status": "succeeded",
            "inputs": output_seen["targets"],
            "output": output_path,
            "duration_sec": duration_sec,
            "transition_sec": transition_sec,
        }

    monkeypatch.setattr("gemia.agent_workflow.probe_media", fake_probe)
    monkeypatch.setattr("gemia.agent_workflow._render_timeline_broll_preview", fake_render)

    class FakeOrch:
        outputs_dir = tmp_path

        def plan_from_primitives(self, *args, **kwargs) -> dict:
            raise AssertionError("timeline b-roll concat should not call Gemini planning")

    events: list[dict] = []
    result = run_agent_workflow(
        FakeOrch(),
        prompt="使用当前时间线里的两个媒体池视频，剪成一个 3 秒横版 B-roll，小片中间柔和叠化，不要添加字幕或标题。",
        input_path=None,
        project_state={
            "clips": [
                {
                    "id": "clip_1",
                    "assetId": "asset_1",
                    "name": "one.mp4",
                    "serverPath": str(one),
                    "mediaKind": "video",
                    "duration": 6.0,
                },
                {
                    "id": "clip_2",
                    "assetId": "asset_2",
                    "name": "two.mp4",
                    "serverPath": str(two),
                    "mediaKind": "video",
                    "duration": 8.0,
                },
            ],
            "selectedClipId": "clip_2",
        },
        event_callback=events.append,
    )

    assert output_seen["targets"] == [str(one), str(two)]
    assert output_seen["duration_sec"] == 3.0
    assert result["targets"][0]["clip_id"] == "clip_1"
    assert result["targets"][1]["clip_id"] == "clip_2"
    assert result["render_passes"][0]["kind"] == "timeline_broll_preview"
    assert result["agent_plan"][0]["plan"]["steps"][0]["args"]["no_text_overlay"] is True
    assert any(event.get("label") == "规划时间线拼接" for event in events)
    assert any(event.get("command") == "timeline_broll_concat" for event in events)


def test_agent_workflow_timeline_broll_preserves_explicit_filename_order(monkeypatch, tmp_path: Path) -> None:
    pexels = tmp_path / "pexels-abstract-lights.mp4"
    mechanical = tmp_path / "174f721de3b2f58d090220f91852a967.mp4"
    synthetic = tmp_path / "ui-replace-synthetic-blue-orbit.mp4"
    for path in (pexels, mechanical, synthetic):
        path.write_bytes(b"video")
    output_seen: dict[str, object] = {}

    def fake_probe(path: str) -> dict:
        return {
            "duration": 4.0 if path == str(synthetic) else 6.0,
            "media_kind": "video",
            "mime_type": "video/mp4",
            "width": 1280,
            "height": 720,
            "fps": 30.0,
            "codec": "h264",
            "audio_codec": "",
            "has_audio": False,
            "file_size_bytes": 5,
        }

    def fake_render(targets, *, output_path: str, duration_sec: float, transition_sec: float, prompt: str = "", **kwargs) -> dict:
        Path(output_path).write_bytes(b"rendered")
        output_seen["targets"] = [Path(item["source_path"]).name for item in targets]
        output_seen["duration_sec"] = duration_sec
        output_seen["transition_sec"] = transition_sec
        output_seen["prompt"] = prompt
        return {
            "kind": "timeline_broll_preview",
            "status": "succeeded",
            "inputs": [item["source_path"] for item in targets],
            "output": output_path,
            "duration_sec": duration_sec,
            "transition_sec": transition_sec,
            "clip_count": len(targets),
        }

    monkeypatch.setattr("gemia.agent_workflow.probe_media", fake_probe)
    monkeypatch.setattr("gemia.agent_workflow._render_timeline_broll_preview", fake_render)

    class FakeOrch:
        outputs_dir = tmp_path

        def plan_from_primitives(self, *args, **kwargs) -> dict:
            raise AssertionError("explicit timeline b-roll concat should not call Gemini planning")

    prompt = (
        "基于当前时间线和刚通过更换导入的 ui-replace-synthetic-blue-orbit.mp4，"
        "再加上 174f721de3b2f58d090220f91852a967.mp4，导出严格 3 秒横版本地 MP4："
        "前 1.2 秒使用蓝色几何合成素材，1.2 秒后硬切到机械主体，"
        "后段机械主体至少 1.8 秒；不要叠化、不要交叉淡化、不要残影；无字幕、无标题、无水印。"
    )
    result = run_agent_workflow(
        FakeOrch(),
        prompt=prompt,
        input_path=None,
        project_state={
            "clips": [
                {
                    "id": "clip_1",
                    "assetId": "asset_pexels",
                    "name": pexels.name,
                    "serverPath": str(pexels),
                    "mediaKind": "video",
                    "duration": 6.0,
                },
                {
                    "id": "clip_2",
                    "assetId": "asset_mechanical",
                    "name": mechanical.name,
                    "serverPath": str(mechanical),
                    "mediaKind": "video",
                    "duration": 6.0,
                },
                {
                    "id": "clip_3",
                    "assetId": "asset_synthetic",
                    "name": synthetic.name,
                    "serverPath": str(synthetic),
                    "mediaKind": "video",
                    "duration": 4.0,
                },
            ],
            "selectedClipId": "clip_3",
        },
    )

    assert output_seen["targets"] == [synthetic.name, mechanical.name]
    assert output_seen["duration_sec"] == 3.0
    assert output_seen["transition_sec"] == 0.0
    assert [Path(item["source_path"]).name for item in result["targets"]] == [synthetic.name, mechanical.name]
    assert result["render_passes"][0]["metadata"]["clip_count"] == 2


def test_agent_workflow_timeline_broll_uses_named_library_clips_over_selected_output(monkeypatch, tmp_path: Path) -> None:
    selected_output = tmp_path / "ai_8a4f963d_1.mp4"
    soccer = tmp_path / "01_pair_of_soccer_courts_games_from_above_mixkit_41371_720p.mp4"
    synthetic = tmp_path / "ui-replace-synthetic-blue-orbit.mp4"
    private = tmp_path / "98f59a73df9ce456a4cfbc4020efe02b.mp4"
    pexels = tmp_path / "pexels-abstract-lights.mp4"
    for path in (selected_output, soccer, synthetic, private, pexels):
        path.write_bytes(b"video")
    output_seen: dict[str, object] = {}

    def fake_probe(path: str) -> dict:
        durations = {
            selected_output.name: 3.0,
            soccer.name: 25.0,
            synthetic.name: 4.0,
            private.name: 14.0,
            pexels.name: 32.0,
        }
        return {
            "duration": durations[Path(path).name],
            "media_kind": "video",
            "mime_type": "video/mp4",
            "width": 1280,
            "height": 720,
            "fps": 30.0,
            "codec": "h264",
            "audio_codec": "",
            "has_audio": False,
            "file_size_bytes": 5,
        }

    def fake_list_assets(account_id: str, limit: int = 300):
        assert account_id == "acct_1"
        return [
            {"asset_id": "asset_soccer", "name": soccer.name, "source_path": str(soccer), "media_kind": "video"},
            {"asset_id": "asset_synthetic", "name": synthetic.name, "source_path": str(synthetic), "media_kind": "video"},
            {"asset_id": "asset_private", "name": private.name, "source_path": str(private), "media_kind": "video"},
            {"asset_id": "asset_pexels", "name": pexels.name, "source_path": str(pexels), "media_kind": "video"},
        ]

    def fake_render(targets, *, output_path: str, duration_sec: float, transition_sec: float, **kwargs) -> dict:
        Path(output_path).write_bytes(b"rendered")
        output_seen["targets"] = [Path(item["source_path"]).name for item in targets]
        output_seen["duration_sec"] = duration_sec
        output_seen["transition_sec"] = transition_sec
        return {
            "kind": "timeline_broll_preview",
            "status": "succeeded",
            "inputs": [item["source_path"] for item in targets],
            "output": output_path,
            "duration_sec": duration_sec,
            "transition_sec": transition_sec,
            "clip_count": len(targets),
        }

    fake_module = ModuleType("gemia.media_library")
    fake_module.list_assets = fake_list_assets  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gemia.media_library", fake_module)
    monkeypatch.setattr("gemia.agent_workflow.probe_media", fake_probe)
    monkeypatch.setattr("gemia.agent_workflow._render_timeline_broll_preview", fake_render)

    class FakeOrch:
        outputs_dir = tmp_path

        def plan_from_primitives(self, *args, **kwargs) -> dict:
            raise AssertionError("named library b-roll concat should not call Gemini planning")

    prompt = (
        "重新生成一个只使用公开/合成素材的严格 3 秒横版本地 MP4："
        f"前 1.5 秒只用 {soccer.name} 的俯拍球场，"
        f"1.5 秒后硬切到 {synthetic.name} 的蓝色几何动画，后段 1.5 秒；"
        f"明确不要使用 {private.name}、174f721de3b2f58d090220f91852a967.mp4 或 {pexels.name}；"
        "总长必须 3 秒，1280x720，无字幕、无标题、无水印、无叠化、无残影。"
    )
    result = run_agent_workflow(
        FakeOrch(),
        prompt=prompt,
        input_path=None,
        account_id="acct_1",
        project_state={
            "clips": [
                {
                    "id": "clip_output",
                    "assetId": "asset_output",
                    "name": selected_output.name,
                    "serverPath": str(selected_output),
                    "mediaKind": "video",
                    "duration": 3.0,
                }
            ],
            "selectedClipId": "clip_output",
        },
    )

    assert output_seen["targets"] == [soccer.name, synthetic.name]
    assert output_seen["duration_sec"] == 3.0
    assert output_seen["transition_sec"] == 0.0
    assert [Path(item["source_path"]).name for item in result["targets"]] == [soccer.name, synthetic.name]


def test_agent_workflow_plans_adjacent_transition_once_with_both_targets(monkeypatch, tmp_path: Path) -> None:
    one = tmp_path / "one.mp4"
    two = tmp_path / "two.mp4"
    one.write_bytes(b"one")
    two.write_bytes(b"two")
    output_path = tmp_path / "transition.mp4"

    monkeypatch.setattr(
        "gemia.agent_workflow.probe_media",
        lambda path: {
            "duration": 6.0 if path == str(one) else 8.0,
            "media_kind": "video",
            "mime_type": "video/mp4",
            "width": 1280,
            "height": 720,
            "fps": 30.0,
            "codec": "h264",
            "audio_codec": "aac",
            "has_audio": True,
            "file_size_bytes": 3,
        },
    )

    class FakeOrch:
        outputs_dir = tmp_path

        def __init__(self) -> None:
            self.tasks: dict[str, dict] = {}
            self.plan_calls = 0
            self.last_project_state: dict | None = None

        def plan_from_primitives(self, *args, **kwargs) -> dict:
            self.plan_calls += 1
            self.last_project_state = kwargs.get("project_state")
            return {
                "version": "2.1",
                "goal": "Add a dissolve transition",
                "input_path": str(two),
                "output_path": str(output_path),
                "assistant_message": "我会把两段素材作为一个转场操作处理。",
                "steps": [
                    {
                        "id": "s1",
                        "function": "gemia.video.transitions.transition_dissolve",
                        "args": {"duration_sec": 0.5},
                        "input": [str(one), "$input"],
                        "output": "$output",
                    }
                ],
            }

        def run_plan_dict(self, plan: dict, progress_callback=None) -> str:
            task_id = "child_transition"
            output_path.write_bytes(b"transition")
            if progress_callback is not None:
                progress_callback(1, 1, "gemia.video.transitions.transition_dissolve")
            self.tasks[task_id] = {"outputs": [str(output_path)]}
            return task_id

        def get_task(self, task_id: str) -> dict:
            return self.tasks[task_id]

    events: list[dict] = []
    orch = FakeOrch()
    result = run_agent_workflow(
        orch,
        prompt="在这里添加转场",
        input_path=str(two),
        project_state={
            "clips": [
                {
                    "id": "clip_1",
                    "assetId": "asset_1",
                    "name": "one.mp4",
                    "serverPath": str(one),
                    "mediaKind": "video",
                    "duration": 6.0,
                },
                {
                    "id": "clip_2",
                    "assetId": "asset_2",
                    "name": "two.mp4",
                    "serverPath": str(two),
                    "mediaKind": "video",
                    "duration": 8.0,
                },
            ],
            "selectedClipId": "clip_2",
        },
        event_callback=events.append,
    )

    assert orch.plan_calls == 1
    assert result["outputs"] == [str(output_path)]
    assert [target["clip_id"] for target in result["targets"]] == ["clip_1", "clip_2"]
    assert orch.last_project_state
    agent_context = orch.last_project_state["agent_context"]
    assert [target["clip_id"] for target in agent_context["targets"]] == ["clip_1", "clip_2"]
    assert [target["clip_id"] for target in agent_context["multi_input_targets"]] == ["clip_1", "clip_2"]
    assert agent_context["multi_input_operation"] == "adjacent_transition"
    assert "one multi-input operation" in " ".join(agent_context["instructions"])
    assert any(event.get("label") == "选择目标素材" and event.get("stats", {}).get("target_count") == 2 for event in events)


def test_agent_workflow_single_target_transition_fails_before_fake_layer_preview(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "one.mp4"
    source.write_bytes(b"one")
    output_path = tmp_path / "transition.mp4"

    monkeypatch.setattr(
        "gemia.agent_workflow.probe_media",
        lambda path: {
            "duration": 6.0,
            "media_kind": "video",
            "mime_type": "video/mp4",
            "width": 1280,
            "height": 720,
            "fps": 30.0,
            "codec": "h264",
            "audio_codec": "",
            "has_audio": False,
            "file_size_bytes": 3,
        },
    )

    class FakeOrch:
        outputs_dir = tmp_path

        def __init__(self) -> None:
            self.calls = 0
            self.executed_plans: list[dict] = []

        def plan_from_primitives(self, *args, **kwargs) -> dict:
            return {
                "version": "2.1",
                "goal": "Add a dissolve transition",
                "input_path": str(source),
                "output_path": str(output_path),
                "steps": [
                    {
                        "id": "s1",
                        "function": "gemia.video.transitions.transition_dissolve",
                        "args": {"duration_sec": 0.5},
                        "input": "$input",
                        "output": "$output",
                    }
                ],
            }

        def run_plan_dict(self, plan: dict, progress_callback=None) -> str:
            self.calls += 1
            self.executed_plans.append(plan)
            raise RuntimeError("transition_dissolve needs two input videos")

    events: list[dict] = []
    orch = FakeOrch()
    with pytest.raises(Exception, match="转场需要两段"):
        run_agent_workflow(
            orch,
            prompt="在两段视频中间添加溶解转场",
            input_path=str(source),
            project_state={
                "clips": [
                    {
                        "id": "clip_1",
                        "assetId": "asset_1",
                        "name": "one.mp4",
                        "serverPath": str(source),
                        "mediaKind": "video",
                        "duration": 6.0,
                    }
                ],
                "selectedClipId": "clip_1",
            },
            event_callback=events.append,
        )

    assert orch.calls == 0
    assert orch.executed_plans == []
    assert not output_path.exists()
    assert not any(event.get("detail") == "local layer fallback" for event in events)
    assert not any(event.get("command") == "render_layer_workflow" for event in events)


def test_agent_workflow_pins_adjacent_transition_step_to_two_targets(monkeypatch, tmp_path: Path) -> None:
    one = tmp_path / "one.mp4"
    two = tmp_path / "two.mp4"
    one.write_bytes(b"one")
    two.write_bytes(b"two")
    output_path = tmp_path / "transition.mp4"

    monkeypatch.setattr(
        "gemia.agent_workflow.probe_media",
        lambda path: {
            "duration": 4.0,
            "media_kind": "video",
            "mime_type": "video/mp4",
            "width": 1280,
            "height": 720,
            "fps": 30.0,
            "codec": "h264",
            "audio_codec": "aac",
            "has_audio": True,
            "file_size_bytes": 3,
        },
    )

    class FakeOrch:
        outputs_dir = tmp_path

        def __init__(self) -> None:
            self.tasks: dict[str, dict] = {}
            self.executed_plan: dict | None = None

        def plan_from_primitives(self, *args, **kwargs) -> dict:
            return {
                "version": "2.1",
                "goal": "Add transition",
                "input_path": str(two),
                "output_path": str(output_path),
                "steps": [
                    {
                        "id": "s1",
                        "function": "gemia.video.transitions.transition_dissolve",
                        "args": {"duration_sec": 0.5},
                        "input": "$input",
                        "output": "$output",
                    }
                ],
            }

        def run_plan_dict(self, plan: dict, progress_callback=None) -> str:
            self.executed_plan = plan
            output_path.write_bytes(b"transition")
            if progress_callback is not None:
                progress_callback(1, 1, "gemia.video.transitions.transition_dissolve")
            self.tasks["child_transition"] = {"outputs": [str(output_path)]}
            return "child_transition"

        def get_task(self, task_id: str) -> dict:
            return self.tasks[task_id]

    orch = FakeOrch()
    result = run_agent_workflow(
        orch,
        prompt="在这里添加转场",
        input_path=str(two),
        project_state={
            "clips": [
                {
                    "id": "clip_1",
                    "assetId": "asset_1",
                    "name": "one.mp4",
                    "serverPath": str(one),
                    "mediaKind": "video",
                    "duration": 4.0,
                },
                {
                    "id": "clip_2",
                    "assetId": "asset_2",
                    "name": "two.mp4",
                    "serverPath": str(two),
                    "mediaKind": "video",
                    "duration": 4.0,
                },
            ],
            "selectedClipId": "clip_2",
        },
    )

    assert result["outputs"] == [str(output_path)]
    assert orch.executed_plan is not None
    assert orch.executed_plan["steps"][0]["input"] == [str(one), str(two)]


def test_agent_workflow_preserves_split_timeline_clips_with_same_asset(monkeypatch, tmp_path: Path) -> None:
    one = tmp_path / "one.mp4"
    two = tmp_path / "two.mp4"
    one.write_bytes(b"one")
    two.write_bytes(b"two")
    output_seen: dict[str, object] = {}

    def fake_probe(path: str) -> dict:
        return {
            "duration": 14.0 if path == str(one) else 6.0,
            "media_kind": "video",
            "mime_type": "video/mp4",
            "width": 1280,
            "height": 720,
            "fps": 30.0,
            "codec": "h264",
            "audio_codec": "aac",
            "has_audio": True,
            "file_size_bytes": 3,
        }

    def fake_render(targets, *, output_path: str, duration_sec: float, transition_sec: float, prompt: str = "", **kwargs) -> dict:
        Path(output_path).write_bytes(b"rendered")
        output_seen["clip_ids"] = [item["clip_id"] for item in targets]
        output_seen["source_ranges"] = [(item.get("source_in"), item.get("source_out")) for item in targets]
        output_seen["durations"] = [item["duration"] for item in targets]
        output_seen["prompt"] = prompt
        return {
            "kind": "timeline_broll_preview",
            "status": "succeeded",
            "inputs": [item["source_path"] for item in targets],
            "output": output_path,
            "duration_sec": duration_sec,
            "transition_sec": transition_sec,
            "clip_count": len(targets),
        }

    monkeypatch.setattr("gemia.agent_workflow.probe_media", fake_probe)
    monkeypatch.setattr("gemia.agent_workflow._render_timeline_broll_preview", fake_render)

    class FakeOrch:
        outputs_dir = tmp_path

        def plan_from_primitives(self, *args, **kwargs) -> dict:
            raise AssertionError("split timeline b-roll concat should not call Gemini planning")

    result = run_agent_workflow(
        FakeOrch(),
        prompt="基于当前已经分割过的时间线，剪成一个 3 秒横版小样：只使用当前时间线里的三个片段，第一段约 0.9 秒、第二段约 0.9 秒、第三段约 1.2 秒，中间快速叠化，不要字幕标题。",
        input_path=None,
        project_state={
            "schema": "gemia.project",
            "version": 1,
            "assets": [
                {
                    "id": "asset_same",
                    "asset_id": "asset_same",
                    "name": "one.mp4",
                    "source_path": str(one),
                    "media_kind": "video",
                    "duration": 14.0,
                    "metadata": {"duration": 14.0},
                },
                {
                    "id": "asset_two",
                    "asset_id": "asset_two",
                    "name": "two.mp4",
                    "source_path": str(two),
                    "media_kind": "video",
                    "duration": 6.0,
                    "metadata": {"duration": 6.0},
                },
            ],
            "timeline": {
                "clips": [
                    {
                        "id": "clip_a",
                        "asset_id": "asset_same",
                        "name": "one.mp4 A",
                        "media_kind": "video",
                        "start": 0.0,
                        "duration": 10.0,
                        "source_in": 0.0,
                        "source_out": 10.0,
                    },
                    {
                        "id": "clip_b",
                        "asset_id": "asset_same",
                        "name": "one.mp4 B",
                        "media_kind": "video",
                        "start": 10.0,
                        "duration": 4.0,
                        "source_in": 10.0,
                        "source_out": 14.0,
                    },
                    {
                        "id": "clip_c",
                        "asset_id": "asset_two",
                        "name": "two.mp4",
                        "media_kind": "video",
                        "start": 14.0,
                        "duration": 6.0,
                        "source_in": 0.0,
                        "source_out": 6.0,
                    },
                ]
            },
            "ui_state": {"selected_clip_id": "clip_a"},
        },
    )

    assert output_seen["clip_ids"] == ["clip_a", "clip_b", "clip_c"]
    assert output_seen["source_ranges"] == [(0.0, 10.0), (10.0, 14.0), (0.0, 6.0)]
    assert output_seen["durations"] == [10.0, 4.0, 6.0]
    assert "三个片段" in output_seen["prompt"]
    assert [item["clip_id"] for item in result["targets"]] == ["clip_a", "clip_b", "clip_c"]
    assert result["render_passes"][0]["metadata"]["clip_count"] == 3


def test_requested_duration_prefers_revision_target_over_rejected_output_duration() -> None:
    prompt = (
        "Review 不合格：刚产出的 merge_87b49957.mp4 是 17 秒，太长。"
        "请只使用当前时间线里的三个片段，导出一个严格 3 秒横版 MP4："
        "第一段约 0.9 秒、第二段约 0.9 秒、第三段约 1.2 秒。"
    )

    assert _requested_total_duration_sec(prompt, default=3.0) == 3.0


def test_requested_segment_durations_understands_front_back_constraints() -> None:
    prompt = (
        "Review 旧卡片复查：请做一个严格 2.6 秒版本："
        "前段抽象光效最多 0.8 秒且明显压暗，"
        "后段机械主体至少 1.6 秒更清楚，中间柔和叠化。"
    )

    durations = _requested_segment_durations_sec(prompt, count=2, total_sec=2.6)

    assert [round(value, 2) for value in durations or []] == [0.8, 1.8]


def test_requested_segment_durations_ignores_timestamp_evidence_before_constraint() -> None:
    prompt = (
        "Review 不合格：前段 0.75 秒时青绿色光斑仍然满屏。"
        "请保持严格 2.6 秒，前段抽象光效最多 0.6 秒，"
        "后段机械主体至少 1.8 秒。"
    )
    durations = _requested_segment_durations_sec(prompt, count=2, total_sec=2.6)

    assert [round(value, 2) for value in durations or []] == [0.6, 2.0]


def test_requested_segment_durations_understands_front_seconds_and_cut_time() -> None:
    prompt = (
        "导出严格 3 秒横版本地 MP4：前 1.2 秒使用蓝色几何合成素材，"
        "1.2 秒后硬切到机械主体，后段机械主体至少 1.8 秒；"
        "不要叠化、不要交叉淡化、不要残影。"
    )
    durations = _requested_segment_durations_sec(prompt, count=2, total_sec=3.0)

    assert [round(value, 2) for value in durations or []] == [1.2, 1.8]


def test_requested_segment_durations_understands_short_front_back_after_list_separator() -> None:
    prompt = (
        "重试同样改法验证修复：第二段 ui-replace-synthetic-blue-orbit.mp4 仍要 1.25x center crop，"
        "只裁第二段，保留两个 timeline 素材、顺序、前 1.2 秒 soccer、后 1.8 秒 blue-orbit、"
        "总长 3 秒、transition_sec 0.0。"
    )
    durations = _requested_segment_durations_sec(prompt, count=2, total_sec=3.0)

    assert [round(value, 2) for value in durations or []] == [1.2, 1.8]


def test_hard_cut_prompt_zeroes_transition_and_keeps_front_segment_cap(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeProc:
        returncode = 0
        stderr = ""

    def fake_run(cmd, text: bool, capture_output: bool):
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr("gemia.agent_workflow.subprocess.run", fake_run)
    prompt = (
        "基于当前时间线两个片段，生成严格 2.6 秒横版本地 MP4："
        "前段只用 pexels-abstract-lights.mp4 的抽象光效最多 0.45 秒，"
        "0.45 秒后硬切到 174f721de3b2f58d090220f91852a967.mp4 的机械主体；"
        "不要叠化、不要交叉淡化、不要叠加残影；后段机械主体至少 2.1 秒。"
    )
    source = str(tmp_path / "one.mp4")
    other = str(tmp_path / "two.mp4")

    assert _prompt_requests_hard_cut(prompt) is True
    assert [round(value, 2) for value in _requested_segment_durations_sec(prompt, count=2, total_sec=2.6) or []] == [0.45, 2.15]

    meta = _render_timeline_broll_preview(
        [
            {"source_path": source, "clip_id": "a", "source_in": 0.0, "source_out": 32.0, "metadata": {"source_duration": 32.0}},
            {"source_path": other, "clip_id": "b", "source_in": 0.0, "source_out": 6.0, "metadata": {"source_duration": 6.0}},
        ],
        output_path=str(tmp_path / "out.mp4"),
        duration_sec=2.6,
        transition_sec=0.0,
        prompt=prompt,
    )

    cmd = captured["cmd"]
    assert meta["transition_sec"] == 0.0
    assert [round(value, 2) for value in meta["clip_durations"]] == [0.45, 2.15]
    assert "concat=n=2:v=1:a=0" in cmd[cmd.index("-filter_complex") + 1]
    assert "xfade=" not in cmd[cmd.index("-filter_complex") + 1]


def test_timeline_broll_crop_feedback_targets_last_segment() -> None:
    prompt = (
        "只改这一段：前段蓝色几何合成素材保持 0.9 秒，0.9 秒后硬切到机械主体，"
        "后段机械画面请放大裁切到 125%，中心对准机械圆盘和蓝白零件。"
    )

    transforms = _requested_timeline_broll_clip_transforms(prompt, count=2)

    assert transforms[0]["zoom"] == 1.0
    assert transforms[0]["crop"] is False
    assert transforms[1]["zoom"] == 1.25
    assert transforms[1]["crop"] is True


def test_timeline_broll_crop_feedback_ignores_timeline_view_zoom_percent() -> None:
    prompt = (
        "Review 不合格：技术上没有把 139% 时间线视图缩放误当成画面 crop，这是对的；"
        "但创作画面还不够干净。只改这一段：仍保持前 0.9 秒蓝色合成素材，"
        "0.9 秒后硬切到机械主体；继续忽略时间线视图缩放百分比，"
        "只对后段机械画面实际放大裁切到 125%，中心对准机械圆盘。"
    )

    transforms = _requested_timeline_broll_clip_transforms(prompt, count=2)

    assert transforms[0]["zoom"] == 1.0
    assert transforms[0]["crop"] is False
    assert transforms[1]["zoom"] == 1.25
    assert transforms[1]["crop"] is True


def test_timeline_broll_crop_feedback_accepts_increase_to_zoom_language() -> None:
    prompt = (
        "Review 不合格：135% 的技术结果正确，且 100% 时间线视图缩放没有污染输出；"
        "但画面仍不够干净。只改这一段：继续忽略时间线视图缩放，"
        "只把后段机械画面实际放大裁切提高到 150%，"
        "并在 manifest 的 clip_transforms 记录后段 zoom 为 1.50；前段保持 zoom 1.0。"
    )

    transforms = _requested_timeline_broll_clip_transforms(prompt, count=2)

    assert transforms[0]["zoom"] == 1.0
    assert transforms[0]["crop"] is False
    assert transforms[1]["zoom"] == 1.5
    assert transforms[1]["crop"] is True


def test_timeline_broll_crop_feedback_accepts_bare_zoom_language() -> None:
    prompt = (
        "Review 仍不合格：后段木桌太多。只改这一段：后段机械画面提高到 zoom 2.2，"
        "保持 anchor lower、crop_offset_x 0.45、crop_offset_y 0.45；前段保持 zoom 1.0。"
    )

    transforms = _requested_timeline_broll_clip_transforms(prompt, count=2)

    assert transforms[0]["zoom"] == 1.0
    assert transforms[0]["crop"] is False
    assert transforms[1]["zoom"] == 2.2
    assert transforms[1]["crop"] is True
    assert transforms[1]["anchor"] == "lower"
    assert transforms[1]["crop_offset_x"] == 0.45
    assert transforms[1]["crop_offset_y"] == 0.45


def test_timeline_broll_crop_feedback_accepts_prefixed_x_center_crop_language() -> None:
    prompt = (
        "Review 不合格：第二段 ui-replace-synthetic-blue-orbit.mp4 底部横线和下方留白太明显。"
        "只局部重做第二段：第二段做 1.25x center crop，尽量裁掉底部横线；前段保持 zoom 1.0。"
    )

    transforms = _requested_timeline_broll_clip_transforms(prompt, count=2)

    assert transforms[0]["zoom"] == 1.0
    assert transforms[0]["crop"] is False
    assert transforms[1]["zoom"] == 1.25
    assert transforms[1]["crop"] is True
    assert transforms[1]["anchor"] == "center"
    assert transforms[1]["crop_offset_x"] == 0.0
    assert transforms[1]["crop_offset_y"] == 0.0


def test_timeline_broll_crop_feedback_accepts_top_finger_offset_language() -> None:
    prompt = (
        "Review 仍不合格：150% 已生效，但顶部手指仍明显。"
        "只改后段机械画面：放大裁切提高到 180%，取景下移，避开顶部手指，前段保持 zoom 1.0。"
    )

    transforms = _requested_timeline_broll_clip_transforms(prompt, count=2)

    assert transforms[0]["zoom"] == 1.0
    assert transforms[0]["crop"] is False
    assert transforms[1]["zoom"] == 1.8
    assert transforms[1]["crop"] is True
    assert transforms[1]["anchor"] == "lower"
    assert transforms[1]["crop_offset_y"] > 0


def test_timeline_broll_crop_feedback_accepts_stronger_explicit_offset_language() -> None:
    prompt = (
        "Review 仍不合格：180% lower 已生效，但顶部手指仍贴边。"
        "只改后段机械画面：保持放大裁切 180%，取景继续下移，crop_offset_y 0.45，"
        "避开顶部手指，前段保持 zoom 1.0。"
    )

    transforms = _requested_timeline_broll_clip_transforms(prompt, count=2)

    assert transforms[0]["zoom"] == 1.0
    assert transforms[0]["crop"] is False
    assert transforms[1]["zoom"] == 1.8
    assert transforms[1]["crop"] is True
    assert transforms[1]["anchor"] == "lower"
    assert transforms[1]["crop_offset_y"] == 0.45


def test_timeline_broll_crop_feedback_accepts_explicit_horizontal_offset_language() -> None:
    prompt = (
        "Review 仍不合格：纵向 crop_offset_y 0.45 已生效，但后段机械圆盘仍偏右。"
        "只改后段机械画面：保持实际放大裁切 180%、anchor lower、crop_offset_y 0.45，"
        "再把取景向右平移，crop_offset_x 0.25，让机械圆盘更接近画面中心；前段保持 zoom 1.0。"
    )

    transforms = _requested_timeline_broll_clip_transforms(prompt, count=2)

    assert transforms[0]["zoom"] == 1.0
    assert transforms[0]["crop"] is False
    assert transforms[1]["zoom"] == 1.8
    assert transforms[1]["crop"] is True
    assert transforms[1]["anchor"] == "lower"
    assert transforms[1]["crop_offset_y"] == 0.45
    assert transforms[1]["crop_offset_x"] == 0.25


def test_timeline_broll_crop_feedback_explicit_offset_overrides_prior_value() -> None:
    prompt = (
        "Review 仍不合格：crop_offset_x 0.45 已生效，但右侧木桌仍过多。"
        "只改后段机械画面：保持 zoom 2.5、anchor lower、crop_offset_y 0.45，"
        "把横向取景改成 crop_offset_x 0.15；前段保持 zoom 1.0。"
    )

    transforms = _requested_timeline_broll_clip_transforms(prompt, count=2)

    assert transforms[0]["zoom"] == 1.0
    assert transforms[0]["crop"] is False
    assert transforms[1]["zoom"] == 2.5
    assert transforms[1]["crop"] is True
    assert transforms[1]["anchor"] == "lower"
    assert transforms[1]["crop_offset_y"] == 0.45
    assert transforms[1]["crop_offset_x"] == 0.15


def test_timeline_broll_preview_applies_center_crop_zoom_to_requested_segment(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeProc:
        returncode = 0
        stderr = ""

    def fake_run(cmd, text: bool, capture_output: bool):
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr("gemia.agent_workflow.subprocess.run", fake_run)
    source = str(tmp_path / "one.mp4")
    other = str(tmp_path / "two.mp4")
    prompt = (
        "Review 不合格：刚才的 125% 放大裁切没有生效。只改这一段：仍保持两个素材顺序、"
        "3 秒、0.9/2.1、硬切无残影；后段机械主体必须实际放大裁切到 125%，中心对准机械圆盘。"
    )

    meta = _render_timeline_broll_preview(
        [
            {"source_path": source, "clip_id": "a", "source_in": 0.0, "source_out": 32.0, "metadata": {"source_duration": 32.0}},
            {"source_path": other, "clip_id": "b", "source_in": 0.0, "source_out": 6.0, "metadata": {"source_duration": 6.0}},
        ],
        output_path=str(tmp_path / "out.mp4"),
        duration_sec=3.0,
        transition_sec=0.0,
        prompt=prompt,
    )

    filter_complex = captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
    assert meta["clip_transforms"][0]["zoom"] == 1.0
    assert meta["clip_transforms"][1]["zoom"] == 1.25
    assert "[0:v]" in filter_complex
    assert "[1:v]" in filter_complex
    assert "[0:v]" + "trim" in filter_complex
    assert f"[0:v]" in filter_complex
    assert "scale=trunc(iw*1.250000/2)*2:trunc(ih*1.250000/2)*2,crop=1280:720" in filter_complex
    first_clip_filter = filter_complex.split("[v0]")[0]
    assert "scale=trunc(iw*1.250000" not in first_clip_filter


def test_timeline_broll_preview_applies_crop_offset_to_requested_segment(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeProc:
        returncode = 0
        stderr = ""

    def fake_run(cmd, text: bool, capture_output: bool):
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr("gemia.agent_workflow.subprocess.run", fake_run)
    prompt = "只改后段机械画面：放大裁切提高到 180%，取景下移，避开顶部手指。"

    meta = _render_timeline_broll_preview(
        [
            {"source_path": str(tmp_path / "one.mp4"), "clip_id": "a", "source_in": 0.0, "source_out": 4.0},
            {"source_path": str(tmp_path / "two.mp4"), "clip_id": "b", "source_in": 0.0, "source_out": 6.0},
        ],
        output_path=str(tmp_path / "out.mp4"),
        duration_sec=3.0,
        transition_sec=0.0,
        prompt=prompt,
    )

    filter_complex = captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
    assert meta["clip_transforms"][1]["zoom"] == 1.8
    assert meta["clip_transforms"][1]["crop_offset_y"] > 0
    assert "crop=1280:720:(iw-1280)*0.500000:(ih-720)*0.800000" in filter_complex


def test_timeline_broll_preview_applies_explicit_strong_crop_offset(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeProc:
        returncode = 0
        stderr = ""

    def fake_run(cmd, text: bool, capture_output: bool):
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr("gemia.agent_workflow.subprocess.run", fake_run)
    prompt = "只改后段机械画面：保持放大裁切 180%，取景继续下移，crop_offset_y 0.45，避开顶部手指。"

    meta = _render_timeline_broll_preview(
        [
            {"source_path": str(tmp_path / "one.mp4"), "clip_id": "a", "source_in": 0.0, "source_out": 4.0},
            {"source_path": str(tmp_path / "two.mp4"), "clip_id": "b", "source_in": 0.0, "source_out": 6.0},
        ],
        output_path=str(tmp_path / "out.mp4"),
        duration_sec=3.0,
        transition_sec=0.0,
        prompt=prompt,
    )

    filter_complex = captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
    assert meta["clip_transforms"][1]["zoom"] == 1.8
    assert meta["clip_transforms"][1]["crop_offset_y"] == 0.45
    assert "crop=1280:720:(iw-1280)*0.500000:(ih-720)*0.950000" in filter_complex


def test_timeline_broll_preview_applies_explicit_horizontal_crop_offset(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeProc:
        returncode = 0
        stderr = ""

    def fake_run(cmd, text: bool, capture_output: bool):
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr("gemia.agent_workflow.subprocess.run", fake_run)
    prompt = "只改后段机械画面：保持放大裁切 180%，crop_offset_y 0.45，再把取景向右平移，crop_offset_x 0.25。"

    meta = _render_timeline_broll_preview(
        [
            {"source_path": str(tmp_path / "one.mp4"), "clip_id": "a", "source_in": 0.0, "source_out": 4.0},
            {"source_path": str(tmp_path / "two.mp4"), "clip_id": "b", "source_in": 0.0, "source_out": 6.0},
        ],
        output_path=str(tmp_path / "out.mp4"),
        duration_sec=3.0,
        transition_sec=0.0,
        prompt=prompt,
    )

    filter_complex = captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
    assert meta["clip_transforms"][1]["zoom"] == 1.8
    assert meta["clip_transforms"][1]["crop_offset_y"] == 0.45
    assert meta["clip_transforms"][1]["crop_offset_x"] == 0.25
    assert "crop=1280:720:(iw-1280)*0.750000:(ih-720)*0.950000" in filter_complex


def test_timeline_broll_preview_extends_ultra_short_split_clip(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeProc:
        returncode = 0
        stderr = ""

    def fake_run(cmd, text: bool, capture_output: bool):
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr("gemia.agent_workflow.subprocess.run", fake_run)
    source = str(tmp_path / "one.mp4")
    other = str(tmp_path / "two.mp4")
    output = tmp_path / "out.mp4"

    meta = _render_timeline_broll_preview(
        [
            {"source_path": source, "clip_id": "a", "source_in": 0.0, "source_out": 10.5, "metadata": {"source_duration": 14.0}},
            {"source_path": source, "clip_id": "b", "source_in": 10.5, "source_out": 10.6, "metadata": {"source_duration": 14.0}},
            {"source_path": other, "clip_id": "c", "source_in": 0.0, "source_out": 6.0, "metadata": {"source_duration": 6.0}},
        ],
        output_path=str(output),
        duration_sec=3.0,
        transition_sec=0.3,
        prompt="第一段约 0.9 秒、第二段约 0.9 秒、第三段约 1.2 秒",
    )

    assert meta["clip_count"] == 3
    assert meta["source_ranges"][1]["duration"] == 1.2
    assert meta["source_ranges"][1]["source_out"] == 11.7
    assert meta["source_ranges"][1]["extended_short_clip"] is True
    assert captured["cmd"].count("-i") == 3


def test_timeline_merge_kept_feedback_min_duration_extends_short_clip(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeProc:
        returncode = 0
        stderr = ""

    def fake_run(cmd, text: bool, capture_output: bool):
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr("gemia.agent_workflow.subprocess.run", fake_run)
    source = str(tmp_path / "one.mp4")
    other = str(tmp_path / "two.mp4")
    output = tmp_path / "merge.mp4"

    meta = _render_timeline_kept_clip_merge(
        [
            {"source_path": source, "clip_id": "a", "source_in": 0.0, "source_out": 10.5, "metadata": {"source_duration": 14.0}},
            {"source_path": source, "clip_id": "b", "source_in": 10.5, "source_out": 10.6, "metadata": {"source_duration": 14.0}},
            {"source_path": other, "clip_id": "c", "source_in": 0.0, "source_out": 6.0, "metadata": {"source_duration": 6.0}},
        ],
        output_path=str(output),
        min_clip_duration_sec=0.8,
    )

    assert meta["kind"] == "timeline_merge_kept"
    assert meta["min_clip_duration_sec"] == 0.8
    assert meta["source_ranges"][1]["duration"] == 0.8
    assert meta["source_ranges"][1]["source_out"] == 11.3
    assert meta["source_ranges"][1]["extended_short_clip"] is True
    assert captured["cmd"].count("-i") == 3


def test_agent_context_mentions_materials_and_targets() -> None:
    material = {
        "material_id": "m1",
        "asset_id": "a1",
        "clip_id": "c1",
        "name": "clip.mp4",
        "source_path": "/tmp/clip.mp4",
        "media_kind": "video",
        "duration": 1.5,
        "metadata": {"width": 1920, "height": 1080, "fps": 30, "has_audio": True},
        "origin": "timeline",
        "selected": True,
        "read_status": "ready",
    }

    context = build_agent_context(
        prompt="剪成短片",
        scope="auto",
        materials=[material],
        targets=[material],
    )

    assert context["mode"] == "think_read_plan_execute"
    assert context["material_count"] == 1
    assert context["target_count"] == 1
    assert context["targets"][0]["clip_id"] == "c1"


def test_agent_workflow_emits_codex_style_execution_events(monkeypatch, tmp_path: Path) -> None:
    media_path = tmp_path / "clip.mp4"
    media_path.write_bytes(b"fake")
    output_path = tmp_path / "out.mp4"

    monkeypatch.setattr(
        "gemia.agent_workflow.probe_media",
        lambda path: {
            "duration": 2.0,
            "media_kind": "video",
            "mime_type": "video/mp4",
            "width": 1280,
            "height": 720,
            "fps": 24.0,
            "codec": "h264",
            "audio_codec": "aac",
            "has_audio": True,
            "file_size_bytes": 4,
        },
    )

    class FakeOrch:
        outputs_dir = tmp_path

        def __init__(self) -> None:
            self.tasks: dict[str, dict] = {}
            self.last_project_state: dict | None = None
            self.last_answers: dict | None = None

        def plan_from_primitives(self, *args, **kwargs) -> dict:
            self.last_project_state = kwargs.get("project_state")
            self.last_answers = kwargs.get("answers")
            return {
                "input_path": str(media_path),
                "output_path": str(output_path),
                "assistant_message": "我会把这段素材调整成更冷静的色调，同时保留原视频节奏。",
                "steps": [
                    {
                        "id": "s1",
                        "function": "gemia.video.effects.trim_clip",
                        "assistant_message": "我先锁定这段素材的有效范围，保证后续处理只落在目标片段上。",
                    },
                    {
                        "id": "s2",
                        "function": "gemia.video.effects.color_grade",
                        "assistant_message": "现在把画面调成更冷静的色调，同时保留原始节奏。",
                    },
                ],
            }

        def run_plan_dict(self, plan: dict, progress_callback=None) -> str:
            task_id = "child_1"
            if progress_callback is not None:
                progress_callback(1, 2, "gemia.video.effects.trim_clip")
                progress_callback(2, 2, "gemia.video.effects.color_grade")
            self.tasks[task_id] = {"outputs": [str(output_path)]}
            return task_id

        def get_task(self, task_id: str) -> dict:
            return self.tasks[task_id]

    events: list[dict] = []
    orch = FakeOrch()
    result = run_agent_workflow(
        orch,
        prompt="调色",
        input_path=str(media_path),
        answers={"tone": "冷色"},
        project_state={
            "timeReferences": [
                {
                    "id": "ref_1",
                    "kind": "range",
                    "start": 1.0,
                    "end": 1.8,
                    "confirmed": True,
                }
            ]
        },
        event_callback=events.append,
    )

    assert result["outputs"] == [str(output_path)]
    assert any(event.get("body") for event in events if event.get("phase") in {"think", "read", "plan", "execute"})
    assert all(len(event.get("body", "")) <= 560 for event in events if event.get("phase") in {"think", "read", "plan", "execute"})
    assert any(event.get("label") == "锁定时间参考" for event in events)
    assert any(event.get("meta") == "执行了 trim_clip · 步骤 1/2 · 素材 1/1" for event in events)
    assert any("我先锁定这段素材的有效范围" in event.get("body", "") for event in events)
    assert any(
        event.get("command") == "trim_clip"
        and event.get("voice") == "gemini"
        and "目标片段" in event.get("body", "")
        for event in events
    )
    assert any(event.get("meta") == "已规划 2 个步骤" for event in events)
    assert any("我会把这段素材调整成更冷静的色调" in event.get("body", "") for event in events)
    assert any(event.get("voice") == "gemini" for event in events)
    assert orch.last_project_state
    assert orch.last_answers == {"tone": "冷色"}
    assert orch.last_project_state["agent_time_references"][0]["label"] == "00:01-00:01.8"
    assert any(event.get("command") == "trim_clip" and event.get("step_index") == 1 for event in events)
    assert events[-1]["label"] == "完成汇报"
    assert "最终汇报" in events[-1]["body"]
    assert events[-1]["outputs"] == [str(output_path)]


def test_agent_workflow_allows_prompt_only_creation(tmp_path: Path) -> None:
    output_path = tmp_path / "prompt_canvas.mp4"

    class FakeOrch:
        outputs_dir = tmp_path

        def __init__(self) -> None:
            self.tasks: dict[str, dict] = {}
            self.last_input_path: str | None = None
            self.last_project_state: dict | None = None

        def plan_from_primitives(self, *args, **kwargs) -> dict:
            self.last_input_path = kwargs.get("input_path")
            self.last_project_state = kwargs.get("project_state")
            return {
                "input_path": "",
                "output_path": str(output_path),
                "assistant_message": "我会从空画布开始做一段 Lumeri 风格的商业标题动画。",
                "steps": [
                    {
                        "id": "s1",
                        "function": "gemia.video.ad_graphics.render_ad_title_pack",
                        "args": {"title": "Lumeri", "subtitle": "Prompt to video", "duration": 3},
                        "input": "$input",
                        "output": "$output",
                        "assistant_message": "我先在空画布上渲染标题和卖点动画。",
                    }
                ],
            }

        def run_plan_dict(self, plan: dict, progress_callback=None) -> str:
            task_id = "child_prompt"
            if progress_callback is not None:
                progress_callback(1, 1, "gemia.video.ad_graphics.render_ad_title_pack")
            self.tasks[task_id] = {"outputs": [str(output_path)]}
            return task_id

        def get_task(self, task_id: str) -> dict:
            return self.tasks[task_id]

    events: list[dict] = []
    orch = FakeOrch()
    result = run_agent_workflow(
        orch,
        prompt="做一个 Lumeri 三秒商业广告标题动画",
        input_path=None,
        project_state={"clips": []},
        event_callback=events.append,
    )

    assert result["outputs"] == [str(output_path)]
    assert result["materials"] == []
    assert result["targets"][0]["origin"] == "prompt"
    assert result["creative_mode"] == "prompt_only"
    assert result["render_passes"][0]["status"] == "missing_output"
    assert result["review_notes"][0]["verdict"] == "needs_work"
    assert orch.last_input_path == ""
    assert orch.last_project_state
    assert orch.last_project_state["agent_context"]["prompt_only_creation"] is True
    assert orch.last_project_state["agent_context"]["creative_mode"] == "prompt_only"
    assert any(event.get("label") == "进入空画布创作" for event in events)
    assert any(event.get("voice") == "gemini" for event in events)


def test_agent_workflow_prompt_only_ignores_account_library(monkeypatch, tmp_path: Path) -> None:
    output_path = tmp_path / "prompt_canvas.mp4"

    def fail_list_assets(account_id: str, limit: int = 300):
        raise AssertionError("prompt-only workflow must not read the account library by default")

    fake_module = ModuleType("gemia.media_library")
    fake_module.list_assets = fail_list_assets  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gemia.media_library", fake_module)

    class FakeOrch:
        outputs_dir = tmp_path

        def __init__(self) -> None:
            self.tasks: dict[str, dict] = {}
            self.last_input_path: str | None = None
            self.last_project_state: dict | None = None

        def plan_from_primitives(self, *args, **kwargs) -> dict:
            self.last_input_path = kwargs.get("input_path")
            self.last_project_state = kwargs.get("project_state")
            return {
                "input_path": "",
                "output_path": str(output_path),
                "steps": [
                    {
                        "id": "s1",
                        "function": "gemia.video.layer_flow.render_layer_workflow",
                        "args": {"canvas": {"width": 640, "height": 360, "fps": 30, "total_frames": 90}},
                    }
                ],
            }

        def run_plan_dict(self, plan: dict, progress_callback=None) -> str:
            task_id = "child_prompt"
            output_path.write_bytes(b"fake video")
            if progress_callback is not None:
                progress_callback(1, 1, "gemia.video.layer_flow.render_layer_workflow")
            self.tasks[task_id] = {"outputs": [str(output_path)]}
            return task_id

        def get_task(self, task_id: str) -> dict:
            return self.tasks[task_id]

    orch = FakeOrch()
    result = run_agent_workflow(
        orch,
        prompt="创建 3 秒 prompt-only MG 标题动画：中心大字 LUMERI，不要外部素材",
        input_path=None,
        project_state={"clips": []},
        account_id="acct_1",
    )

    assert result["materials"] == []
    assert result["targets"][0]["origin"] == "prompt"
    assert result["creative_mode"] == "prompt_only"
    assert orch.last_input_path == ""
    assert orch.last_project_state
    assert orch.last_project_state["agent_context"]["prompt_only_creation"] is True


def test_agent_workflow_gates_unrequested_risky_capability_to_local_layer_preview(tmp_path: Path) -> None:
    output_path = tmp_path / "safe_preview.mp4"

    class FakeOrch:
        outputs_dir = tmp_path

        def __init__(self) -> None:
            self.tasks: dict[str, dict] = {}
            self.executed_plan: dict | None = None

        def plan_from_primitives(self, *args, **kwargs) -> dict:
            return {
                "input_path": "",
                "output_path": str(output_path),
                "assistant_message": "我想直接调用外部视频生成。",
                "steps": [
                    {
                        "id": "s1",
                        "function": "gemia.video.generative.generate_video",
                        "args": {"prompt": "city night", "duration": 3},
                    }
                ],
            }

        def run_plan_dict(self, plan: dict, progress_callback=None) -> str:
            self.executed_plan = plan
            task_id = "child_safe"
            output_path.write_bytes(b"fake video")
            if progress_callback is not None:
                progress_callback(1, 1, plan["steps"][0]["function"])
            self.tasks[task_id] = {"outputs": [str(output_path)]}
            return task_id

        def get_task(self, task_id: str) -> dict:
            return self.tasks[task_id]

    events: list[dict] = []
    orch = FakeOrch()
    result = run_agent_workflow(
        orch,
        prompt="做一个城市夜景广告小样",
        input_path=None,
        project_state={"clips": []},
        event_callback=events.append,
    )

    assert result["outputs"] == [str(output_path)]
    assert orch.executed_plan is not None
    assert orch.executed_plan["steps"][0]["function"] == "gemia.video.layer_flow.render_layer_workflow"
    assert orch.executed_plan["stability_gate"]["blocked_functions"] == ["gemia.video.generative.generate_video"]
    args = orch.executed_plan["steps"][0]["args"]
    assert not args.get("title")
    assert not any(layer.get("type") == "text" for layer in args["overlay_layers"])
    assert "在两段视频中间添加溶解转场" not in json.dumps(args, ensure_ascii=False)
    gate_events = [event for event in events if event.get("capability") == "stability_gate"]
    assert gate_events
    assert gate_events[0]["blocked_capabilities"] == ["veo"]


def test_agent_workflow_rejects_unrequested_risky_capability_for_editing_task(monkeypatch, tmp_path: Path) -> None:
    media_path = tmp_path / "clip.mp4"
    media_path.write_bytes(b"fake")

    monkeypatch.setattr(
        "gemia.agent_workflow.probe_media",
        lambda path: {
            "duration": 3.0,
            "media_kind": "video",
            "mime_type": "video/mp4",
            "width": 1280,
            "height": 720,
            "fps": 30.0,
            "codec": "h264",
            "audio_codec": "",
            "has_audio": False,
            "file_size_bytes": 4,
        },
    )

    class FakeOrch:
        outputs_dir = tmp_path

        def plan_from_primitives(self, *args, **kwargs) -> dict:
            return {
                "version": "2.1",
                "goal": "调色",
                "input_path": str(media_path),
                "output_path": str(tmp_path / "out.mp4"),
                "steps": [
                    {
                        "id": "s1",
                        "function": "gemia.video.generative.generate_video",
                        "args": {"prompt": "unrequested external video"},
                    }
                ],
            }

        def run_plan_dict(self, plan: dict, progress_callback=None) -> str:
            raise AssertionError("blocked risky edit capability must not execute")

    with pytest.raises(Exception, match="高风险能力"):
        run_agent_workflow(
            FakeOrch(),
            prompt="把这个视频调亮一点",
            input_path=str(media_path),
            project_state={
                "clips": [
                    {
                        "id": "clip_1",
                        "assetId": "asset_1",
                        "name": "clip.mp4",
                        "serverPath": str(media_path),
                        "mediaKind": "video",
                        "duration": 3.0,
                    }
                ],
                "selectedClipId": "clip_1",
            },
        )


def test_agent_workflow_downgrades_bouncing_word_video_to_scripted_layers(tmp_path: Path) -> None:
    output_path = tmp_path / "acrab_preview.mp4"

    class FakeOrch:
        outputs_dir = tmp_path

        def __init__(self) -> None:
            self.tasks: dict[str, dict] = {}
            self.executed_plan: dict | None = None

        def plan_from_primitives(self, *args, **kwargs) -> dict:
            return {
                "input_path": "",
                "output_path": str(output_path),
                "assistant_message": "我想直接调用外部视频生成。",
                "steps": [
                    {
                        "id": "s1",
                        "function": "gemia.video.generative.generate_video",
                        "args": {
                            "prompt": "A small ball bouncing over Acrab letters",
                            "duration": 5,
                        },
                    }
                ],
            }

        def run_plan_dict(self, plan: dict, progress_callback=None) -> str:
            self.executed_plan = plan
            task_id = "child_acrab"
            output_path.write_bytes(b"fake video")
            if progress_callback is not None:
                progress_callback(1, 1, plan["steps"][0]["function"])
            self.tasks[task_id] = {"outputs": [str(output_path)]}
            return task_id

        def get_task(self, task_id: str) -> dict:
            return self.tasks[task_id]

    orch = FakeOrch()
    result = run_agent_workflow(
        orch,
        prompt="写脚本创建一个视频，效果是一个黄色小球依次弹过Acrab5个字符，每次落点加小火花，最后停在 b 上方",
        input_path=None,
        project_state={"clips": []},
    )

    assert result["outputs"] == [str(output_path)]
    assert orch.executed_plan is not None
    assert orch.executed_plan["stability_gate"]["mode"] == "local_scripted_layer_preview"
    args = orch.executed_plan["steps"][0]["args"]
    layer_ids = [layer["id"] for layer in args["overlay_layers"]]
    assert "bouncing_ball" in layer_ids
    assert "ball_trail_1" in layer_ids
    assert any(layer["id"].startswith("spark_") for layer in args["overlay_layers"])
    assert any(layer["id"].startswith("contact_shadow_") for layer in args["overlay_layers"])
    assert not any(layer["id"] == "stability_gate_note" for layer in args["overlay_layers"])
    assert any(layer["id"] == "word_base" and layer["text"] == "Acrab" for layer in args["overlay_layers"])
    assert any(layer["id"].startswith("letter_hit_") and layer["text"] == "A" for layer in args["overlay_layers"])
    letter_xs = [
        layer["position"][0]
        for layer in args["overlay_layers"]
        if layer["id"].startswith("letter_hit_")
    ]
    assert letter_xs == sorted(letter_xs)
    assert max(b - a for a, b in zip(letter_xs, letter_xs[1:])) < 100
    ball_layer = next(layer for layer in args["overlay_layers"] if layer["id"] == "bouncing_ball")
    assert ball_layer["color"] == [1.0, 0.84, 0.12, 1.0]
    points = ball_layer["keyframes"]["position"]["points"]
    assert points[0]["value"] != points[-1]["value"]
    last_letter_x = max(letter_xs)
    assert abs(points[-1]["value"][0] - (last_letter_x + int(112 * 0.18))) <= 2
    y_values = [point["value"][1] for point in points]
    assert min(y_values) < max(y_values) - 100


def test_agent_workflow_runtime_failure_uses_scripted_title_mg_fallback(tmp_path: Path) -> None:
    output_path = tmp_path / "neon_cut_preview.mp4"

    class FakeOrch:
        outputs_dir = tmp_path

        def __init__(self) -> None:
            self.tasks: dict[str, dict] = {}
            self.calls = 0
            self.executed_plan: dict | None = None

        def plan_from_primitives(self, *args, **kwargs) -> dict:
            return {
                "input_path": "",
                "output_path": str(output_path),
                "assistant_message": "我将制作 NEON CUT 标题动画。",
                "steps": [
                    {
                        "id": "s1",
                        "function": "gemia.video.layer_flow.render_layer_workflow",
                        "args": {"overlay_layers": [{"id": "bad", "type": "solid", "color": "#bad"}]},
                    }
                ],
            }

        def run_plan_dict(self, plan: dict, progress_callback=None) -> str:
            self.calls += 1
            if self.calls == 1:
                raise ValueError("Invalid layer plan: solid color must be RGBA")
            self.executed_plan = plan
            task_id = "child_neon_cut"
            output_path.write_bytes(b"fake video")
            if progress_callback is not None:
                progress_callback(1, 1, plan["steps"][0]["function"])
            self.tasks[task_id] = {"outputs": [str(output_path)]}
            return task_id

        def get_task(self, task_id: str) -> dict:
            return self.tasks[task_id]

    orch = FakeOrch()
    result = run_agent_workflow(
        orch,
        prompt=(
            "请创建一个3秒MG标题动画：深色网格背景，白色文字 NEON CUT 从左滑入，"
            "青色竖线做一次擦除转场，品红色小方块绕标题转一圈后停在 T 右侧。"
        ),
        input_path=None,
        project_state={"clips": []},
    )

    assert result["outputs"] == [str(output_path)]
    assert orch.calls == 2
    assert orch.executed_plan is not None
    assert orch.executed_plan["stability_gate"]["mode"] == "local_scripted_layer_preview"
    args = orch.executed_plan["steps"][0]["args"]
    layer_ids = [layer["id"] for layer in args["overlay_layers"]]
    assert "title_main" in layer_ids
    assert "cyan_wipe_line" in layer_ids
    assert "magenta_orbit_square" in layer_ids
    assert any(layer_id.startswith("grid_v_") for layer_id in layer_ids)
    assert any(layer_id.startswith("grid_h_") for layer_id in layer_ids)
    assert not any(layer_id == "stability_gate_note" for layer_id in layer_ids)
    title_layer = next(layer for layer in args["overlay_layers"] if layer["id"] == "title_main")
    assert title_layer["text"] == "NEON CUT"
    square_layer = next(layer for layer in args["overlay_layers"] if layer["id"] == "magenta_orbit_square")
    points = square_layer["keyframes"]["position"]["points"]
    assert points[-1]["value"][0] > title_layer["position"][0]


def test_agent_workflow_plan_contract_error_uses_scripted_prompt_only_title_fallback(tmp_path: Path) -> None:
    output_path = tmp_path / "lumeri_title_preview.mp4"

    class FakeOrch:
        outputs_dir = tmp_path

        def __init__(self) -> None:
            self.tasks: dict[str, dict] = {}
            self.executed_plan: dict | None = None

        def plan_from_primitives(self, *args, **kwargs) -> dict:
            raise PlanContractError(
                "规划里还有未解析的模板占位符。",
                detail="placeholder={color} arg=html",
            )

        def run_plan_dict(self, plan: dict, progress_callback=None) -> str:
            self.executed_plan = plan
            task_id = "child_lumeri_title"
            output_path.write_bytes(b"fake video")
            if progress_callback is not None:
                progress_callback(1, 1, plan["steps"][0]["function"])
            self.tasks[task_id] = {"outputs": [str(output_path)]}
            return task_id

        def get_task(self, task_id: str) -> dict:
            return self.tasks[task_id]

    events: list[dict] = []
    orch = FakeOrch()
    result = run_agent_workflow(
        orch,
        prompt=(
            "创建一个 3 秒 prompt-only MG 标题动画：深色纯代码背景，中心大字 LUMERI，"
            "5 个彩色发光小球从左到右依次弹跳击中每个字母，字母短暂放大并产生光晕；"
            "不要使用媒体池素材，不要外部素材。"
        ),
        input_path=None,
        project_state={"clips": []},
        event_callback=events.append,
    )

    assert result["outputs"] == [str(output_path)]
    assert orch.executed_plan is not None
    assert orch.executed_plan["stability_gate"]["mode"] == "local_scripted_layer_preview"
    assert orch.executed_plan["stability_gate"]["reason"] == "plan_contract_error"
    args = orch.executed_plan["steps"][0]["args"]
    layer_ids = [layer["id"] for layer in args["overlay_layers"]]
    assert "bouncing_ball" in layer_ids
    assert any(layer["id"] == "word_base" and layer["text"] == "LUMERI" for layer in args["overlay_layers"])
    assert not any(layer["id"] == "word_base" and layer["text"].lower() == "prompt" for layer in args["overlay_layers"])
    assert any(event.get("label") == "规划契约失败已降级为本地预览" for event in events)


def test_agent_workflow_runtime_failure_falls_back_to_local_layer_preview(tmp_path: Path) -> None:
    output_path = tmp_path / "runtime_safe_preview.mp4"

    class FakeOrch:
        outputs_dir = tmp_path

        def __init__(self) -> None:
            self.tasks: dict[str, dict] = {}
            self.calls = 0
            self.executed_plan: dict | None = None

        def plan_from_primitives(self, *args, **kwargs) -> dict:
            return {
                "input_path": "",
                "output_path": str(output_path),
                "assistant_message": "我会做一段图层小样。",
                "steps": [
                    {
                        "id": "s1",
                        "function": "gemia.video.layer_flow.render_layer_workflow",
                        "args": {"overlay_layers": [{"type": "solid", "color": "#bad"}]},
                    }
                ],
            }

        def run_plan_dict(self, plan: dict, progress_callback=None) -> str:
            self.calls += 1
            if self.calls == 1:
                raise ValueError("Invalid layer plan: color must be RGBA")
            self.executed_plan = plan
            task_id = "child_runtime_safe"
            output_path.write_bytes(b"fake video")
            if progress_callback is not None:
                progress_callback(1, 1, plan["steps"][0]["function"])
            self.tasks[task_id] = {"outputs": [str(output_path)]}
            return task_id

        def get_task(self, task_id: str) -> dict:
            return self.tasks[task_id]

    events: list[dict] = []
    orch = FakeOrch()
    result = run_agent_workflow(
        orch,
        prompt="做一个本地图层小样",
        input_path=None,
        project_state={"clips": []},
        event_callback=events.append,
    )

    assert result["outputs"] == [str(output_path)]
    assert orch.calls == 2
    assert orch.executed_plan is not None
    assert orch.executed_plan["stability_gate"]["mode"] == "local_preview_fallback"
    assert any(event.get("label") == "执行失败已降级为本地预览" for event in events)


def test_agent_workflow_provider_planning_failure_falls_back_for_prompt_only(tmp_path: Path) -> None:
    output_path = tmp_path / "provider_safe_preview.mp4"

    class FakeOrch:
        outputs_dir = tmp_path

        def __init__(self) -> None:
            self.tasks: dict[str, dict] = {}
            self.executed_plan: dict | None = None

        def plan_from_primitives(self, *args, **kwargs) -> dict:
            raise RuntimeError("OpenRouter returned empty response")

        def run_plan_dict(self, plan: dict, progress_callback=None) -> str:
            self.executed_plan = plan
            task_id = "child_provider_safe"
            output_path.write_bytes(b"fake video")
            if progress_callback is not None:
                progress_callback(1, 1, plan["steps"][0]["function"])
            self.tasks[task_id] = {"outputs": [str(output_path)]}
            return task_id

        def get_task(self, task_id: str) -> dict:
            return self.tasks[task_id]

    events: list[dict] = []
    orch = FakeOrch()
    result = run_agent_workflow(
        orch,
        prompt="创建 3 秒 prompt-only MG 标题动画：CLEAN START 从左滑入，黄色小球弹跳经过 CLEAN START，不要外部素材，输出 1280x720",
        input_path=None,
        project_state={"clips": []},
        event_callback=events.append,
    )

    assert result["outputs"] == [str(output_path)]
    assert orch.executed_plan is not None
    assert orch.executed_plan["stability_gate"]["reason"] == "planning_provider_unavailable"
    args = orch.executed_plan["steps"][0]["args"]
    assert args["canvas"] == {"width": 1280, "height": 720, "fps": 30, "total_frames": 90}
    assert args["max_long_edge"] == 1280
    word_base = next(layer for layer in args["overlay_layers"] if layer["id"] == "word_base")
    assert word_base["keyframes"]["position"]["points"][0]["value"][0] < 0
    assert word_base["keyframes"]["position"]["points"][1]["value"] == word_base["position"]
    assert not any(
        layer.get("type") == "text" and not str(layer.get("text") or "").strip()
        for layer in args["overlay_layers"]
    )
    assert any(event.get("label") == "规划失败已降级为本地预览" for event in events)


def test_agent_workflow_builds_render_passes_and_review_notes_from_sidecars(tmp_path: Path) -> None:
    output_path = tmp_path / "layered.mp4"

    class FakeOrch:
        outputs_dir = tmp_path

        def __init__(self) -> None:
            self.tasks: dict[str, dict] = {}

        def plan_from_primitives(self, *args, **kwargs) -> dict:
            return {
                "input_path": "",
                "output_path": str(output_path),
                "assistant_message": "我会把参考做成一个分层广告小样。",
                "steps": [
                    {
                        "id": "s1",
                        "function": "gemia.video.layer_flow.render_layer_workflow",
                        "assistant_message": "我先把标题和参考画面拆成可回溯图层。",
                    }
                ],
            }

        def run_plan_dict(self, plan: dict, progress_callback=None) -> str:
            task_id = "child_layered"
            output_path.write_bytes(b"fake video")
            output_path.with_suffix(".layer-flow.json").write_text(
                json.dumps(
                    {
                        "output_path": str(output_path),
                        "authoring_mode": "planner_controller_layer_flow",
                        "authored_plan": {
                            "layers": [
                                {
                                    "id": "headline",
                                    "type": "text",
                                    "text": "Lumeri",
                                    "z_index": 10,
                                    "position": [48, 48],
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            if progress_callback is not None:
                progress_callback(1, 1, "gemia.video.layer_flow.render_layer_workflow")
            self.tasks[task_id] = {"outputs": [str(output_path)]}
            return task_id

        def get_task(self, task_id: str) -> dict:
            return self.tasks[task_id]

    events: list[dict] = []
    result = run_agent_workflow(
        FakeOrch(),
        prompt="参考资料做成商业广告小样",
        input_path=None,
        project_state={
            "clips": [],
            "reference_assets": [
                {"id": "ref_1", "name": "brand.png", "path": str(tmp_path / "brand.png"), "kind": "image"}
            ],
        },
        event_callback=events.append,
    )

    assert result["creative_mode"] == "reference_guided"
    assert result["reference_assets"][0]["reference_asset_id"] == "ref_1"
    assert result["render_passes"][0]["status"] == "succeeded"
    assert result["render_passes"][0]["layer_ids"] == ["headline"]
    assert result["layer_plan"]["layers"][0]["id"] == "headline"
    assert result["review_notes"][0]["verdict"] == "pass"
    assert any(event.get("phase") == "capability_call" for event in events)
    assert any(event.get("phase") == "preview_ready" and event.get("voice") == "gemini" for event in events)
    assert any(event.get("phase") == "self_review" for event in events)


def test_agent_workflow_marks_veo_fallback_sidecar_as_reviewable_preview(tmp_path: Path) -> None:
    output_path = tmp_path / "veo-fallback.mp4"

    class FakeOrch:
        outputs_dir = tmp_path

        def __init__(self) -> None:
            self.tasks: dict[str, dict] = {}

        def plan_from_primitives(self, *args, **kwargs) -> dict:
            return {
                "input_path": "",
                "output_path": str(output_path),
                "assistant_message": "我先尝试生成一段视频小样。",
                "steps": [
                    {
                        "id": "s1",
                        "function": "gemia.video.generative.generate_video",
                        "args": {"prompt": "city at night", "duration": 0.4},
                        "assistant_message": "我会调用视频生成能力；如果外部接口不稳，就先留下本地预览。",
                    }
                ],
            }

        def run_plan_dict(self, plan: dict, progress_callback=None) -> str:
            task_id = "child_veo"
            output_path.write_bytes(b"fake video")
            output_path.with_suffix(".veo-fallback.json").write_text(
                json.dumps(
                    {
                        "kind": "veo_fallback_preview",
                        "status": "fallback",
                        "provider": "openrouter/veo",
                        "user_message": "Veo 外部接口暂时断开了。我先用 Lumeri 本地渲染做一个可看的低清预览。",
                        "composition": {
                            "duration": 0.4,
                            "blocks": [
                                {"id": "veo_prompt", "kind": "text", "text": "city at night"}
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )
            if progress_callback is not None:
                progress_callback(1, 1, "gemia.video.generative.generate_video")
            self.tasks[task_id] = {"outputs": [str(output_path)]}
            return task_id

        def get_task(self, task_id: str) -> dict:
            return self.tasks[task_id]

    result = run_agent_workflow(
        FakeOrch(),
        prompt="生成一段城市夜景视频",
        input_path=None,
        project_state={"clips": []},
    )

    render_pass = result["render_passes"][0]
    assert render_pass["status"] == "succeeded"
    assert render_pass["fallback"] is True
    assert render_pass["kind"] == "veo_fallback_preview"
    assert render_pass["layer_ids"] == ["veo_prompt"]
    assert result["review_notes"][0]["verdict"] == "fallback"
    assert "本地渲染" in result["review_notes"][0]["note"]


def test_agent_workflow_keeps_dev_briefs_out_of_media_outputs(tmp_path: Path) -> None:
    brief_path = tmp_path / "ai_patch_1.lumeri-dev-brief.md"

    class FakeOrch:
        outputs_dir = tmp_path

        def __init__(self) -> None:
            self.tasks: dict[str, dict] = {}

        def plan_from_primitives(self, *args, **kwargs) -> dict:
            return {
                "input_path": "",
                "output_path": str(brief_path),
                "assistant_message": "我会先写一个开发补丁 brief，不把它当视频预览。",
                "steps": [
                    {
                        "id": "s1",
                        "function": "gemia.video.creative_runtime.write_development_patch_brief",
                        "assistant_message": "当前能力不足，我写 brief 供下一轮开发补齐。",
                    }
                ],
            }

        def run_plan_dict(self, plan: dict, progress_callback=None) -> str:
            task_id = "child_brief"
            brief_path.write_text("# Lumeri dev brief\n", encoding="utf-8")
            if progress_callback is not None:
                progress_callback(1, 1, "gemia.video.creative_runtime.write_development_patch_brief")
            self.tasks[task_id] = {"outputs": [str(brief_path)]}
            return task_id

        def get_task(self, task_id: str) -> dict:
            return self.tasks[task_id]

    events: list[dict] = []
    result = run_agent_workflow(
        FakeOrch(),
        prompt="如果缺能力就写开发 brief",
        input_path=None,
        project_state={"clips": []},
        event_callback=events.append,
    )

    assert result["outputs"] == []
    assert result["artifact_outputs"] == [str(brief_path)]
    assert result["all_outputs"] == [str(brief_path)]
    render_pass = result["render_passes"][0]
    assert render_pass["kind"] == "development_brief"
    assert render_pass["status"] == "artifact_ready"
    assert render_pass["preview_path"] == ""
    assert render_pass["artifact_path"] == str(brief_path)
    assert render_pass["dev_brief_path"] == str(brief_path)
    assert result["review_notes"][0]["verdict"] == "needs_dev"
    assert not any(event.get("phase") == "preview_ready" for event in events)
    assert any(event.get("phase") == "dev_brief" for event in events)
    assert events[-1]["outputs"] == []


def test_agent_workflow_returns_needs_input_for_planner_ask(monkeypatch, tmp_path: Path) -> None:
    media_path = tmp_path / "clip.mp4"
    media_path.write_bytes(b"fake")

    monkeypatch.setattr(
        "gemia.agent_workflow.probe_media",
        lambda path: {
            "duration": 2.0,
            "media_kind": "video",
            "mime_type": "video/mp4",
            "width": 1280,
            "height": 720,
            "fps": 24.0,
            "codec": "h264",
            "audio_codec": "aac",
            "has_audio": True,
            "file_size_bytes": 4,
        },
    )

    questions = [
        {"id": "transition", "text": "您希望使用哪种转场效果？", "input_type": "text"},
        {"id": "duration", "text": "转场持续时间（秒）？", "input_type": "text"},
    ]

    class FakeOrch:
        outputs_dir = tmp_path

        def plan_from_primitives(self, *args, **kwargs) -> dict:
            return {"ask": True, "questions": questions}

        def run_plan_dict(self, *args, **kwargs) -> str:
            raise AssertionError("planner ask should not enter primitive execution")

    events: list[dict] = []
    result = run_agent_workflow(
        FakeOrch(),
        prompt="加一个转场",
        input_path=str(media_path),
        project_state={},
        event_callback=events.append,
    )

    assert result["status"] == "needs_input"
    assert result["ask"] is True
    assert result["pending_ask"]["questions"] == questions
    assert result["_pending_ask_session"]["video"] == str(media_path)
    assert any(
        event.get("phase") == "ask"
        and event.get("status") == "asking"
        and event.get("questions") == questions
        for event in events
    )
