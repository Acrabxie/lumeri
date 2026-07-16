from __future__ import annotations

import importlib

import pytest

from gemia.ai.google_genai_client import VertexAPIError
from gemia.model_strength import (
    is_model_unavailable_error,
    media_model_failover_chain,
    rank_media_models,
    strongest_media_model,
)


def test_known_media_models_are_ranked_by_code_owned_strength() -> None:
    ranked = rank_media_models(
        "video",
        "vertex",
        ("veo-3.0-fast-generate-001", "custom-video-99-ultra", "veo-3.1-fast-generate-preview"),
    )
    assert ranked[0] == "veo-3.1-fast-generate-preview"
    assert ranked[-1] == "custom-video-99-ultra"


def test_failover_chain_is_strongest_to_weakest() -> None:
    assert media_model_failover_chain("video", "vertex")[:2] == [
        "veo-3.1-generate-preview",
        "veo-3.1-fast-generate-preview",
    ]


def test_only_explicit_model_unavailable_errors_trigger_failover() -> None:
    assert is_model_unavailable_error(
        VertexAPIError("model not found", status=404, body_tail="unknown model")
    )
    assert not is_model_unavailable_error(VertexAPIError("quota exceeded", status=429))
    assert not is_model_unavailable_error(VertexAPIError("safety policy", status=400))


@pytest.mark.parametrize(
    ("slot", "backend", "expected"),
    (
        ("image", "vertex", "gemini-3.1-flash-image-preview"),
        ("image", "openrouter", "google/gemini-3.1-flash-image-preview"),
        ("image", "imagen", "imagen-4.0-ultra-generate-001"),
        ("video", "vertex", "veo-3.1-generate-preview"),
        ("video", "openrouter", "google/veo-3.1"),
        ("video", "gemini", "veo-3.1-generate-preview"),
        ("audio", "vertex", "lyria-3-pro-preview"),
        ("audio", "gemini", "lyria-3-pro-preview"),
    ),
)
def test_strongest_media_model_ignores_weaker_or_unknown_override(slot, backend, expected) -> None:
    assert strongest_media_model(slot, backend, ("custom-99-ultra", "legacy-fast-model")) == expected


def test_vertex_tools_ignore_weak_environment_and_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    config_dir = tmp_path / ".gemia"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        '{"vertex_video_model":"veo-3.0-fast-generate-001","vertex_audio_model":"lyria-002"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("VERTEX_VIDEO_MODEL", "veo-3.1-fast-generate-preview")
    monkeypatch.setenv("VERTEX_AUDIO_MODEL", "lyria-002")

    from gemia.tools import generate_audio, generate_video

    importlib.reload(generate_video)
    importlib.reload(generate_audio)
    assert generate_video._model() == "veo-3.1-generate-preview"
    assert generate_audio._model() == "lyria-3-pro-preview"


def test_openrouter_image_client_ignores_flash_tier_and_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("GEMIA_IMAGE_MODEL", "google/gemini-2.5-flash-image")

    from gemia.ai.generative_client import GenerativeClient

    client = GenerativeClient(model_tier="flash")
    assert client._model == "google/gemini-3.1-flash-image-preview"


def test_openrouter_image_silently_uses_next_model_when_strongest_is_unavailable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    from gemia.ai.generative_client import GenerativeClient

    client = GenerativeClient()
    attempts: list[str] = []

    def fake_post(url, payload):
        attempts.append(payload["model"])
        if len(attempts) == 1:
            raise RuntimeError("Nano Banana OpenRouter API HTTP 404: model not found")
        return {"ok": True}

    monkeypatch.setattr(client, "_post_json", fake_post)
    assert client._post_json_with_model_failover("https://example.invalid", {"model": client._model}) == {"ok": True}
    assert attempts == [
        "google/gemini-3.1-flash-image-preview",
        "google/gemini-2.5-flash-image",
    ]
    assert client._model == "google/gemini-2.5-flash-image"


def test_model_api_payload_exposes_locked_media_priorities(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    from gemia import memory

    memory = importlib.reload(memory)
    policy = memory.model_selection_payload()["media_strength_policy"]
    assert policy["image"]["vertex"]["active"] == "gemini-3.1-flash-image-preview"
    assert policy["video"]["vertex"]["active"] == "veo-3.1-generate-preview"
    assert policy["audio"]["vertex"]["active"] == "lyria-3-pro-preview"
    assert all(
        backend["locked"]
        for slot in policy.values()
        for backend in slot.values()
    )
    assert policy["video"]["vertex"]["unavailable_policy"] == "silent_next_strongest"
