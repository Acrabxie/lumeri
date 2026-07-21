"""brain_config 金标准：脱敏、白名单、custom→openai、env 即时生效。"""
import os

import pytest

from gemia import brain_config as bc


def test_read_status_masks_keys():
    cfg = {
        "lumeri_v3_provider": "openai",
        "lumeri_v3_model": "gpt-5.5",
        "lumeri_v3_effort": "high",
        "openai_api_key": "sk-super-secret-value",
        "anthropic_api_key": "",
        "openrouter_api_key": "or-key",
        "vertex_project": "proj-1",
        "lumeri_anthropic_base_url": "https://gateway.example/v1/messages",
        "lumeri_anthropic_betas": "context-1m-2025-08-07",
    }
    st = bc.read_status(cfg)
    # 现状字段透传
    assert st["provider"] == "openai"
    assert st["model"] == "gpt-5.5"
    assert st["effort"] == "high"
    assert st["vertex_project"] == "proj-1"
    assert st["anthropic_base_url"] == "https://gateway.example/v1/messages"
    assert st["anthropic_betas"] == "context-1m-2025-08-07"
    # 密钥只给布尔
    assert st["has_key"] == {
        "openrouter": True,
        "gemini": False,
        "anthropic": False,
        "openai": True,
    }
    # 绝不泄漏任何明文密钥
    blob = str(st)
    assert "sk-super-secret-value" not in blob
    assert "or-key" not in blob
    # 供前端渲染的目录齐全
    assert [p["id"] for p in st["providers"]] == [
        "vertex", "gemini", "openai", "claude", "openrouter", "custom",
    ]
    assert st["efforts"] == bc.EFFORTS


def test_apply_update_whitelist_only():
    cfg = {"smtp": {"password": "keep-me"}, "cloudflare_email": {"api_token": "keep"}}
    body = {
        "provider": "openrouter",
        "model": "anthropic/claude-fable-5",
        "effort": "medium",
        "openrouter_api_key": "or-new",
        # 恶意/越界字段——必须被忽略
        "smtp": "HACK",
        "cloudflare_email": "HACK",
        "google_oauth_client_secret": "HACK",
    }
    out, changed = bc.apply_update(cfg, body)
    assert out["lumeri_v3_provider"] == "openrouter"
    assert out["lumeri_v3_model"] == "anthropic/claude-fable-5"
    assert out["openrouter_api_key"] == "or-new"
    # 敏感块原样保留、未被越界写入覆盖
    assert out["smtp"] == {"password": "keep-me"}
    assert out["cloudflare_email"] == {"api_token": "keep"}
    assert "google_oauth_client_secret" not in out
    assert os.environ.get("OPENROUTER_API_KEY") == "or-new"
    assert os.environ.get("LUMERI_V3_PROVIDER") == "openrouter"


def test_custom_maps_to_openai_with_base_url():
    out, _ = bc.apply_update({}, {
        "provider": "custom",
        "base_url": "https://gw.example/v1/chat/completions",
        "model": "my-model",
        "openai_api_key": "sk-x",
    })
    # custom 是 UI 概念 → 实际 openai 通道 + 自定义 base_url
    assert out["lumeri_v3_provider"] == "openai"
    assert out["lumeri_openai_base_url"] == "https://gw.example/v1/chat/completions"
    assert out["openai_api_key"] == "sk-x"
    assert os.environ.get("LUMERI_OPENAI_BASE_URL") == "https://gw.example/v1/chat/completions"


def test_claude_custom_endpoint_and_beta_are_whitelisted():
    out, changed = bc.apply_update({}, {
        "provider": "claude",
        "model": "claude-fable-5",
        "anthropic_base_url": "https://anyrouter.top/v1/messages",
        "anthropic_betas": "context-1m-2025-08-07",
    })
    assert out["lumeri_v3_provider"] == "claude"
    assert out["lumeri_anthropic_base_url"] == "https://anyrouter.top/v1/messages"
    assert out["lumeri_anthropic_betas"] == "context-1m-2025-08-07"
    assert "lumeri_anthropic_base_url" in changed
    assert os.environ.get("LUMERI_ANTHROPIC_BASE_URL") == "https://anyrouter.top/v1/messages"
    assert os.environ.get("LUMERI_ANTHROPIC_BETAS") == "context-1m-2025-08-07"


def test_blank_key_does_not_clobber():
    cfg = {"openai_api_key": "sk-existing"}
    out, changed = bc.apply_update(cfg, {"provider": "openai", "openai_api_key": ""})
    # 留空表单不清已存 key
    assert out["openai_api_key"] == "sk-existing"
    assert "openai_api_key" not in changed


def test_unknown_provider_rejected():
    out, _ = bc.apply_update({}, {"provider": "definitely-not-a-provider"})
    assert "lumeri_v3_provider" not in out
