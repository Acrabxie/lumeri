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
        "vertex", "gemini", "openai", "openai_subscription", "claude", "openrouter", "custom",
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


def test_openai_subscription_maps_to_local_bridge_without_storing_a_key():
    out, changed = bc.apply_update({}, {
        "provider": "openai_subscription",
        "model": "gpt-5.5",
    })
    assert out["lumeri_v3_provider"] == "openai"
    assert out["lumeri_openai_auth_mode"] == "subscription"
    assert out["lumeri_openai_base_url"] == bc.OPENAI_SUBSCRIPTION_BASE_URL
    assert "openai_api_key" not in out
    assert "lumeri_openai_auth_mode" in changed
    assert os.environ.get("LUMERI_OPENAI_AUTH_MODE") == "subscription"
    assert os.environ.get("LUMERI_OPENAI_BASE_URL") == bc.OPENAI_SUBSCRIPTION_BASE_URL
    assert bc.read_status(out)["provider"] == "openai_subscription"
    legacy = dict(out)
    legacy.pop("lumeri_openai_auth_mode")
    assert bc.read_status(legacy)["provider"] == "openai_subscription"


def test_switching_from_subscription_to_openai_api_clears_bridge_url():
    cfg, _ = bc.apply_update({}, {"provider": "openai_subscription"})
    out, _ = bc.apply_update(cfg, {
        "provider": "openai",
        "base_url": "",
        "openai_api_key": "sk-test",
    })
    assert out["lumeri_v3_provider"] == "openai"
    assert out["lumeri_openai_auth_mode"] == "api_key"
    assert out["lumeri_openai_base_url"] == ""
    assert os.environ.get("LUMERI_OPENAI_BASE_URL") is None
    assert bc.read_status(out)["provider"] == "openai"


def test_openai_subscription_model_list_scans_local_bridge(monkeypatch):
    subscription = next(p for p in bc.PROVIDERS if p["id"] == "openai_subscription")
    assert subscription["default_model"] == "gpt-5.5"

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "object": "list",
                "data": [
                    {"id": "gpt-5.6-sol", "name": "GPT-5.6-Sol"},
                    {"id": "gpt-5.5", "name": "GPT-5.5"},
                ],
            }

    calls = []
    monkeypatch.setattr("httpx.get", lambda url, **kwargs: calls.append((url, kwargs)) or Response())
    result = bc.list_models("openai_subscription", {}, proxy="http://127.0.0.1:7890")
    assert result == {
        "ok": True,
        "models": [
            {"id": "gpt-5.6-sol", "name": "GPT-5.6-Sol"},
            {"id": "gpt-5.5", "name": "GPT-5.5"},
        ],
    }
    assert calls[0][0] == "http://127.0.0.1:7808/v1/models"
    assert "headers" not in calls[0][1]
    assert "proxy" not in calls[0][1]


def test_codex_login_bridge_starts_and_checks_loopback_oauth(monkeypatch):
    class Response:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "POST":
            return Response({"state": "waiting", "authorization_url": "https://auth.openai.com/oauth/authorize?x=1"})
        return Response({"state": "success", "logged_in": True})

    monkeypatch.setattr("httpx.request", request)
    assert bc.codex_login_bridge("POST") == (
        200,
        {"state": "waiting", "authorization_url": "https://auth.openai.com/oauth/authorize?x=1"},
    )
    assert bc.codex_login_bridge("GET") == (200, {"state": "success", "logged_in": True})
    assert [(method, url) for method, url, _ in calls] == [
        ("POST", "http://127.0.0.1:7808/v1/auth/login"),
        ("GET", "http://127.0.0.1:7808/v1/auth/status"),
    ]
    assert all("proxy" not in kwargs for _, _, kwargs in calls)


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
