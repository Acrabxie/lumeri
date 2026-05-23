from __future__ import annotations

import copy
import json
import os
from datetime import date
from pathlib import Path
from typing import Any


SECRET_FIELD_NAMES = {
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
}
SECRET_FIELD_SUFFIXES = (
    "_api_key",
    "_apikey",
    "_authorization",
    "_password",
    "_secret",
    "_token",
)

DEFAULT_MODEL_PROFILE: dict[str, Any] = {
    "schema_version": 1,
    "updated_at": "2026-05-04",
    "description": "Gemia canonical model defaults. API keys and credentials must not be stored here.",
    "models": {
        "planner": {
            "default": "google/gemini-3.1-pro-preview",
            "provider": "openrouter",
            "aliases": ["LumeriPlanner", "Gemini31Pro", "Gemini3.1pro"],
            "variants": {"fast": "google/gemini-3-flash-preview", "reviewer": "openai/gpt-5.4"},
            "env": ["OPENROUTER_MODEL", "GEMIA_OPENROUTER_MODEL", "GEMIA_PLANNER_MODEL", "GEMINI_MODEL"],
            "config": ["openrouter_model", "planner_model", "gemini_model"],
            "description": "Primary planning model through OpenRouter. Legacy Gemini model names are mapped where possible.",
        },
        "image": {
            "default": "google/gemini-2.5-flash-image",
            "provider": "openrouter/nano-banana",
            "aliases": ["Nano Banana", "Gemini 2.5 Flash Image"],
            "tiers": {
                "flash": "google/gemini-2.5-flash-image",
                "pro": "google/gemini-3.1-flash-image-preview",
            },
            "env": ["GEMIA_IMAGE_MODEL", "NANO_BANANA_MODEL", "OPENROUTER_IMAGE_MODEL"],
            "env_by_tier": {
                "flash": ["GEMIA_IMAGE_MODEL", "NANO_BANANA_MODEL", "OPENROUTER_IMAGE_MODEL"],
                "pro": ["GEMIA_IMAGE_PRO_MODEL", "NANO_BANANA_PRO_MODEL", "OPENROUTER_IMAGE_PRO_MODEL"],
            },
            "config": ["image_model", "nano_banana_model", "openrouter_image_model"],
            "config_by_tier": {
                "flash": ["image_model", "nano_banana_model", "openrouter_image_model"],
                "pro": ["image_pro_model", "nano_banana_pro_model", "openrouter_image_pro_model"],
            },
            "description": "Default image generation and image editing model through OpenRouter Nano Banana.",
        },
        "video": {
            "default": "veo-3.1-generate-preview",
            "aliases": ["veo3.1quality", "Veo 3.1 quality"],
            "variants": {"fast": "veo-3.1-fast-generate-preview"},
            "env": ["GEMIA_VIDEO_MODEL", "GEMIA_GEMINI_VIDEO_MODEL", "VEO_MODEL"],
            "config": ["video_model", "veo_model", "gemini_video_model"],
            "description": "Default video generation model.",
        },
        "audio": {
            "default": "lyria-3-pro-preview",
            "aliases": ["lyric2pro", "Lyria 3 Pro"],
            "variants": {"clip": "lyria-3-clip-preview"},
            "env": ["GEMIA_AUDIO_MODEL", "LYRIA_MODEL"],
            "config": ["audio_model", "lyria_model"],
            "description": "Default audio generation model reserved for future audio-generation primitives.",
        },
    },
    "safety": {
        "no_secrets": True,
        "secret_storage": "~/.gemia/config.json",
    },
}


def memory_root() -> Path:
    return Path.home() / ".gemia" / "memory"


def roles_path() -> Path:
    return memory_root() / "ROLES.md"


def queue_path() -> Path:
    return memory_root() / "QUEUE.md"


def durable_memory_path() -> Path:
    return memory_root() / "MEMORY.md"


def model_profile_path() -> Path:
    return memory_root() / "MODEL_PROFILE.json"


def daily_dir() -> Path:
    return memory_root() / "daily"


def daily_path(day: str | date | None = None) -> Path:
    if day is None:
        day_str = date.today().isoformat()
    elif isinstance(day, date):
        day_str = day.isoformat()
    else:
        day_str = day
    return daily_dir() / f"{day_str}.md"


def config_path() -> Path:
    return Path.home() / ".gemia" / "config.json"


def default_model_profile() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_MODEL_PROFILE)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return copy.deepcopy(default)


def read_user_config() -> dict[str, Any]:
    data = _read_json(config_path(), {})
    return data if isinstance(data, dict) else {}


def _clean_string(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _has_secret_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in SECRET_FIELD_NAMES or any(lowered.endswith(suffix) for suffix in SECRET_FIELD_SUFFIXES)


def assert_memory_safe(payload: Any, *, path: str = "$") -> None:
    """Reject payloads that try to store obvious secret-bearing keys."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_str = str(key)
            if _has_secret_key(key_str):
                raise ValueError(f"Refusing to write secret-like field to Gemia memory: {path}.{key_str}")
            assert_memory_safe(value, path=f"{path}.{key_str}")
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            assert_memory_safe(item, path=f"{path}[{index}]")


def write_memory_json(path: Path, payload: Any) -> None:
    assert_memory_safe(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text_if_missing(path: Path, text: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json_if_missing(path: Path, payload: Any) -> None:
    if path.exists():
        return
    write_memory_json(path, payload)


def bootstrap_memory(day: str | date | None = None) -> dict[str, str]:
    """Create Gemia memory seed files without overwriting existing notes."""
    root = memory_root()
    root.mkdir(parents=True, exist_ok=True)
    daily_dir().mkdir(parents=True, exist_ok=True)

    _write_text_if_missing(
        root / "README.md",
        "# Gemia Memory\n\n"
        "Gemia project memory lives here. Read shared agent memory first, then this layer.\n\n"
        "Do not store secrets, tokens, passwords, API keys, or raw private conversations here.\n",
    )
    _write_text_if_missing(
        roles_path(),
        "# Gemia Memory Roles\n\n"
        "## Model Defaults\n\n"
        "- Primary planner: `google/gemini-3.1-pro-preview` through OpenRouter (`LumeriPlanner`)\n"
        "- Image generation: `google/gemini-2.5-flash-image` through OpenRouter (`Nano Banana`)\n"
        "- Video generation: `veo-3.1-generate-preview` (`veo3.1quality`)\n"
        "- Audio generation: `lyria-3-pro-preview` (`lyric2pro`)\n",
    )
    _write_text_if_missing(
        queue_path(),
        "# Gemia Memory Queue\n\n## Active\n\n_None._\n\n## Pending\n\n_None._\n\n## Blocked\n\n_None._\n\n## Done\n\n_None._\n",
    )
    _write_text_if_missing(
        durable_memory_path(),
        "# Gemia Durable Memory\n\n"
        "- Gemia local memory root: `~/.gemia/memory/`\n"
        "- API keys remain in `~/.gemia/config.json`, not memory.\n",
    )
    _write_json_if_missing(model_profile_path(), default_model_profile())
    _write_text_if_missing(
        daily_path(day),
        f"# {daily_path(day).stem}\n\n"
        "- Gemia memory bootstrap is available. Add short raw progress notes here.\n",
    )
    return {
        "root": str(root),
        "roles": str(roles_path()),
        "queue": str(queue_path()),
        "memory": str(durable_memory_path()),
        "model_profile": str(model_profile_path()),
        "daily": str(daily_path(day)),
    }


def load_model_profile(*, bootstrap: bool = True) -> dict[str, Any]:
    if bootstrap:
        bootstrap_memory()
    profile = _read_json(model_profile_path(), default_model_profile())
    if not isinstance(profile, dict):
        return default_model_profile()
    models = profile.get("models")
    if not isinstance(models, dict):
        profile["models"] = default_model_profile()["models"]
    _migrate_model_profile(profile)
    return profile


def _migrate_model_profile(profile: dict[str, Any]) -> None:
    """Patch stale local defaults in memory without touching user secrets."""
    defaults = default_model_profile()
    models = profile.setdefault("models", {})
    if not isinstance(models, dict):
        profile["models"] = defaults["models"]
        return
    planner = models.get("planner")
    if isinstance(planner, dict) and _clean_string(planner.get("default")) in {"google/gemini-2.5-pro", "gemini-2.5-pro"}:
        planner["default"] = defaults["models"]["planner"]["default"]
    image = models.get("image")
    if isinstance(image, dict):
        image_default = _clean_string(image.get("default")).lower()
        image_provider = _clean_string(image.get("provider")).lower()
        if image_default in {"gpt-image-2", "gpt_image2", "gpt image2"} or image_provider == "sisyphus":
            models["image"] = copy.deepcopy(defaults["models"]["image"])


def _slot_default(slot: str, profile: dict[str, Any], tier: str | None = None) -> str:
    model_info = (profile.get("models") or {}).get(slot, {})
    if not isinstance(model_info, dict):
        return ""
    if tier:
        tiers = model_info.get("tiers")
        if isinstance(tiers, dict):
            tier_value = _clean_string(tiers.get(tier))
            if tier_value:
                return tier_value
    return _clean_string(model_info.get("default"))


def _slot_names(slot: str, profile: dict[str, Any], key: str, tier: str | None = None) -> list[str]:
    model_info = (profile.get("models") or {}).get(slot, {})
    if not isinstance(model_info, dict):
        return []
    names: list[str] = []
    by_tier = model_info.get(f"{key}_by_tier")
    if tier and isinstance(by_tier, dict):
        tier_names = by_tier.get(tier, [])
        if isinstance(tier_names, list):
            names.extend(str(item) for item in tier_names)
    base_names = model_info.get(key, [])
    if isinstance(base_names, list):
        names.extend(str(item) for item in base_names)
    return names


def resolve_model_with_source(
    slot: str,
    *,
    env_names: tuple[str, ...] | list[str] | None = None,
    config_keys: tuple[str, ...] | list[str] | None = None,
    fallback: str | None = None,
    tier: str | None = None,
) -> dict[str, str]:
    """Resolve a Gemia model by priority: env, config, profile, fallback."""
    profile = load_model_profile()
    env_candidates = list(env_names) if env_names is not None else _slot_names(slot, profile, "env", tier)
    config_candidates = list(config_keys) if config_keys is not None else _slot_names(slot, profile, "config", tier)

    for name in env_candidates:
        value = _clean_string(os.environ.get(name))
        if value:
            return {"model": value, "source": "env", "source_name": name, "slot": slot}

    config = read_user_config()
    for key in config_candidates:
        value = _clean_string(config.get(key))
        if value:
            return {"model": value, "source": "config", "source_name": key, "slot": slot}

    profile_default = _slot_default(slot, profile, tier)
    if profile_default:
        return {"model": profile_default, "source": "profile", "source_name": slot, "slot": slot}

    return {"model": fallback or "", "source": "fallback", "source_name": slot, "slot": slot}


def resolve_model(
    slot: str,
    *,
    env_names: tuple[str, ...] | list[str] | None = None,
    config_keys: tuple[str, ...] | list[str] | None = None,
    fallback: str | None = None,
    tier: str | None = None,
) -> str:
    return resolve_model_with_source(
        slot,
        env_names=env_names,
        config_keys=config_keys,
        fallback=fallback,
        tier=tier,
    )["model"]


def public_model_profile() -> dict[str, Any]:
    """Return model memory details safe for UI/API exposure."""
    profile = load_model_profile()
    return {
        "memory_root": str(memory_root()),
        "model_profile_path": str(model_profile_path()),
        "schema_version": profile.get("schema_version"),
        "updated_at": profile.get("updated_at"),
        "models": profile.get("models", {}),
        "resolved_models": {
            "planner": resolve_model_with_source("planner"),
            "image": resolve_model_with_source("image", tier="flash"),
            "video": resolve_model_with_source("video"),
            "audio": resolve_model_with_source("audio"),
        },
        "safety": {
            "no_secrets": True,
            "secret_storage": str(config_path()),
        },
    }
