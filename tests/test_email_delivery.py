from __future__ import annotations

import json
from pathlib import Path

from gemia import email_delivery


def _write_config(path: Path, smtp: dict[str, object]) -> None:
    path.write_text(json.dumps({"smtp": smtp}, ensure_ascii=False), encoding="utf-8")


def test_smtp_config_aligns_visible_from_to_authenticated_sender_by_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(email_delivery, "CONFIG_PATH", tmp_path / "config.json")
    _write_config(
        email_delivery.CONFIG_PATH,
        {
            "host": "smtp.example.test",
            "username": "sender@gmail.com",
            "password": "app-password",
            "from_addr": "login@lumeri.dev",
        },
    )

    cfg = email_delivery.smtp_config()

    assert cfg["configured_from_addr"] == "login@lumeri.dev"
    assert cfg["from_addr"] == "sender@gmail.com"
    assert cfg["reply_to"] == "sender@gmail.com"
    assert cfg["allow_from_alias"] is False


def test_smtp_config_allows_verified_from_alias_when_explicit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(email_delivery, "CONFIG_PATH", tmp_path / "config.json")
    _write_config(
        email_delivery.CONFIG_PATH,
        {
            "host": "smtp.example.test",
            "username": "sender@gmail.com",
            "password": "app-password",
            "from_addr": "login@lumeri.dev",
            "allow_from_alias": True,
        },
    )

    cfg = email_delivery.smtp_config()

    assert cfg["from_addr"] == "login@lumeri.dev"
    assert cfg["allow_from_alias"] is True


def test_send_email_adds_transactional_headers_and_sender_for_alias(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(email_delivery, "CONFIG_PATH", tmp_path / "config.json")
    _write_config(
        email_delivery.CONFIG_PATH,
        {
            "host": "smtp.example.test",
            "username": "sender@gmail.com",
            "password": "app-password",
            "from_addr": "login@lumeri.dev",
            "allow_from_alias": True,
            "starttls": False,
        },
    )
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent["host"] = host
            sent["port"] = port
            sent["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def ehlo(self):
            pass

        def login(self, username, password):
            sent["login"] = (username, password)

        def send_message(self, msg, *, from_addr, to_addrs):
            sent["msg"] = msg
            sent["from_addr"] = from_addr
            sent["to_addrs"] = to_addrs

    monkeypatch.setattr(email_delivery.smtplib, "SMTP", FakeSMTP)

    email_delivery.send_email("user@outlook.com", "验证码", "code: 123456")

    msg = sent["msg"]
    assert sent["from_addr"] == "sender@gmail.com"
    assert msg["From"] == "Lumeri <login@lumeri.dev>"
    assert msg["Sender"] == "sender@gmail.com"
    assert msg["Auto-Submitted"] == "auto-generated"
    assert msg["X-Auto-Response-Suppress"] == "All"
