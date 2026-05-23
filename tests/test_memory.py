from __future__ import annotations

import importlib
import json

import pytest


MODEL_ENV_VARS = (
    "GEMIA_PLANNER_MODEL",
    "GEMINI_MODEL",
    "GEMIA_IMAGE_MODEL",
    "GEMIA_GEMINI_IMAGE_MODEL",
    "GEMIA_IMAGE_PRO_MODEL",
    "GEMIA_VIDEO_MODEL",
    "GEMIA_GEMINI_VIDEO_MODEL",
    "VEO_MODEL",
    "GEMIA_AUDIO_MODEL",
    "LYRIA_MODEL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "GEMIA_OPENROUTER_MODEL",
    "GEMIA_AI_PROVIDER",
    "GEMIA_PLANNER_PROVIDER",
    "GEMIA_OPENROUTER_API_KEY",
    "SISYPHUS_API_KEY",
    "GEMIA_SISYPHUS_API_KEY",
    "GEMIA_IMAGE_API_KEY",
    "SISYPHUS_BASE_URL",
    "GEMIA_IMAGE_BASE_URL",
    "SISYPHUS_IMAGE_MODEL",
    "NANO_BANANA_MODEL",
    "NANO_BANANA_PRO_MODEL",
    "OPENROUTER_IMAGE_MODEL",
    "OPENROUTER_IMAGE_PRO_MODEL",
    "OPENROUTER_IMAGE_URL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
)


def _clear_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in MODEL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_bootstrap_memory_creates_expected_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    from gemia.memory import bootstrap_memory

    paths = bootstrap_memory(day="2026-04-27")

    for key in ("root", "roles", "queue", "memory", "model_profile", "daily"):
        assert paths[key]
    assert (tmp_path / ".gemia" / "memory" / "ROLES.md").exists()
    assert (tmp_path / ".gemia" / "memory" / "QUEUE.md").exists()
    assert (tmp_path / ".gemia" / "memory" / "MEMORY.md").exists()
    assert (tmp_path / ".gemia" / "memory" / "MODEL_PROFILE.json").exists()
    assert (tmp_path / ".gemia" / "memory" / "daily" / "2026-04-27.md").exists()


def test_model_profile_contains_gemia_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _clear_model_env(monkeypatch)
    from gemia.memory import load_model_profile

    profile = load_model_profile()
    models = profile["models"]

    assert models["planner"]["default"] == "google/gemini-3.1-pro-preview"
    assert models["planner"]["provider"] == "openrouter"
    assert "GeminiFlash3" not in models["planner"]["aliases"]
    assert models["planner"]["variants"]["fast"] == "google/gemini-3-flash-preview"
    assert models["image"]["default"] == "google/gemini-2.5-flash-image"
    assert models["image"]["provider"] == "openrouter/nano-banana"
    assert models["video"]["default"] == "veo-3.1-generate-preview"
    assert models["audio"]["default"] == "lyria-3-pro-preview"


def test_memory_writer_rejects_secret_fields(tmp_path) -> None:
    from gemia.memory import write_memory_json

    with pytest.raises(ValueError):
        write_memory_json(tmp_path / "bad.json", {"openrouter_api_key": "sk-test"})


def test_resolve_model_priority_env_config_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _clear_model_env(monkeypatch)
    from gemia.memory import bootstrap_memory, resolve_model

    bootstrap_memory()
    cfg_path = tmp_path / ".gemia" / "config.json"
    cfg_path.write_text(json.dumps({"planner_model": "config-planner"}, indent=2), encoding="utf-8")

    assert resolve_model("planner") == "config-planner"

    monkeypatch.setenv("GEMIA_PLANNER_MODEL", "env-planner")
    assert resolve_model("planner") == "env-planner"

    monkeypatch.delenv("GEMIA_PLANNER_MODEL")
    cfg_path.write_text("{}", encoding="utf-8")
    assert resolve_model("planner") == "google/gemini-3.1-pro-preview"


def test_clients_use_model_profile_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _clear_model_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test-key")

    from gemia.ai.gemini_adapter import GeminiAdapter
    from gemia.ai.generative_client import GenerativeClient
    from gemia.ai.veo_client import VeoClient

    adapter = GeminiAdapter(log_dir=tmp_path / "logs")
    image_client = GenerativeClient()
    video_client = VeoClient()

    assert adapter.gemini_model == "gemini-3.1-pro-preview"
    assert adapter.model == "google/gemini-3.1-pro-preview"
    assert image_client._model == "google/gemini-2.5-flash-image"
    assert image_client.base_url == "https://openrouter.ai/api/v1"
    assert video_client.model == "google/veo-3.1"


def test_model_profile_endpoint_payload_does_not_expose_keys(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _clear_model_env(monkeypatch)
    cfg_dir = tmp_path / ".gemia"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.json").write_text(
        json.dumps(
            {
                "openrouter_api_key": "sk-private",
                "gemini_api_key": "gemini-private",
                "planner_model": "config-planner",
            }
        ),
        encoding="utf-8",
    )

    import server

    server = importlib.reload(server)
    payload = server._model_profile_payload()
    dumped = json.dumps(payload, ensure_ascii=False)

    assert payload["resolved_models"]["planner"]["model"] == "config-planner"
    assert "sk-private" not in dumped
    assert "gemini-private" not in dumped
    assert "openrouter_api_key" not in dumped
    assert "gemini_api_key" not in dumped


def test_server_image_key_status_uses_openrouter_image_keys(tmp_path, monkeypatch) -> None:
    _clear_model_env(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    import server

    server = importlib.reload(server)
    monkeypatch.setattr(server, "_CONFIG_PATH", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(
        json.dumps({"nano_banana_api_key": "openrouter-private-key", "openrouter_image_url": "https://openrouter.ai/api/v1"}),
        encoding="utf-8",
    )

    assert server._has_valid_image_key()

    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-private-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    assert server._has_valid_image_key()
