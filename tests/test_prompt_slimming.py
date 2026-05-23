from __future__ import annotations

from gemia.ai.gemini_adapter import build_primitive_plan_system_prompt
from gemia.ai.prompt_slimming import (
    build_effective_request,
    infer_prompt_categories,
    strip_font_library,
    strip_for_planning,
    token_budget,
)
from gemia.registry import catalog_for_prompt


REGRESSION_CASES = [
    ("裁剪", "裁前10秒"),
    ("加速", "把视频加速2倍"),
    ("慢放", "把关键动作做成0.5倍慢动作"),
    ("暖色调色", "做暖色调色"),
    ("赛博朋克", "改成赛博朋克风格"),
    ("字幕", "给口播加中文字幕"),
    ("标题卡", "开头加一个标题卡 Lumeri"),
    ("图片3s视频", "把图片做成3秒视频"),
    ("去噪", "视频降噪并保持细节"),
    ("锐化", "让画面更清晰锐化"),
    ("模糊背景", "把背景虚化"),
    ("转场", "两段素材之间加溶解转场"),
    ("合并", "合并所有保留片段"),
    ("多素材目标", "帮我把所有素材统一调色"),
    ("时间范围", "只处理我框选的时间段"),
    ("Blender空间", "用 LumeriLink to Blender 做空间效果"),
    ("HTML/Lottie", "加一个 Lottie lower third"),
    ("人脸修饰", "轻微磨皮并调整脸型"),
    ("生成broll", "根据这段生成一个 b-roll"),
    ("模糊请求ask", "帮我做得更好看一点"),
]


def _fat_project_state() -> dict:
    return {
        "clips": [
            {
                "id": "clip1",
                "name": "demo.mp4",
                "serverPath": "/tmp/demo.mp4",
                "mediaKind": "video",
                "duration": 12,
                "thumbnailSrc": "/thumb.jpg",
                "previewSrc": "/preview.mp4",
                "thumbnailStrip": ["x"] * 80,
                "waveformPeaks": [0.1] * 1200,
                "summary": {
                    "duration": 12,
                    "mood": "casual",
                    "key_frame": "00:03",
                    "suggested_use": "opening",
                },
            }
        ],
        "timeReferences": [{"id": "r1", "kind": "range", "start": 2, "end": 5}],
    }


def test_plan_v2_prompt_regression_cases_stay_under_8k() -> None:
    project = _fat_project_state()
    max_tokens = 0
    for _name, request in REGRESSION_CASES:
        effective, raw = build_effective_request(request)
        categories = infer_prompt_categories(effective, input_path="/tmp/demo.mp4", project_state=project)
        prompt = build_primitive_plan_system_prompt(categories, has_video_context=True)
        payload = {
            "request": effective,
            "raw_request": raw,
            "input_path": "/tmp/demo.mp4",
            "output_path": "/tmp/out.mp4",
            "project_state": strip_for_planning(project, effective),
        }
        max_tokens = max(max_tokens, token_budget(prompt, payload)["total_tokens_est"])

    assert max_tokens <= 8000


def test_plan_v2_prompt_is_much_smaller_than_full_catalog_baseline() -> None:
    project = _fat_project_state()
    full_catalog_tokens = token_budget(catalog_for_prompt(), {"project_state": project})["total_tokens_est"]
    prompt = build_primitive_plan_system_prompt(["timeline"])
    slim_payload = {
        "request": "裁前10秒",
        "input_path": "/tmp/demo.mp4",
        "output_path": "/tmp/out.mp4",
        "project_state": strip_for_planning(project, "裁前10秒"),
    }
    slim_tokens = token_budget(prompt, slim_payload)["total_tokens_est"]

    assert full_catalog_tokens > 25_000
    assert slim_tokens < 6_000


def test_font_library_keeps_google_fonts_only_for_text_requests() -> None:
    font_library = {
        "default_font_id": "f1",
        "fonts": [{"font_id": "f1"}, {"font_id": "f2"}],
        "google_fonts": [{"google_family": f"Font{i}"} for i in range(8)],
    }

    plain = strip_font_library(font_library, "做暖色调色")
    text = strip_font_library(font_library, "加字幕")

    assert "google_fonts" not in plain
    assert plain["fonts"] == [{"font_id": "f1"}]
    assert len(text["fonts"]) == 2
    assert len(text["google_fonts"]) == 5
