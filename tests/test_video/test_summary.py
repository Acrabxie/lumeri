from __future__ import annotations

import json

from gemia.video import summary


def test_video_summarize_falls_back_for_missing_video(tmp_path) -> None:
    missing = tmp_path / "missing.mp4"

    result = summary.video_summarize(str(missing))

    assert result["status"] == "missing"
    assert result["backend"] == "metadata"
    assert result["video_path"] == str(missing)
    assert "does not exist" in result["summary"]


def test_video_summarize_uses_gemini_adapter_when_available(tmp_path, monkeypatch) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake video")

    class FakeAdapter:
        def can_read_video(self, input_path: str) -> bool:
            return input_path == str(video)

        async def generate_video_context_json(self, system_prompt, user_payload, tag="video-summary"):
            assert "video summary analyst" in system_prompt
            assert user_payload["input_path"] == str(video)
            assert tag == "video-summary"
            return {
                "duration": 8.3,
                "summary": "A performer opens on a lit stage.",
                "mood": "dramatic",
                "key_frame": "00:01 lights rise",
                "suggested_use": "opening beat",
                "keep": True,
            }

    monkeypatch.setattr("gemia.ai.gemini_adapter.GeminiAdapter", FakeAdapter)

    result = summary.video_summarize(str(video))

    assert result["status"] == "summarized"
    assert result["backend"] == "gemini_native"
    assert result["duration"] == 8.3
    assert result["summary"] == "A performer opens on a lit stage."
    assert result["mood"] == "dramatic"
    assert result["key_frame"] == "00:01 lights rise"
    assert result["suggested_use"] == "opening beat"
    assert result["keep"] is True


def test_model_summary_cannot_override_probed_duration() -> None:
    result = summary._merge_model_context(
        {
            "duration": 12.5,
            "summary": "Metadata-only summary.",
            "mood": "unknown",
            "key_frame": "metadata only",
            "suggested_use": "Review manually.",
            "keep": True,
        },
        {
            "duration": 3.0,
            "summary": "Model summary text.",
            "mood": "clean",
            "key_frame": "00:01",
            "suggested_use": "opening",
            "keep": True,
        },
    )

    assert result["duration"] == 12.5
    assert result["summary"] == "Model summary text."
    assert result["model_context"]["duration"] == 3.0


def test_batch_summarize_writes_project_summary_json(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    video_a = tmp_path / "a.mp4"
    video_b = tmp_path / "b.mp4"
    video_a.write_bytes(b"a")
    video_b.write_bytes(b"b")

    def fake_summarize(path: str) -> dict:
        return {"video_path": path, "status": "fallback"}

    monkeypatch.setattr(summary, "video_summarize", fake_summarize)

    results = summary.batch_summarize([str(video_a), str(video_b)])

    saved = json.loads((tmp_path / "project_summary.json").read_text())
    assert saved == results
    assert [item["video_path"] for item in saved] == [str(video_a), str(video_b)]
