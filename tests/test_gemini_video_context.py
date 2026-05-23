from __future__ import annotations

import asyncio
import base64
import json

from gemia.ai.ai_client import AIClient
from gemia.ai.gemini_adapter import GeminiAdapter, build_primitive_plan_system_prompt
from gemia.ai.provider_audit import audit_provider_payload
from gemia.ai.prompt_slimming import (
    build_effective_request,
    infer_prompt_categories,
    strip_for_planning,
    token_budget,
)


def test_native_parts_attach_inline_video_before_payload(tmp_path, monkeypatch) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake video bytes")
    monkeypatch.setenv("GEMIA_GEMINI_INLINE_VIDEO_MAX_BYTES", "1000")
    monkeypatch.setenv("GEMIA_GEMINI_VIDEO_FPS", "2")

    adapter = GeminiAdapter(api_key="gemini-test-key", log_dir=tmp_path / "logs")
    parts, meta = adapter._build_native_parts(
        {"request": "make a highlight", "input_path": str(video)},
        attach_video=True,
    )

    assert parts[0]["inline_data"]["mime_type"] == "video/mp4"
    assert parts[0]["inline_data"]["data"] == base64.b64encode(b"fake video bytes").decode("ascii")
    assert parts[0]["video_metadata"] == {"fps": 2.0}
    assert json.loads(parts[1]["text"])["input_path"] == str(video)
    assert meta["attached_media_count"] == 1
    assert meta["attached_media"][0]["mode"] == "inline_data"


def test_gemini_adapter_default_model_uses_gemia_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("GEMIA_PLANNER_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMIA_AI_PROVIDER", raising=False)

    adapter = GeminiAdapter(api_key="gemini-test-key", log_dir=tmp_path / "logs")

    assert adapter.provider == "gemini_native"
    assert adapter.gemini_model == "gemini-3.1-pro-preview"
    assert adapter.model == "gemini-3.1-pro-preview"


def test_openrouter_is_primary_planner_when_configured(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMIA_AI_PROVIDER", raising=False)

    adapter = GeminiAdapter(log_dir=tmp_path / "logs")

    assert adapter.provider == "openrouter"
    assert adapter.model == "google/gemini-3.1-pro-preview"
    assert not adapter.can_read_video("/tmp/source.mp4")


def test_openrouter_plan_response_is_parsed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")

    adapter = GeminiAdapter(log_dir=tmp_path / "logs")
    monkeypatch.setattr(
        adapter,
        "_post_openrouter",
        lambda system_prompt, user_payload, tag="openrouter": (
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "version": "2.0",
                                    "goal": "warm grade",
                                    "steps": [
                                        {
                                            "id": "step_1",
                                            "function": "gemia.picture.color.color_grade",
                                            "args": {"preset": "warm"},
                                            "input": "$input",
                                            "output": "$output",
                                        }
                                    ],
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
            {"message_count": 2},
        ),
    )

    plan = asyncio.run(
        adapter.generate_plan_json(
            "Return JSON only.",
            {"request": "暖色调色"},
            tag="openrouter-plan",
        )
    )

    assert plan["version"] == "2.0"
    log_file = next((tmp_path / "logs").glob("*openrouter-plan.json"))
    saved = json.loads(log_file.read_text())
    assert saved["request"]["backend"] == "openrouter"
    assert saved["usage"]["total_tokens"] == 15


def test_native_parts_use_files_api_when_over_inline_limit(tmp_path, monkeypatch) -> None:
    video = tmp_path / "clip.mov"
    video.write_bytes(b"fake video bytes")
    monkeypatch.setenv("GEMIA_GEMINI_INLINE_VIDEO_MAX_BYTES", "1")

    adapter = GeminiAdapter(api_key="gemini-test-key", log_dir=tmp_path / "logs")
    monkeypatch.setattr(
        adapter,
        "_upload_video_file",
        lambda path, mime_type: {"name": "files/abc", "uri": "https://files.example/abc", "mime_type": mime_type},
    )

    parts, meta = adapter._build_native_parts(
        {"request": "summarize it", "input_path": str(video)},
        attach_video=True,
    )

    assert parts[0]["file_data"] == {
        "mime_type": "video/quicktime",
        "file_uri": "https://files.example/abc",
    }
    assert meta["attached_media"][0]["mode"] == "file_api"
    assert meta["attached_media"][0]["file_name"] == "files/abc"


class _FakeVideoAdapter:
    def __init__(self) -> None:
        self.context_payload = None
        self.plan_payload = None
        self.plan_attach_video = None

    def can_read_video(self, input_path: str) -> bool:
        return input_path.endswith(".mp4")

    async def generate_video_context_json(self, system_prompt, user_payload, tag="video-context"):
        self.context_payload = user_payload
        return {
            "available": True,
            "summary": "A presenter speaks on camera.",
            "timeline": [{"timestamp": "00:01", "visual": "presenter", "audio": "speech", "edit_note": "good opener"}],
            "edit_opportunities": [{"timestamp": "00:01", "action": "caption", "reason": "speech"}],
        }

    async def generate_plan_json(self, system_prompt, user_payload, tag, *, attach_video=False):
        self.system_prompt = system_prompt
        self.plan_payload = user_payload
        self.plan_attach_video = attach_video
        return {
            "version": "2.0",
            "goal": "caption the presenter",
            "steps": [
                {
                    "id": "step_1",
                    "function": "gemia.video.subtitles.add_subtitle",
                    "args": {"text": "Hello"},
                    "input": "$input",
                    "output": "$output",
                }
            ],
        }


def test_plan_from_primitives_reads_video_then_plans_with_context() -> None:
    adapter = _FakeVideoAdapter()
    client = AIClient(adapter=adapter)  # type: ignore[arg-type]

    plan = asyncio.run(
        client.plan_from_primitives(
            "给这个口播视频加字幕并做轻微优化",
            input_path="/tmp/source.mp4",
            output_path="/tmp/out.mp4",
        )
    )

    assert plan["version"] == "2.0"
    assert adapter.context_payload["input_path"] == "/tmp/source.mp4"
    assert adapter.plan_payload["video_context"]["summary"] == "A presenter speaks on camera."
    assert adapter.plan_payload["planning_mode"]["passes"] == ["video_context", "primitive_plan", "execute"]
    assert adapter.plan_attach_video is False
    assert adapter.plan_payload["planning_prompt_budget"]["total_tokens_est"] <= 8000


def test_openrouter_input_txt_is_disabled_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    monkeypatch.setenv("GEMIA_INPUT_TXT_DIR", str(tmp_path / "desktop-inputs"))

    adapter = GeminiAdapter(log_dir=tmp_path / "logs")

    def fake_post(system_prompt, user_payload, tag="openrouter"):
        payload = {
            "model": adapter.openrouter_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
        }
        path = adapter._write_desktop_input_txt(
            tag=tag,
            endpoint="https://openrouter.ai/api/v1/chat/completions",
            request_body=payload,
            headers={"Authorization": "Bearer secret", "Content-Type": "application/json"},
            request_meta={"message_count": 2},
        )
        return (
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"version": "2.0", "goal": "ok", "steps": []})
                        }
                    }
                ]
            },
            {"desktop_input_txt": path},
        )

    monkeypatch.setattr(adapter, "_post_openrouter", fake_post)

    asyncio.run(adapter.generate_plan_json("SYSTEM PROMPT", {"request": "用户请求"}, tag="txt-probe"))

    txt_path = tmp_path / "desktop-inputs" / "latest.txt"
    assert not txt_path.exists()


def test_openrouter_input_txt_can_be_enabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    monkeypatch.setenv("GEMIA_INPUT_TXT_LOG", "1")
    monkeypatch.setenv("GEMIA_INPUT_TXT_DIR", str(tmp_path / "desktop-inputs"))

    adapter = GeminiAdapter(log_dir=tmp_path / "logs")
    payload = {
        "model": adapter.openrouter_model,
        "messages": [{"role": "system", "content": "SYSTEM PROMPT"}, {"role": "user", "content": "用户请求"}],
    }
    adapter._write_desktop_input_txt(
        tag="txt-probe",
        endpoint="https://openrouter.ai/api/v1/chat/completions",
        request_body=payload,
        headers={"Authorization": "Bearer secret", "Content-Type": "application/json"},
        request_meta={"message_count": 2},
    )
    txt_path = tmp_path / "desktop-inputs" / "latest.txt"
    text = txt_path.read_text(encoding="utf-8")
    assert "SYSTEM PROMPT" in text
    assert "用户请求" in text
    assert "Bearer secret" not in text
    assert "<redacted>" in text


def test_input_txt_mode_bypasses_plan_cache(monkeypatch) -> None:
    from gemia.ai.cache import plan_cache

    plan_cache.clear()
    monkeypatch.setenv("GEMIA_INPUT_TXT_LOG", "1")

    class FakePlanner:
        provider = "openrouter"
        model = "google/gemini-3.1-pro-preview"
        calls = 0

        def can_read_video(self, input_path: str) -> bool:
            return False

        async def generate_plan_json(self, system_prompt, user_payload, tag, *, attach_video=False):
            self.calls += 1
            return {"version": "2.0", "goal": "ok", "steps": []}

    adapter = FakePlanner()
    client = AIClient(adapter=adapter)  # type: ignore[arg-type]
    for _ in range(2):
        asyncio.run(
            client.plan_from_primitives(
                "暖色调色",
                input_path="/tmp/source.mp4",
                output_path="/tmp/out.mp4",
            )
        )

    assert adapter.calls == 2
    plan_cache.clear()


def test_effective_request_includes_clarifications() -> None:
    effective, raw = build_effective_request("帮我处理", {"q0": "暖色调色", "q1": "3秒"})
    assert raw == "帮我处理"
    assert "暖色调色" in effective
    assert "[clarified]" in effective


def test_strip_for_planning_removes_heavy_project_fields() -> None:
    project = {
        "timeReferences": [{"id": "t1", "start": 1, "end": 2}],
        "agent_time_references": [{"id": "old"}],
        "clips": [
            {
                "id": "clip1",
                "thumbnailStrip": ["a", "b"],
                "thumbnailSrc": "/thumb.jpg",
                "previewSrc": "/preview.mp4",
                "waveformPeaks": [0.1, 0.2],
                "summary": {"duration": 3, "mood": "warm"},
            }
        ],
        "agent_context": {
            "time_references": [{"id": "ctx"}],
            "current_target": {"material_id": "m1"},
            "targets": [{"material_id": "m1"}],
            "materials": [{"material_id": "m1", "selected": True, "name": "long metadata"}],
        },
    }

    stripped = strip_for_planning(project, "暖色调色")

    assert "agent_time_references" not in stripped
    assert stripped["timeReferences"][0]["id"] == "t1"
    clip = stripped["clips"][0]
    for key in ("thumbnailStrip", "thumbnailSrc", "previewSrc", "waveformPeaks"):
        assert key not in clip
    assert stripped["agent_context"]["materials"] == [{"material_id": "m1", "role": "target"}]
    assert "current_target" not in stripped["agent_context"]
    assert "targets" not in stripped["agent_context"]


def test_prompt_slimming_budget_is_under_8k_for_common_case() -> None:
    categories = infer_prompt_categories("帮我把这段视频暖色调色并加速2倍", input_path="/tmp/a.mp4")
    prompt = build_primitive_plan_system_prompt(categories)
    payload = {
        "request": "帮我把这段视频暖色调色并加速2倍",
        "input_path": "/tmp/a.mp4",
        "output_path": "/tmp/out.mp4",
        "project_state": strip_for_planning(
            {
                "clips": [
                    {
                        "id": "clip1",
                        "name": "a.mp4",
                        "serverPath": "/tmp/a.mp4",
                        "mediaKind": "video",
                        "duration": 10,
                        "thumbnailStrip": ["x"] * 100,
                        "waveformPeaks": [0.1] * 2000,
                    }
                ],
                "timeReferences": [{"id": "r1", "kind": "range", "start": 2, "end": 5}],
            },
            "帮我把这段视频暖色调色并加速2倍",
        ),
    }
    budget = token_budget(prompt, payload)
    assert budget["total_tokens_est"] <= 8000


def test_plan_from_primitives_strips_project_and_clarifies_request() -> None:
    adapter = _FakeVideoAdapter()
    client = AIClient(adapter=adapter)  # type: ignore[arg-type]

    asyncio.run(
        client.plan_from_primitives(
            "帮我处理",
            input_path="/tmp/source.mp4",
            output_path="/tmp/out.mp4",
            answers={"q0": "暖色调色"},
            project_state={
                "clips": [
                    {
                        "id": "clip1",
                        "mediaKind": "video",
                        "serverPath": "/tmp/source.mp4",
                        "thumbnailStrip": ["x"] * 100,
                        "waveformPeaks": [0.1] * 1000,
                    }
                ],
                "agent_time_references": [{"id": "old"}],
                "timeReferences": [{"id": "new", "start": 1, "end": 2}],
                "agent_context": {
                    "materials": [{"material_id": "m1", "selected": True, "name": "verbose"}],
                    "targets": [{"material_id": "m1"}],
                    "current_target": {"material_id": "m1"},
                    "time_references": [{"id": "ctx"}],
                },
            },
        )
    )

    payload = adapter.plan_payload
    assert payload["request"] == "帮我处理\n[clarified] 暖色调色"
    assert payload["raw_request"] == "帮我处理"
    assert payload["project_state"]["timeReferences"][0]["id"] == "new"
    assert "agent_time_references" not in payload["project_state"]
    assert "thumbnailStrip" not in payload["project_state"]["clips"][0]
    assert "waveformPeaks" not in payload["project_state"]["clips"][0]
    assert payload["project_state"]["agent_context"]["materials"] == [{"material_id": "m1", "role": "target"}]


def test_openrouter_payload_uses_message_cache_control(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    adapter = GeminiAdapter(log_dir=tmp_path / "logs")

    payload = adapter._openrouter_payload("SYSTEM", {"request": "hello"}, use_cache_control=True)

    content = payload["messages"][0]["content"]
    assert isinstance(content, list)
    assert content[0]["text"] == "SYSTEM"
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in payload


def test_openrouter_payload_uses_static_dynamic_and_user_messages(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    adapter = GeminiAdapter(log_dir=tmp_path / "logs")

    payload = adapter._openrouter_payload(
        "STATIC",
        {"request": "hello"},
        dynamic_system_prompt="DYNAMIC",
        use_cache_control=True,
    )

    assert len(payload["messages"]) == 3
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][0]["content"][0]["text"] == "STATIC"
    assert payload["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert payload["messages"][1] == {"role": "system", "content": "DYNAMIC"}
    assert payload["messages"][2]["role"] == "user"


def test_provider_payload_audit_splits_runtime_sections(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    adapter = GeminiAdapter(log_dir=tmp_path / "logs")

    payload = adapter._openrouter_payload(
        "STATIC",
        {
            "request": "加转场",
            "raw_request": "加转场",
            "active_primitive_specs": [
                {"name": "gemia.video.transitions.transition_dissolve", "args_schema": {}}
            ],
            "project_state": {"clips": [{"id": "clip1"}]},
            "planning_mode": {
                "selected_skills": ["transition"],
                "selected_primitives": ["gemia.video.transitions.transition_dissolve"],
            },
        },
        dynamic_system_prompt="DYNAMIC",
        use_cache_control=True,
    )

    audit = audit_provider_payload(payload)
    assert audit["static_contract_tokens"] > 0
    assert audit["active_skill_context_tokens"] > 0
    assert audit["active_specs_tokens"] > 0
    assert audit["project_context_tokens"] > 0
    assert audit["user_request_tokens"] > 0
    assert audit["total_provider_tokens"] >= audit["active_specs_tokens"]
    assert audit["selected_skills"] == ["transition"]
    assert audit["selected_primitives"] == ["gemia.video.transitions.transition_dissolve"]
