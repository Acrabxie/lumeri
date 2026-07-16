from __future__ import annotations

import json
import math
from pathlib import Path

from gemia.tool_router import (
    MASTER_TOOL_NAMES,
    TOOL_PACKS,
    ToolRouter,
    catalog_coverage,
    classify_request,
    routing_enabled_from_env,
)


_HISTORICAL_PROMPT_FIXTURE = (
    Path(__file__).parent / "fixtures" / "tool_router_historical_prompts.json"
)


def _load_historical_prompt_corpus() -> dict[str, object]:
    return json.loads(_HISTORICAL_PROMPT_FIXTURE.read_text(encoding="utf-8"))


def test_catalog_exactly_covers_current_111_tool_schemas() -> None:
    # 111 = 104 + vector_motion + six dedicated creative-library verbs.
    assert len(MASTER_TOOL_NAMES) == 111
    assert len(set(MASTER_TOOL_NAMES)) == 111
    assert catalog_coverage() == (frozenset(), frozenset())
    # vector_motion must belong to a pack (else it only surfaces on full
    # fallback and the model falls back to hand-pushed keyframes).
    assert "vector_motion" in MASTER_TOOL_NAMES


def test_conversation_has_zero_tools_but_mixed_greeting_stays_actionable() -> None:
    conversation = ToolRouter("你好")
    assert conversation.decision.kind == "conversation"
    assert conversation.active_tool_names == ()

    actionable = ToolRouter("你好，帮我做一个 7 秒的动画视频")
    assert actionable.decision.kind == "actionable"
    assert actionable.active_tool_names
    assert len(actionable.decision.workflows) <= 2


def test_kill_switch_restores_the_full_master_surface() -> None:
    assert routing_enabled_from_env({}) is True
    assert routing_enabled_from_env({"LUMERI_V3_TOOL_ROUTING": "off"}) is False
    assert routing_enabled_from_env({"LUMERI_V3_TOOL_ROUTING": "1"}) is True

    router = ToolRouter("你好", enabled=False)
    assert router.active_tool_names == MASTER_TOOL_NAMES
    assert router.is_full_fallback is True


def test_curated_initial_routes_meet_schema_budget() -> None:
    prompts = [
        "分析这个素材的时长、分辨率和帧率",
        "生成一张 16:9 的产品海报图片",
        "做一个 7 秒动画视频",
        "给这个视频加字幕并调色",
        "给短片加音乐和旁白",
        "列出三个镜头的分镜并组装",
        "把片段插入时间线第二轨",
        "给图层设置位置和透明度",
        "把这一段倒放并做速度坡",
        "给人物加蒙版并做绿幕抠像",
        "在目录里复制并整理这些文件",
        "搜索网上最新的参考资料",
        "给素材添加场景标签",
        "把当前工程导出为 OTIO",
        "记住这个剪辑偏好",
        "让多个代理并行分析素材",
        "做一个 logo 开场动画",
        "生成视频并配旁白",
        "分析图片并把它改成竖版海报",
        "读取这个文件",
    ]
    counts: list[int] = []
    for prompt in prompts:
        router = ToolRouter(prompt)
        assert router.decision.kind == "actionable"
        assert 1 <= len(router.decision.workflows) <= 2
        counts.append(len(router.active_tool_names))

    ordered = sorted(counts)
    p95 = ordered[math.ceil(0.95 * len(ordered)) - 1]
    assert sum(counts) / len(counts) <= 20
    assert p95 <= 32


def test_repository_history_prompt_pack_recall_and_full_fallback_rate() -> None:
    corpus = _load_historical_prompt_corpus()
    assert corpus["corpus_kind"] == "repository_history_test_prompts"
    assert corpus["is_production_conversation_data"] is False
    samples = corpus["samples"]
    assert isinstance(samples, list) and len(samples) >= 30

    seen: set[tuple[str, str]] = set()
    misses: list[tuple[str, str, tuple[str, ...]]] = []
    full_fallbacks = 0
    for sample in samples:
        assert isinstance(sample, dict)
        assert set(sample) == {
            "prompt",
            "required_pack",
            "source_path",
            "source_revision",
        }
        prompt = sample["prompt"]
        required_pack = sample["required_pack"]
        source_path = sample["source_path"]
        source_revision = sample["source_revision"]
        assert isinstance(prompt, str) and prompt.strip()
        assert isinstance(required_pack, str) and required_pack in TOOL_PACKS
        assert source_revision == "487c298"
        assert isinstance(source_path, str) and source_path.startswith("tests/")
        assert source_path.endswith(".py")
        provenance_key = (source_path, prompt)
        assert provenance_key not in seen
        seen.add(provenance_key)

        router = ToolRouter(prompt)
        if required_pack not in router.decision.workflows:
            misses.append((prompt, required_pack, router.decision.workflows))
            router.note_no_progress()
            if required_pack not in router.active_packs:
                router.note_no_progress()
            full_fallbacks += int(router.is_full_fallback)
        assert required_pack in router.active_packs or router.is_full_fallback

    recall = 1 - (len(misses) / len(samples))
    fallback_rate = full_fallbacks / len(samples)
    assert recall >= 0.95, f"required-pack recall={recall:.1%}; misses={misses}"
    assert fallback_rate < 0.05, f"fallback-to-104={fallback_rate:.1%}"


def test_no_progress_expansion_is_monotonic_then_falls_back_to_all_tools() -> None:
    router = ToolRouter("做一个 logo 开场动画")
    initial = set(router.active_tool_names)

    first = router.note_no_progress()
    after_first = set(router.active_tool_names)
    assert first.stage == "adjacent"
    assert first.added_pack is not None
    assert initial < after_first
    assert len(after_first) < len(MASTER_TOOL_NAMES)

    second = router.note_no_progress()
    assert second.stage == "full"
    assert router.active_tool_names == MASTER_TOOL_NAMES
    assert router.is_full_fallback is True


def test_progress_resets_consecutive_no_progress_counter() -> None:
    router = ToolRouter("给图层设置透明度")
    assert router.note_no_progress().stage == "adjacent"
    assert router.no_progress_count == 1

    router.note_progress()
    assert router.no_progress_count == 0
    assert router.note_no_progress().stage == "adjacent"
    assert router.no_progress_count == 1


def test_exhausted_adjacency_falls_back_full_even_after_progress_resets() -> None:
    router = ToolRouter("继续处理")
    assert router.note_no_progress().stage == "adjacent"
    router.note_progress()
    assert router.note_no_progress().stage == "adjacent"
    router.note_progress()
    expansion = router.note_no_progress()
    assert expansion.stage == "full"
    assert router.is_full_fallback is True
    assert router.active_tool_names == MASTER_TOOL_NAMES


def test_hidden_known_tool_can_expand_pack_in_canonical_order() -> None:
    router = ToolRouter("继续处理")
    before = set(router.active_tool_names)
    assert "project_import_otio" not in before

    activated = router.activate_for_tool("project_import_otio")
    assert activated == "interchange"
    assert before < set(router.active_tool_names)
    assert router.activate_for_tool("not_a_real_tool") is None

    master_positions = {name: index for index, name in enumerate(MASTER_TOOL_NAMES)}
    positions = [master_positions[name] for name in router.active_tool_names]
    assert positions == sorted(positions)


def test_pending_job_state_adds_job_support_without_removing_tools() -> None:
    router = ToolRouter("做一个视频", state={"pending_jobs": {"job-1": "running"}})
    initial = set(router.active_tool_names)
    assert {"check_job", "wait_for_job"} <= initial

    router.observe_state({"pending_jobs": {"job-1": "done"}})
    assert initial <= set(router.active_tool_names)


def test_classification_is_deterministic() -> None:
    request = "把视频倒放并加音乐"
    assert classify_request(request) == classify_request(request)


def test_independent_cover_image_keeps_both_deliverable_workflows() -> None:
    decision = classify_request("做一个视频并生成一张封面图")
    assert decision.workflows == ("video_generation", "image")


def test_source_image_made_into_video_routes_to_video_generation() -> None:
    decision = classify_request("把这张图片做成视频")
    assert decision.workflows[:2] == ("video_generation", "image")


def test_common_adjustment_language_routes_to_editing_not_read_only_inspection() -> None:
    decision = classify_request("把画面调亮一点", state={"has_assets": True})
    assert decision.primary_workflow == "video_edit"


def test_google_photos_api_topic_does_not_route_to_image_generation() -> None:
    for request in (
        "哦我说的是通过Google官方的api接入photo",
        "我想了解通过Google官方API接入Google Photos，该怎么做？",
        "请直接把 Google Photos Picker API 接进 Lumeri",
        "Explain how the Google Photos OAuth integration works",
    ):
        router = ToolRouter(request)
        assert "image" not in router.decision.workflows
        assert "generate_image" not in router.active_tool_names


def test_explicit_google_photos_diagram_request_still_routes_to_image() -> None:
    for request in (
        "生成一张 Google Photos API 接入架构图",
        "Create a Google Photos integration diagram",
    ):
        router = ToolRouter(request)
        assert "image" in router.decision.workflows
        assert "generate_image" in router.active_tool_names
