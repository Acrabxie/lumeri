"""Local Agent Link relay for codex-lumeri and gemini-lumeri.

The relay keeps a local JSONL transcript and can optionally invoke either CLI
in a read-only/plan posture. It is deliberately local-only: no secrets and no
shared-memory writes.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
import fcntl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AGENTS: dict[str, dict[str, str]] = {
    "codex-lumeri": {"label": "codex-lumeri", "command": "codex", "engine": "codex"},
    "gemini-lumeri": {"label": "gemini-lumeri", "command": "gemini", "engine": "gemini"},
}

AGENT_ALIASES = {
    "codex": "codex-lumeri",
    "codex_lumeri": "codex-lumeri",
    "gemini": "gemini-lumeri",
    "gemini_cli": "gemini-lumeri",
    "gemini-cli": "gemini-lumeri",
    "gemini_lumeri": "gemini-lumeri",
}

STATE_ROOT = Path.home() / ".gemia" / "agent-links"
MESSAGES_PATH = STATE_ROOT / "messages.jsonl"
LINKS_PATH = STATE_ROOT / "links.json"


def status_payload() -> dict[str, Any]:
    """Return visible link status for the desktop UI."""
    links = _read_links()
    agents: list[dict[str, Any]] = []
    for agent_id, meta in AGENTS.items():
        command = meta["command"]
        path = shutil.which(command)
        agents.append(
            {
                "id": agent_id,
                "label": meta["label"],
                "command": command,
                "available": bool(path),
                "path": path or "",
                "linked": bool(links.get(agent_id, False)),
            }
        )
    return {
        "ok": True,
        "agents": agents,
        "messages": list_messages(limit=30),
    }


def link_agent(agent_id: str, *, linked: bool = True) -> dict[str, Any]:
    """Mark a local agent as linked/unlinked in the UI state."""
    agent_id = _validate_agent(agent_id)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    with (STATE_ROOT / ".links.lock").open("w", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            links = _read_links()
            links[agent_id] = bool(linked)
            _write_links(links)
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
    event = _message_record(
        sender="lumeri",
        target=agent_id,
        message="linked" if linked else "unlinked",
        kind="link",
        ok=True,
    )
    _append_message(event)
    return status_payload()


def list_messages(*, limit: int = 80) -> list[dict[str, Any]]:
    if not MESSAGES_PATH.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in MESSAGES_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows[-max(1, int(limit)) :]


def send_message(
    *,
    sender: str,
    target: str,
    message: str,
    invoke: bool = False,
    cwd: str | Path | None = None,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    """Send one relay message and optionally invoke the target agent CLI."""
    sender_id = _normalize_sender(sender)
    target_id = _validate_agent(target)
    text = str(message or "").strip()
    if not text:
        raise ValueError("message is empty")

    outbound = _message_record(sender=sender_id, target=target_id, message=text, kind="message", ok=True)
    _append_message(outbound)
    response: dict[str, Any] | None = None
    if invoke:
        response = invoke_agent(
            target_id,
            sender=sender_id,
            message=text,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
        )
        _append_message(response)
    return {
        "ok": True,
        "message": outbound,
        "response": response,
        "messages": list_messages(limit=80),
    }


def relay_round(
    *,
    message: str,
    first: str = "codex-lumeri",
    second: str = "gemini-lumeri",
    cwd: str | Path | None = None,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    """Let two linked agents exchange one explicit round."""
    first_id = _validate_agent(first)
    second_id = _validate_agent(second)
    text = str(message or "").strip()
    if not text:
        raise ValueError("message is empty")

    first_result = send_message(
        sender="lumeri",
        target=first_id,
        message=text,
        invoke=True,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
    )
    first_reply = ""
    if isinstance(first_result.get("response"), dict):
        first_reply = str(first_result["response"].get("message") or "")
    if not first_reply:
        first_reply = "上一位 agent 没有返回可用文本。"

    second_result = send_message(
        sender=first_id,
        target=second_id,
        message=first_reply,
        invoke=True,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
    )
    return {
        "ok": True,
        "first": first_result.get("response"),
        "second": second_result.get("response"),
        "messages": list_messages(limit=80),
    }


def invoke_agent(
    agent_id: str,
    *,
    sender: str,
    message: str,
    cwd: str | Path | None = None,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    """Invoke one local CLI agent in a constrained communication mode."""
    target_id = _validate_agent(agent_id)
    prompt = _agent_prompt(target_id, sender=sender, message=message)
    workdir = Path(cwd or os.environ.get("GEMIA_AGENT_LINK_CWD") or Path.cwd()).expanduser().resolve()
    if AGENTS[target_id]["engine"] == "codex":
        result = _invoke_codex(prompt, workdir=workdir, timeout_seconds=timeout_seconds)
    else:
        result = _invoke_gemini(prompt, workdir=workdir, timeout_seconds=timeout_seconds)
    return _message_record(
        sender=target_id,
        target=_normalize_sender(sender),
        message=result["text"],
        kind="agent_reply",
        ok=bool(result["ok"]),
        error=str(result.get("error") or ""),
        metadata={k: v for k, v in result.items() if k != "text"},
    )


def _invoke_codex(prompt: str, *, workdir: Path, timeout_seconds: int) -> dict[str, Any]:
    codex = shutil.which("codex")
    if not codex:
        return {"ok": False, "text": "Codex CLI 不在 PATH 中。", "error": "codex_not_found"}
    with tempfile.TemporaryDirectory(prefix="lumeri-agent-link-") as tmp:
        out = Path(tmp) / "codex-last-message.txt"
        cmd = [
            codex,
            "exec",
            "--sandbox",
            "read-only",
            "--cd",
            str(workdir),
            "--skip-git-repo-check",
            "--output-last-message",
            str(out),
            "-",
        ]
        return _run_command(cmd, prompt, output_path=out, timeout_seconds=timeout_seconds)


def _invoke_gemini(prompt: str, *, workdir: Path, timeout_seconds: int) -> dict[str, Any]:
    gemini = shutil.which("gemini")
    if not gemini:
        return {"ok": False, "text": "Gemini CLI 不在 PATH 中。", "error": "gemini_not_found"}
    script = (
        "export GEMINI_CLI_TRUST_WORKSPACE=true; "
        "source ~/.zshrc >/dev/null 2>&1 || true; "
        'gemini --prompt "$1" --approval-mode plan --output-format text'
    )
    cmd = ["zsh", "-lc", script, "lumeri-agent-link", prompt]
    return _run_command(cmd, "", cwd=workdir, timeout_seconds=timeout_seconds)


def _run_command(
    cmd: list[str],
    stdin_text: str,
    *,
    output_path: Path | None = None,
    cwd: Path | None = None,
    timeout_seconds: int,
) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            cmd,
            input=stdin_text,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=max(10, int(timeout_seconds)),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "text": "Agent Link 调用超时。", "error": "timeout"}
    except Exception as exc:
        return {"ok": False, "text": f"Agent Link 调用失败：{exc}", "error": str(exc)}

    text = ""
    if output_path and output_path.exists():
        text = output_path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        text = (proc.stdout or "").strip()
    if not text and proc.stderr:
        text = proc.stderr.strip()
    if not text:
        text = "(empty reply)"
    if len(text) > 8000:
        text = text[:8000].rstrip() + "\n...(truncated)"
    return {
        "ok": proc.returncode == 0,
        "text": text,
        "returncode": proc.returncode,
        "stderr_tail": (proc.stderr or "")[-1200:],
    }


def _agent_prompt(agent_id: str, *, sender: str, message: str) -> str:
    meta = AGENTS[agent_id]
    link_label = meta["label"]
    agent_name = "Codex" if meta["engine"] == "codex" else "Gemini CLI"
    sender_label = AGENTS.get(sender, {"label": sender}).get("label", sender)
    return (
        f"你是 Lumeri 顶部 Agent Link 中通过 {link_label} 链接接入的 {agent_name}。\n"
        f"你收到一条经由 Lumeri relay 转发的消息，发送方是 {sender_label}。\n"
        "通讯拓扑是 codex-lumeri 与 gemini-lumeri 两条链接；Agent 之间的互通也必须经由 Lumeri。\n"
        "请只回复给 Lumeri relay，再由 Lumeri 转给对方；回复要简洁、具体、自然。\n"
        "当前模式是通讯/协作，不要主动修改文件，不要执行破坏性操作，不要泄露密钥。\n"
        "如果对方在讨论 Lumeri 开发，请给出可以继续推进的一两点建议或判断。\n\n"
        f"消息：\n{message}"
    )


def _read_links() -> dict[str, bool]:
    if not LINKS_PATH.exists():
        return {}
    try:
        payload = json.loads(LINKS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    links: dict[str, bool] = {}
    for key, value in payload.items():
        try:
            links[_validate_agent(key)] = bool(value)
        except ValueError:
            continue
    return links


def _write_links(links: dict[str, bool]) -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    LINKS_PATH.write_text(json.dumps(links, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_message(record: dict[str, Any]) -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    with MESSAGES_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _message_record(
    *,
    sender: str,
    target: str,
    message: str,
    kind: str,
    ok: bool,
    error: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": f"alink_{uuid.uuid4().hex[:12]}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "sender": sender,
        "target": target,
        "message": str(message or ""),
        "ok": bool(ok),
        "error": error,
        "metadata": metadata or {},
    }


def _normalize_sender(sender: str) -> str:
    value = str(sender or "lumeri").strip().lower().replace("_", "-")
    if value in AGENTS or value == "lumeri":
        return value
    if value in AGENT_ALIASES:
        return AGENT_ALIASES[value]
    raise ValueError(f"unknown sender: {sender}")


def _validate_agent(agent_id: str) -> str:
    value = str(agent_id or "").strip().lower().replace("_", "-")
    if value in AGENT_ALIASES:
        value = AGENT_ALIASES[value]
    if value not in AGENTS:
        raise ValueError(f"unknown agent: {agent_id}")
    return value
