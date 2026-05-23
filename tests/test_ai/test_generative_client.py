from __future__ import annotations

import base64

import cv2
import numpy as np
import pytest

from gemia.ai.generative_client import GenerativeClient


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))


def _png_b64() -> str:
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return base64.b64encode(buf.tobytes()).decode("ascii")


def test_nano_banana_client_defaults_to_openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.delenv("GEMIA_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("GEMIA_IMAGE_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    client = GenerativeClient()

    assert client._backend == "openrouter"
    assert client._model == "google/gemini-2.5-flash-image"
    assert client.base_url == "https://openrouter.ai/api/v1"


def test_text_generation_posts_to_openrouter_chat_completions(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    captured = {}

    def fake_post_json(self, url, payload):
        captured["url"] = url
        captured["payload"] = payload
        return {
            "choices": [
                {
                    "message": {
                        "images": [{"image_url": {"url": f"data:image/png;base64,{_png_b64()}"}}],
                    }
                }
            ]
        }

    monkeypatch.setattr(GenerativeClient, "_post_json", fake_post_json)

    result = GenerativeClient().generate_image_from_text("cinematic ice-blue title card")

    assert result.shape == (8, 8, 3)
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["payload"]["model"] == "google/gemini-2.5-flash-image"
    assert captured["payload"]["modalities"] == ["image", "text"]
    assert captured["payload"]["messages"][0]["content"] == "cinematic ice-blue title card"


def test_image_edit_uses_openrouter_image_url_content(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    captured = {}

    def fake_post_json(self, url, payload):
        captured["url"] = url
        captured["payload"] = payload
        return {"choices": [{"message": {"content": f"data:image/png;base64,{_png_b64()}"}}]}

    monkeypatch.setattr(GenerativeClient, "_post_json", fake_post_json)
    img = np.zeros((8, 8, 3), dtype=np.float32)

    result = GenerativeClient().generate_image_from_image_and_text(img, "make it warmer")

    assert result.shape == (8, 8, 3)
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    content = captured["payload"]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "make it warmer"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_openai_api_key_only_allowed_when_base_url_is_explicit_openrouter(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMIA_OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMIA_IMAGE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-compatible-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")

    client = GenerativeClient()

    assert client.base_url == "https://openrouter.ai/api/v1"
    assert client._api_key == "openai-compatible-key"


def test_openai_key_is_not_reused_for_non_openrouter_base_url(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMIA_OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMIA_IMAGE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "not-an-image-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.sisyphusx.com/v1")

    try:
        GenerativeClient()
    except RuntimeError as exc:
        assert "OPENROUTER_API_KEY" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("GenerativeClient should not reuse OPENAI_API_KEY for non-OpenRouter base URL")


def test_extracts_openrouter_content_part_image(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    captured = {}

    def fake_post_json(self, url, payload):
        captured["url"] = url
        return {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "done"},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_png_b64()}"}},
                        ]
                    }
                }
            ]
        }

    monkeypatch.setattr(GenerativeClient, "_post_json", fake_post_json)

    result = GenerativeClient().generate_image_from_text("make a clean product card")

    assert result.shape == (8, 8, 3)
    assert captured["url"].endswith("/chat/completions")
