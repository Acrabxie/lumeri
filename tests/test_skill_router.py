from __future__ import annotations

from statistics import median

from gemia.ai.skill_router import clear_skill_cache, load_skill_metadata, route


SAMPLES = [
    ("裁前 3 秒", {"timeline-ops"}),
    ("加速 2 倍", {"timeline-ops"}),
    ("慢动作 0.5 倍", {"timeline-ops"}),
    ("暖色调色，稍微提亮", {"color-grade"}),
    ("做赛博朋克调色", {"color-grade"}),
    ("加中文字幕", {"html-graphics"}),
    ("做一个标题卡", {"html-graphics"}),
    ("把图片按 3 秒视频处理", {"timeline-ops"}),
    ("视频降噪", {"motion-deblur"}),
    ("锐化并恢复细节", {"ultrasharpen"}),
    ("转场后统一调色", {"transition", "color-grade"}),
    ("裁剪后加字幕并加速", {"timeline-ops", "html-graphics"}),
    ("LumeriLink to Blender 做空间标题", {"blender-link", "html-graphics"}),
    ("先抠像再加 lower third", {"composite-blend", "html-graphics"}),
    ("参考达芬奇剪辑软件做抠像和 power window", {"composite-blend"}),
    ("参考 DaVinci Resolve 的 color page 做自然调色", {"color-grade"}),
    ("油画风格再做暖色调色", {"stylize-art", "color-grade"}),
    ("磨皮并轻微瘦脸", {"blemish-removal", "face-reshaper"}),
    ("去模糊后再锐化", {"motion-deblur", "ultrasharpen"}),
    ("生成一段 b-roll 并加字幕", {"generative", "html-graphics"}),
    ("检测场景然后合并高光", {"analysis", "timeline-ops"}),
    ("加淡入淡出转场并生成 broll", {"transition", "generative"}),
    ("模糊背景", {"blur-defocus"}),
    ("两个片段加淡入淡出转场", {"transition"}),
    ("做一个六片相机快门光圈转场", {"transition"}),
    ("合并所有素材", {"timeline-ops"}),
    ("LumeriLink to Blender 做空间效果", {"blender-link"}),
    ("人脸磨皮，去掉瑕疵", {"blemish-removal"}),
    ("给这段视频做人脸跟踪", {"face-tracking"}),
    ("track face and show the tracking trail", {"face-tracking"}),
    ("去 Pexels 搜索一段城市夜景 b-roll", {"stock-media"}),
    ("从 Pixabay 抓取山间湖泊背景素材", {"stock-media"}),
    ("找一个孩子在抓娃娃机的视频插入二段素材中间", {"stock-media", "timeline-ops"}),
    ("让 Lumeri 自己写微函数，必要时修改底层源码来做文字图层", {"creative-runtime"}),
    ("自己插入图层并编辑文字，不要只做剪辑转场", {"creative-runtime"}),
    ("先用代码渲染结果，review 再写代码补微函数", {"creative-runtime"}),
    ("把参考资料做成每个效果都有小样的创作视频，做完后自审再局部修改", {"creative-runtime", "generative"}),
    ("优化一下商业广告的文字、图案和动效效果", {"ad-graphics"}),
    ("给产品卖点加一个 price badge 和 CTA 结尾卡", {"ad-graphics"}),
    ("让它好看点", {"fallback"}),
    ("再处理一下", {"fallback"}),
    ("在吗", {"fallback"}),
    ("???", {"fallback"}),
    ("", {"fallback"}),
]


def test_skill_metadata_loads_planner_skills() -> None:
    skills = load_skill_metadata(refresh=True)

    assert len(skills) == 23
    assert "transition" in skills
    assert "lumeri-execution" in skills
    assert "stock-media" in skills
    assert "creative-runtime" in skills
    assert "face-tracking" in skills
    assert "ad-graphics" in skills
    assert "何时不用我" in skills["transition"].description
    assert "何时不用我" in skills["lumeri-execution"].description
    assert "gemia.video.stock_media.fetch_stock_media" in skills["stock-media"].primitives
    assert "gemia.video.creative_runtime.write_development_patch_brief" in skills["creative-runtime"].primitives
    assert "gemia.video.face_tracking.render_face_tracking_plan" in skills["face-tracking"].primitives
    assert "gemia.video.ad_graphics.render_ad_title_pack" in skills["ad-graphics"].primitives
    assert "gemia.video.transitions.transition_dissolve" in skills["transition"].primitives


def test_keyword_router_30_samples_recall_and_latency() -> None:
    clear_skill_cache()
    load_skill_metadata(refresh=True)
    results = [route(request) for request, _expected in SAMPLES]

    hits = 0
    latencies = []
    for (request, expected), result in zip(SAMPLES, results):
        latencies.append(result.latency_ms)
        if expected == {"fallback"}:
            assert result.source == "fallback", request
            hits += 1
        elif expected.issubset(set(result.skills)):
            assert result.source == "keyword", request
            hits += 1

    assert hits / len(SAMPLES) >= 0.95
    assert max(latencies[1:]) <= 5.0


def test_lumeri_execution_routes_only_for_top_level_workflow() -> None:
    clear_skill_cache()

    total = route("Lumeri 总控先做 preflight，然后进入 Timeline Build 阶段")
    color = route("暖色调色，稍微提亮")

    assert total.source == "keyword"
    assert total.skills == ["lumeri-execution"]
    assert color.skills == ["color-grade"]


def test_creative_runtime_routes_open_authoring_requests() -> None:
    clear_skill_cache()

    result = route("现在 Lumeri 太受限了，应该自己插入图层、编辑文字，必要时自己写微函数")

    assert result.source == "keyword"
    assert result.skills[0] == "creative-runtime"
    assert "creative-runtime" in result.skills


def test_ad_graphics_routes_commercial_graphics_requests() -> None:
    clear_skill_cache()

    result = route("做一套商业广告感的产品卖点文字、CTA 按钮和扫光动效")

    assert result.source == "keyword"
    assert result.skills[0] == "ad-graphics"


def test_creative_ad_prompt_routes_runtime_graphics_and_generative() -> None:
    clear_skill_cache()

    result = route("只写 prompt 做一条商业广告视频，每个效果渲染小样，自审后继续推进")

    assert result.source == "keyword"
    assert "creative-runtime" in result.skills
    assert "ad-graphics" in result.skills
    assert "generative" in result.skills


def test_prompt_only_fallback_activates_creation_skills() -> None:
    clear_skill_cache()

    result = route(
        "让它好看点",
        project_state={"agent_context": {"prompt_only_creation": True}},
    )

    assert result.source == "keyword"
    assert result.skills[0] == "generative"


def test_llm_fallback_interface_is_default_off(monkeypatch) -> None:
    calls = 0

    def fake_llm(_request, _descriptions):
        nonlocal calls
        calls += 1
        return ["generative"]

    result = route("完全无法判断请求", llm_fallback=fake_llm)
    assert result.source == "fallback"
    assert calls == 0

    monkeypatch.setenv("GEMIA_SKILL_LLM_FALLBACK", "1")
    result = route("完全无法判断请求", llm_fallback=fake_llm)
    assert result.source == "llm"
    assert result.skills == ["generative"]
    assert calls == 1
