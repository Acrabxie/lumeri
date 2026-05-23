from __future__ import annotations

from pathlib import Path

from gemia import agent_links


def _patch_state(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "agent-links"
    monkeypatch.setattr(agent_links, "STATE_ROOT", root)
    monkeypatch.setattr(agent_links, "MESSAGES_PATH", root / "messages.jsonl")
    monkeypatch.setattr(agent_links, "LINKS_PATH", root / "links.json")


def test_link_agent_records_local_link_state(monkeypatch, tmp_path: Path) -> None:
    _patch_state(monkeypatch, tmp_path)
    monkeypatch.setattr(agent_links.shutil, "which", lambda command: f"/bin/{command}")

    payload = agent_links.link_agent("codex")

    codex = next(item for item in payload["agents"] if item["id"] == "codex-lumeri")
    assert codex["linked"] is True
    assert codex["available"] is True
    assert agent_links.list_messages(limit=5)[-1]["kind"] == "link"


def test_send_message_can_invoke_fake_target(monkeypatch, tmp_path: Path) -> None:
    _patch_state(monkeypatch, tmp_path)

    def fake_invoke(agent_id: str, *, sender: str, message: str, cwd=None, timeout_seconds=180):
        return {
            "id": "reply",
            "created_at": "now",
            "kind": "agent_reply",
            "sender": agent_id,
            "target": sender,
            "message": f"{agent_id} heard: {message}",
            "ok": True,
            "error": "",
            "metadata": {},
        }

    monkeypatch.setattr(agent_links, "invoke_agent", fake_invoke)

    result = agent_links.send_message(sender="lumeri", target="gemini_cli", message="hello", invoke=True)

    assert result["ok"] is True
    assert result["response"]["message"] == "gemini-lumeri heard: hello"
    messages = agent_links.list_messages(limit=10)
    assert [item["kind"] for item in messages] == ["message", "agent_reply"]


def test_relay_round_invokes_both_agents(monkeypatch, tmp_path: Path) -> None:
    _patch_state(monkeypatch, tmp_path)
    calls: list[tuple[str, str, str]] = []

    def fake_invoke(agent_id: str, *, sender: str, message: str, cwd=None, timeout_seconds=180):
        calls.append((sender, agent_id, message))
        return {
            "id": f"reply-{agent_id}",
            "created_at": "now",
            "kind": "agent_reply",
            "sender": agent_id,
            "target": sender,
            "message": f"{agent_id} reply",
            "ok": True,
            "error": "",
            "metadata": {},
        }

    monkeypatch.setattr(agent_links, "invoke_agent", fake_invoke)

    result = agent_links.relay_round(message="start", first="codex", second="gemini_cli")

    assert result["ok"] is True
    assert calls == [
        ("lumeri", "codex-lumeri", "start"),
        ("codex-lumeri", "gemini-lumeri", "codex-lumeri reply"),
    ]


def test_gemini_invoke_trusts_headless_workspace(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(agent_links.shutil, "which", lambda command: f"/bin/{command}")
    captured: dict[str, object] = {}

    def fake_run_command(cmd, stdin_text, *, output_path=None, cwd=None, timeout_seconds=180):
        captured["cmd"] = cmd
        captured["stdin_text"] = stdin_text
        captured["cwd"] = cwd
        return {"ok": True, "text": "ok", "returncode": 0, "stderr": ""}

    monkeypatch.setattr(agent_links, "_run_command", fake_run_command)

    result = agent_links._invoke_gemini("ping", workdir=tmp_path, timeout_seconds=12)

    assert result["ok"] is True
    assert "GEMINI_CLI_TRUST_WORKSPACE=true" in captured["cmd"][2]
    assert captured["cwd"] == tmp_path
