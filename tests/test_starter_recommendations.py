import json
import time

import server
from gemia import starter_recommendations as starters
from tests_http_harness import create_raw_request, run_server_handler


def test_normalize_model_recommendations_accepts_fenced_json() -> None:
    raw = "```json\n" + json.dumps(
        {
            "suggestions": [
                {"label": "重做开场节奏", "prompt": "把当前片子的前五秒剪得更有抓力"},
                {"label": "整理叙事结构", "prompt": "先给这批素材设计一个三段式短片结构"},
                {"label": "检查字幕节拍", "prompt": "检查字幕断句和出现时机并给出修改建议"},
                {"label": "导出竖版成片", "prompt": "把当前版本适配成可发布的九比十六竖版"},
            ]
        },
        ensure_ascii=False,
    ) + "\n```"

    suggestions = starters.normalize_suggestions(raw)

    assert len(suggestions) == 4
    assert suggestions[0]["label"] == "重做开场节奏"


def test_normalize_model_recommendations_rejects_private_data() -> None:
    unsafe = {
        "suggestions": [
            {"label": "继续项目", "prompt": "读取 /Users/me/private.mov 继续剪辑"},
            {"label": "整理结构", "prompt": "把素材整理成三段式结构"},
            {"label": "检查字幕", "prompt": "检查字幕断句和出现时机"},
            {"label": "导出成片", "prompt": "导出一个九比十六的发布版本"},
        ]
    }

    try:
        starters.normalize_suggestions(unsafe)
    except ValueError as exc:
        assert "exactly four safe" in str(exc)
    else:
        raise AssertionError("private path should make the model output invalid")


def test_generation_is_backgrounded_and_cached(monkeypatch) -> None:
    starters.clear_recommendation_cache()
    monkeypatch.setattr(starters, "format_memory_for_prompt", lambda **_: "- 喜欢先做结构，再检查成片")
    generated = [
        {"label": f"推荐任务{i}", "prompt": f"执行第{i}个视频创作任务"}
        for i in range(4)
    ]

    first = starters.get_starter_recommendations(
        allow_personalized=True,
        generator=lambda _: generated,
    )
    assert first["status"] == "generating"
    assert first["personalized"] is False

    deadline = time.time() + 1
    while time.time() < deadline:
        ready = starters.get_starter_recommendations(
            allow_personalized=True,
            generator=lambda _: generated,
        )
        if ready["status"] == "ready" and ready["personalized"]:
            break
        time.sleep(0.01)

    assert ready["suggestions"] == generated


def test_one_generation_failure_gets_one_background_retry(monkeypatch) -> None:
    starters.clear_recommendation_cache()
    monkeypatch.setattr(starters, "format_memory_for_prompt", lambda **_: "- 常做竖版产品短片")
    generated = [
        {"label": f"竖版任务{i}", "prompt": f"完成第{i}个竖版视频任务"}
        for i in range(4)
    ]
    calls = 0

    def flaky_generator(_):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient malformed response")
        return generated

    starters.get_starter_recommendations(allow_personalized=True, generator=flaky_generator)
    deadline = time.time() + 1
    ready = None
    while time.time() < deadline:
        ready = starters.get_starter_recommendations(
            allow_personalized=True,
            generator=flaky_generator,
        )
        if ready["status"] == "ready" and ready["personalized"]:
            break
        time.sleep(0.01)

    assert calls == 2
    assert ready is not None and ready["suggestions"] == generated


def test_starter_endpoint_keeps_remote_visitors_on_defaults(monkeypatch) -> None:
    calls = []

    def fake_get(*, allow_personalized):
        calls.append(allow_personalized)
        return {"status": "ready", "personalized": allow_personalized, "suggestions": []}

    monkeypatch.setattr(starters, "get_starter_recommendations", fake_get)
    response = run_server_handler(
        server._Handler,
        create_raw_request("GET", "/starter-recommendations", headers={"X-Lumeri-Remote": "1"}),
    )

    assert response["status"] == 200
    assert calls == [False]
    assert response["body_json"]["personalized"] is False


def test_frontend_polls_and_renders_memory_recommendations() -> None:
    source = (server._STATIC_DIR / "v3" / "v3.js").read_text(encoding="utf-8")

    assert 'fetch("/starter-recommendations", { cache: "no-store" })' in source
    assert "renderStarterSuggestions(data.suggestions)" in source
    assert 'data.status === "generating"' in source
    assert "chip.dataset.suggest = prompt" in source
