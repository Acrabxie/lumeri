from __future__ import annotations

import pytest

from gemia.ai.gemini_adapter import build_primitive_plan_system_prompt
from gemia.ai.prompt_slimming import (
    build_effective_request,
    infer_prompt_categories,
    strip_for_planning,
    token_budget,
)


REGRESSION_CASES = [
    ("裁剪前 3 秒", "timeline"),
    ("加速 2 倍", "timeline"),
    ("慢动作 0.5 倍", "timeline"),
    ("暖色调色，稍微提亮", "color"),
    ("做赛博朋克调色", "color"),
    ("加中文字幕", "text_graphics"),
    ("做一个标题卡", "text_graphics"),
    ("把图片按 3 秒视频处理", "timeline"),
    ("视频降噪", "repair"),
    ("锐化并恢复细节", "edge"),
    ("模糊背景", "blur"),
    ("两个片段加淡入淡出转场", "timeline"),
    ("合并所有素材", "timeline"),
    ("对选中的多个素材逐个调色", "color"),
    ("只处理 00:02 到 00:05 的时间范围", "timeline"),
    ("LumeriLink to Blender 做空间效果", "spatial"),
    ("加一个 Lottie 下三分之一动画", "text_graphics"),
    ("人脸磨皮，去掉瑕疵", "face"),
    ("生成一段 b-roll", "generative"),
    ("帮我弄得高级一点", "core"),
]


def _heavy_project_state() -> dict:
    return {
        "selectedClipId": "clip1",
        "timeReferences": [{"id": "r1", "kind": "range", "start": 2, "end": 5, "confirmed": True}],
        "agent_time_references": [{"id": "duplicate"}],
        "clips": [
            {
                "id": "clip1",
                "name": "source.mp4",
                "serverPath": "/tmp/source.mp4",
                "mediaKind": "video",
                "duration": 14.7,
                "thumbnailSrc": "/thumb.jpg",
                "previewSrc": "/preview.mp4",
                "thumbnailStrip": [f"/thumbs/{i}.jpg" for i in range(120)],
                "waveformPeaks": [0.1, 0.3, 0.2, 0.4] * 600,
                "summary": {
                    "duration": 14.7,
                    "mood": "playful",
                    "key_frame": "00:04",
                    "suggested_use": "main clip",
                },
                "metadata": {"width": 1280, "height": 720, "fps": 30, "has_audio": True},
            }
        ],
        "agent_context": {
            "materials": [{"material_id": "clip1", "selected": True, "name": "source.mp4", "metadata": {"huge": "x" * 2000}}],
            "targets": [{"material_id": "clip1"}],
            "current_target": {"material_id": "clip1", "metadata": {"huge": "x" * 2000}},
            "time_references": [{"id": "duplicate"}],
        },
    }


@pytest.mark.parametrize(("user_request", "expected_category"), REGRESSION_CASES)
def test_plan_v2_prompt_slimming_regression_cases_under_8k(user_request: str, expected_category: str) -> None:
    effective_request, _raw = build_effective_request(user_request)
    project_state = strip_for_planning(_heavy_project_state(), effective_request)
    categories = infer_prompt_categories(effective_request, input_path="/tmp/source.mp4", project_state=project_state)
    prompt = build_primitive_plan_system_prompt(categories, has_video_context=True)
    payload = {
        "request": effective_request,
        "input_path": "/tmp/source.mp4",
        "output_path": "/tmp/out.mp4",
        "project_state": project_state,
        "video_context": {
            "duration": 14.7,
            "mood": "playful",
            "key_frame": "00:04",
            "suggested_use": "main clip",
        },
    }
    budget = token_budget(prompt, payload)

    assert budget["total_tokens_est"] <= 8000
    assert expected_category in categories
