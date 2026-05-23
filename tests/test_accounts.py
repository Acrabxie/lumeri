from __future__ import annotations

import json
from pathlib import Path

from gemia import accounts, session_history


def _patch_account_roots(monkeypatch, tmp_path: Path) -> Path:
    root = tmp_path / "accounts"
    monkeypatch.setattr(accounts, "ACCOUNTS_ROOT", root)
    monkeypatch.setattr(accounts, "ACTIVE_ACCOUNT_PATH", root / "active.json")
    monkeypatch.setattr(accounts, "CONFIG_PATH", tmp_path / "config.json")
    return root


def _claims(sub: str, email: str) -> dict[str, object]:
    return {
        "sub": sub,
        "email": email,
        "email_verified": True,
        "name": email.split("@")[0],
        "picture": f"https://lh3.googleusercontent.com/a/{sub}",
    }


def test_google_sign_in_creates_local_account_without_token(monkeypatch, tmp_path: Path) -> None:
    root = _patch_account_roots(monkeypatch, tmp_path)
    monkeypatch.setattr(accounts, "verify_google_id_token", lambda credential, client_id=None: _claims("g-1", "one@example.com"))

    profile = accounts.sign_in_with_google("id-token")

    assert profile["provider"] == "google"
    assert profile["email"] == "one@example.com"
    assert accounts.current_account_id() == profile["account_id"]
    assert (root / profile["account_id"] / "profile.json").stat().st_mode & 0o777 == 0o600
    assert (root / profile["account_id"] / "memory" / "MEMORY.md").exists()
    dumped = json.dumps(json.loads((root / profile["account_id"] / "profile.json").read_text()), ensure_ascii=False)
    assert "id-token" not in dumped


def test_accounts_can_switch_and_sign_out(monkeypatch, tmp_path: Path) -> None:
    _patch_account_roots(monkeypatch, tmp_path)
    claims = {
        "one": _claims("g-1", "one@example.com"),
        "two": _claims("g-2", "two@example.com"),
    }
    monkeypatch.setattr(accounts, "verify_google_id_token", lambda credential, client_id=None: claims[credential])

    first = accounts.sign_in_with_google("one")
    second = accounts.sign_in_with_google("two")

    assert accounts.current_account_id() == second["account_id"]
    assert accounts.switch_account(first["account_id"])["email"] == "one@example.com"
    listed = accounts.list_accounts()
    assert {item["email"] for item in listed} == {"one@example.com", "two@example.com"}

    accounts.sign_out()
    assert accounts.current_account() is None


def test_session_history_is_isolated_by_account(monkeypatch, tmp_path: Path) -> None:
    _patch_account_roots(monkeypatch, tmp_path)
    claims = {
        "one": _claims("g-1", "one@example.com"),
        "two": _claims("g-2", "two@example.com"),
    }
    monkeypatch.setattr(accounts, "verify_google_id_token", lambda credential, client_id=None: claims[credential])

    first = accounts.sign_in_with_google("one")
    second = accounts.sign_in_with_google("two")

    session_history.save_current_session(
        {"messages": [{"id": "a", "role": "user", "content": "first", "timestamp": 1}]},
        account_id=first["account_id"],
    )
    session_history.save_current_session(
        {"messages": [{"id": "b", "role": "user", "content": "second", "timestamp": 2}]},
        account_id=second["account_id"],
    )

    first_loaded = session_history.load_current_session(account_id=first["account_id"])
    second_loaded = session_history.load_current_session(account_id=second["account_id"])

    assert first_loaded["messages"][0]["content"] == "first"
    assert second_loaded["messages"][0]["content"] == "second"
    assert session_history.current_session_path(first["account_id"]) != session_history.current_session_path(second["account_id"])


def test_google_client_id_comes_from_local_config(monkeypatch, tmp_path: Path) -> None:
    _patch_account_roots(monkeypatch, tmp_path)
    monkeypatch.delenv("GEMIA_GOOGLE_OAUTH_CLIENT_ID", raising=False)

    accounts.save_google_client_id("client.apps.googleusercontent.com")

    assert accounts.google_client_id() == "client.apps.googleusercontent.com"
    payload = json.loads((tmp_path / "config.json").read_text())
    assert payload["google_oauth_client_id"] == "client.apps.googleusercontent.com"


def test_google_oauth_start_uses_browser_redirect_and_pkce(monkeypatch, tmp_path: Path) -> None:
    _patch_account_roots(monkeypatch, tmp_path)
    monkeypatch.setenv("GEMIA_GOOGLE_OAUTH_CLIENT_ID", "client.apps.googleusercontent.com")
    accounts._PENDING_OAUTH_STATES.clear()

    payload = accounts.start_google_oauth()

    assert payload["authorization_url"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "prompt=select_account" in payload["authorization_url"]
    assert "code_challenge_method=S256" in payload["authorization_url"]
    assert payload["redirect_uri"] == "http://127.0.0.1:7788/auth/google/callback"
    assert payload["state"] in accounts._PENDING_OAUTH_STATES


def test_google_oauth_finish_exchanges_code_and_activates_account(monkeypatch, tmp_path: Path) -> None:
    _patch_account_roots(monkeypatch, tmp_path)
    monkeypatch.setenv("GEMIA_GOOGLE_OAUTH_CLIENT_ID", "client.apps.googleusercontent.com")
    accounts._PENDING_OAUTH_STATES.clear()
    started = accounts.start_google_oauth()

    def fake_exchange(code: str, *, client_id: str, code_verifier: str, redirect_uri: str) -> dict[str, str]:
        assert code == "auth-code"
        assert client_id == "client.apps.googleusercontent.com"
        assert code_verifier
        assert redirect_uri == started["redirect_uri"]
        return {"id_token": "verified-id-token"}

    monkeypatch.setattr(accounts, "_exchange_google_oauth_code", fake_exchange)
    monkeypatch.setattr(accounts, "verify_google_id_token", lambda credential, client_id=None: _claims("g-3", "three@example.com"))

    profile = accounts.finish_google_oauth(str(started["state"]), "auth-code")

    assert profile["email"] == "three@example.com"
    assert accounts.current_account_id() == profile["account_id"]
    assert started["state"] not in accounts._PENDING_OAUTH_STATES
