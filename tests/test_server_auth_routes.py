from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import urllib.parse

import server
from gemia import accounts, session_history
from tests_http_harness import create_raw_request, run_server_handler


def _patch_account_roots(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "accounts"
    monkeypatch.setattr(accounts, "ACCOUNTS_ROOT", root)
    monkeypatch.setattr(accounts, "ACTIVE_ACCOUNT_PATH", root / "active.json")
    monkeypatch.setattr(accounts, "CONFIG_PATH", tmp_path / "config.json")
    accounts._PENDING_OAUTH_STATES.clear()
    accounts._PENDING_EMAIL_CODES.clear()


def _claims(sub: str, email: str) -> dict[str, object]:
    return {
        "sub": sub,
        "email": email,
        "email_verified": True,
        "name": email.split("@")[0],
        "picture": f"https://lh3.googleusercontent.com/a/{sub}",
        "aud": "client.apps.googleusercontent.com",
        "iss": "https://accounts.google.com",
        "exp": 4_102_444_800,
    }


def make_request(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, str, str]:
    headers = {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    raw_request = create_raw_request(method, path, headers=headers, body=body)
    response = run_server_handler(server._Handler, raw_request)
    return response["status"], response["headers"].get("content-type", ""), response["body"].decode("utf-8")


def test_auth_session_start_callback_and_logout(monkeypatch, tmp_path: Path) -> None:
    _patch_account_roots(monkeypatch, tmp_path)
    monkeypatch.setenv("GEMIA_GOOGLE_OAUTH_CLIENT_ID", "client.apps.googleusercontent.com")
    monkeypatch.setattr(
        accounts,
        "_exchange_google_oauth_code",
        lambda code, *, client_id, code_verifier, redirect_uri: {"id_token": f"id-token:{code}"},
    )
    monkeypatch.setattr(
        accounts,
        "verify_google_id_token",
        lambda credential, client_id=None: _claims("g-1", "one@example.com"),
    )

    status, _, session_raw = make_request("GET", "/auth/session")
    assert status == 200
    session = json.loads(session_raw)
    assert session["account"] is None
    assert session["has_google_client_id"] is True

    status, _, start_raw = make_request("POST", "/auth/google/start")
    assert status == 200
    started = json.loads(start_raw)
    parsed = urllib.parse.urlparse(started["authorization_url"])
    query = urllib.parse.parse_qs(parsed.query)
    assert parsed.netloc == "accounts.google.com"
    assert query["prompt"] == ["select_account"]

    callback = f"/auth/google/callback?state={query['state'][0]}&code=auth-code"
    status, content_type, callback_html = make_request("GET", callback)
    assert status == 200
    assert "text/html" in content_type
    assert "已登录" in callback_html

    status, _, session_raw = make_request("GET", "/auth/session")
    assert status == 200
    session = json.loads(session_raw)
    assert session["account"]["email"] == "one@example.com"
    assert session["accounts"][0]["email"] == "one@example.com"

    status, _, logout_raw = make_request("POST", "/auth/logout")
    assert status == 200
    assert json.loads(logout_raw)["account"] is None


def test_accounts_switch_route_reads_json_body(monkeypatch, tmp_path: Path) -> None:
    _patch_account_roots(monkeypatch, tmp_path)
    monkeypatch.setattr(
        accounts,
        "verify_google_id_token",
        lambda credential, client_id=None: _claims(credential, f"{credential}@example.com"),
    )
    first = accounts.sign_in_with_google("one")
    second = accounts.sign_in_with_google("two")
    assert accounts.current_account_id() == second["account_id"]

    status, _, raw = make_request("POST", "/accounts/switch", {"account_id": first["account_id"]})

    assert status == 200
    payload = json.loads(raw)
    assert payload["account"]["account_id"] == first["account_id"]
    assert accounts.current_account_id() == first["account_id"]


def test_email_code_login_routes_activate_account(monkeypatch, tmp_path: Path) -> None:
    _patch_account_roots(monkeypatch, tmp_path)
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(accounts, "_generate_email_code", lambda: "123456")
    monkeypatch.setattr(
        "gemia.email_delivery.send_login_code",
        lambda email, code, **kwargs: sent.append((email, code)),
    )

    status, _, start_raw = make_request("POST", "/auth/email/start", {"email": "User@Example.Dev"})

    assert status == 200
    assert json.loads(start_raw)["email"] == "user@example.dev"
    assert sent == [("user@example.dev", "123456")]
    assert "123456" not in json.dumps(accounts._PENDING_EMAIL_CODES)

    status, _, verify_raw = make_request("POST", "/auth/email/verify", {"email": "user@example.dev", "code": "123456"})

    assert status == 200
    payload = json.loads(verify_raw)
    assert payload["account"]["provider"] == "email"
    assert payload["account"]["email"] == "user@example.dev"
    assert accounts.current_account_id() == payload["account"]["account_id"]

    status, _, accounts_raw = make_request("GET", "/accounts")
    assert status == 200
    roster = json.loads(accounts_raw)
    assert roster["account"]["email"] == "user@example.dev"
    assert roster["accounts"][0]["email"] == "user@example.dev"


def test_session_history_snapshot_route_opens_previous_session(monkeypatch, tmp_path: Path) -> None:
    _patch_account_roots(monkeypatch, tmp_path)
    monkeypatch.setattr(
        accounts,
        "verify_google_id_token",
        lambda credential, client_id=None: _claims("g-1", "one@example.com"),
    )
    profile = accounts.sign_in_with_google("one")
    session_history.save_current_session(
        {
            "messages": [{"id": "m1", "role": "user", "content": "旧会话", "timestamp": 1}],
            "project_state": {"clips": [{"id": "clip_1"}]},
        },
        account_id=profile["account_id"],
    )
    snapshot_id = session_history.list_session_snapshots(account_id=profile["account_id"])[0]["id"]
    session_history.save_current_session(
        {
            "messages": [{"id": "m2", "role": "user", "content": "新会话", "timestamp": 2}],
            "project_state": {"clips": []},
        },
        account_id=profile["account_id"],
    )

    quoted_id = urllib.parse.quote(snapshot_id)
    status, _, raw = make_request("GET", f"/session-history/{quoted_id}")

    assert status == 200
    payload = json.loads(raw)
    assert payload["title"] == "旧会话"
    assert payload["messages"][0]["content"] == "旧会话"
    assert session_history.load_current_session(account_id=profile["account_id"])["title"] == "旧会话"
