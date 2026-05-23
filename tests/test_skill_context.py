from __future__ import annotations

from statistics import median

from gemia.ai.prompt_slimming import strip_for_planning
from gemia.ai.skill_context import build_skill_plan_prompt_bundle
from gemia.ai.skill_router import route
from gemia.ai.prompt_slimming import token_budget
from gemia.registry import catalog_for_prompt


REQUESTS = [
    "裁前 3 秒",
    "加速 2 倍",
    "暖色调色，稍微提亮",
    "加中文字幕",
    "转场后统一调色",
    "裁剪后加字幕并加速",
    "LumeriLink to Blender 做空间效果",
    "先抠像再加 lower third",
    "油画风格再做暖色调色",
    "磨皮并轻微瘦脸",
    "去模糊后再锐化",
    "生成一段 b-roll 并加字幕",
    "检测场景然后合并高光",
    "帮我弄得高级一点",
]


def _heavy_project_state() -> dict:
    return {
        "clips": [
            {
                "id": "clip1",
                "name": "source.mp4",
                "serverPath": "/tmp/source.mp4",
                "mediaKind": "video",
                "duration": 14,
                "thumbnailStrip": ["x"] * 200,
                "thumbnailSrc": "/tmp/thumb.jpg",
                "previewSrc": "/tmp/preview.mp4",
                "waveformPeaks": [0.2] * 2400,
                "summary": {
                    "duration": 14,
                    "mood": "clean",
                    "key_frame": "00:03",
                    "suggested_use": "main",
                },
            }
        ],
        "timeReferences": [{"id": "r1", "kind": "range", "start": 2, "end": 5}],
    }


def test_skill_prompt_budget_regression() -> None:
    budgets = []
    for request in REQUESTS:
        route_result = route(request)
        bundle = build_skill_plan_prompt_bundle(route_result.skills, effective_request=request, has_video_context=True)
        payload = {
            "request": request,
            "input_path": "/tmp/source.mp4",
            "output_path": "/tmp/out.mp4",
            "project_state": strip_for_planning(_heavy_project_state(), request),
        }
        budget = bundle.token_budget(payload)["total_tokens_est"]
        budgets.append(budget)
        assert budget <= 8000

    assert median(budgets) <= 6000


def test_creative_runtime_prompt_activates_layer_and_development_tools() -> None:
    result = route("让 Lumeri 自己插入图层、编辑文字，必要时自己写微函数")
    bundle = build_skill_plan_prompt_bundle(result.skills, effective_request=result.effective_request)

    assert result.skills[0] == "creative-runtime"
    assert "creative-runtime" in result.skills
    assert "render_layer_workflow" in bundle.dynamic_chunk
    assert "write_development_patch_brief" in bundle.dynamic_chunk
    assert "prefer authored layer/HTML workflows" in bundle.static_chunk
    assert "Do not invent unregistered primitive names" in bundle.static_chunk


def test_combo_stub_is_injected_for_transition_and_color() -> None:
    result = route("请加转场并做冷色调色")
    bundle = build_skill_plan_prompt_bundle(result.skills, effective_request="请加转场并做冷色调色")

    assert "transition" in bundle.selected_skills
    assert "color-grade" in bundle.selected_skills
    assert "transition+color-grade" in bundle.combo_ids
    assert "Combo Plan Stub" in bundle.dynamic_chunk
    assert "```json" in bundle.dynamic_chunk
    assert "{duration|" not in bundle.dynamic_chunk
    assert '"duration": 0.5' in bundle.dynamic_chunk
    assert "duration: " not in bundle.dynamic_chunk


def test_static_prompt_forbids_inactive_skill_primitives() -> None:
    bundle = build_skill_plan_prompt_bundle(["transition"], effective_request="加转场")

    assert "Do not call primitives from inactive skills" in bundle.static_chunk
    assert "Other skill descriptions in this index are only for boundary decisions" in bundle.static_chunk


def test_static_prompt_defaults_face_tracking_instead_of_reasking() -> None:
    bundle = build_skill_plan_prompt_bundle(["face-tracking"], effective_request="人脸跟踪")

    assert "Face/object tracking defaults" in bundle.static_chunk
    assert "Do not ask just because a point, face id, time range, or overlay style is missing" in bundle.static_chunk
    assert "render_face_tracking_plan" in bundle.dynamic_chunk


def test_skill_prompt_is_smaller_than_full_catalog() -> None:
    full_tokens = token_budget(catalog_for_prompt(), {"project_state": _heavy_project_state()})["total_tokens_est"]
    result = route("裁剪后加字幕并加速")
    bundle = build_skill_plan_prompt_bundle(result.skills, effective_request="裁剪后加字幕并加速")
    slim_tokens = token_budget(bundle.combined, {"project_state": strip_for_planning(_heavy_project_state(), "裁剪后加字幕并加速")})["total_tokens_est"]

    assert full_tokens > 25_000
    assert slim_tokens < 6_000
