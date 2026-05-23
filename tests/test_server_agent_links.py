import json
from pathlib import Path
from typing import Any

import server
from gemia import agent_links
from tests_http_harness import create_raw_request, run_server_handler


def _patch_agent_link_state(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "agent-links"
    monkeypatch.setattr(agent_links, "STATE_ROOT", root)
    monkeypatch.setattr(agent_links, "MESSAGES_PATH", root / "messages.jsonl")
    monkeypatch.setattr(agent_links, "LINKS_PATH", root / "links.json")

def make_request(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, str, bytes]:
    headers = {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    raw_request = create_raw_request(method, path, headers=headers, body=body)
    response = run_server_handler(server._Handler, raw_request)
    return response["status"], response["headers"].get("content-type", ""), response["body"]


def test_agent_link_status_link_and_message_routes(monkeypatch, tmp_path: Path) -> None:
    _patch_agent_link_state(monkeypatch, tmp_path)
    monkeypatch.setattr(agent_links.shutil, "which", lambda command: f"/bin/{command}")

    def fake_invoke(agent_id: str, *, sender: str, message: str, cwd=None, timeout_seconds=180):
        return {
            "id": "reply",
            "created_at": "now",
            "kind": "agent_reply",
            "sender": agent_id,
            "target": sender,
            "message": f"reply from {agent_id}",
            "ok": True,
            "error": "",
            "metadata": {},
        }

    monkeypatch.setattr(agent_links, "invoke_agent", fake_invoke)

    status, _, raw = make_request("GET", "/agent-links/status")
    assert status == 200
    payload = json.loads(raw)
    assert {item["id"] for item in payload["agents"]} == {"codex-lumeri", "gemini-lumeri"}

    status, _, raw = make_request("POST", "/agent-links/link", {"agent_id": "codex"})
    assert status == 200
    payload = json.loads(raw)
    assert next(item for item in payload["agents"] if item["id"] == "codex-lumeri")["linked"] is True

    status, _, raw = make_request(
        "POST",
        "/agent-links/message",
        {"sender": "codex-lumeri", "target": "gemini-lumeri", "message": "ping", "invoke": True},
    )
    assert status == 200
    payload = json.loads(raw)
    assert payload["response"]["message"] == "reply from gemini-lumeri"

    status, _, raw = make_request("GET", "/agent-links/messages?limit=2")
    assert status == 200
    assert len(json.loads(raw)["messages"]) == 2
