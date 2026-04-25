from __future__ import annotations

import json
import os
import socket
import shutil
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


HEARTBEAT_INTENTS = {"heartbeat", "bridge_heartbeat", "heartbeat_poll", "poll_heartbeat"}
HEARTBEAT_TASK_CLASSES = {"heartbeat", "bridge_heartbeat", "healthcheck"}
DEFAULT_LEASE_STALE_AFTER_SEC = 30
DEFAULT_PROCESSING_STALE_AFTER_SEC = 300
DEFAULT_AUTO_HEARTBEAT_INTERVAL_SEC = 2 * 60 * 60


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


@dataclass
class BridgePaths:
    root: Path
    inbox: Path
    processing: Path
    outbox: Path
    failed: Path
    logs: Path
    leases: Path
    heartbeat_state: Path

    @classmethod
    def from_root(cls, root: str | Path) -> "BridgePaths":
        root_path = Path(root).expanduser().resolve()
        return cls(
            root=root_path,
            inbox=root_path / "inbox",
            processing=root_path / "processing",
            outbox=root_path / "outbox",
            failed=root_path / "failed",
            logs=root_path / "logs",
            leases=root_path / "leases",
            heartbeat_state=root_path / "heartbeat_state.json",
        )

    def ensure(self) -> None:
        for path in (
            self.root,
            self.inbox,
            self.processing,
            self.outbox,
            self.failed,
            self.logs,
            self.leases,
        ):
            path.mkdir(parents=True, exist_ok=True)


@dataclass
class BridgeTask:
    task_id: str
    source: str
    intent: str
    prompt: str
    assets: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    permissions: dict[str, Any] = field(default_factory=dict)
    preferred_agent: str | None = None
    allowed_agents: list[str] = field(default_factory=list)
    cwd: str | None = None
    created_at: str = field(default_factory=_utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        source: str,
        intent: str,
        prompt: str,
        assets: list[str] | None = None,
        context: dict[str, Any] | None = None,
        permissions: dict[str, Any] | None = None,
        cwd: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "BridgeTask":
        return cls(
            task_id=f"bridge_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}",
            source=source,
            intent=intent,
            prompt=prompt,
            assets=list(assets or []),
            context=dict(context or {}),
            permissions=dict(permissions or {}),
            cwd=cwd,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BridgeTask":
        return cls(
            task_id=str(payload["task_id"]),
            source=str(payload["source"]),
            intent=str(payload["intent"]),
            prompt=str(payload["prompt"]),
            assets=[str(item) for item in payload.get("assets", [])],
            context=dict(payload.get("context", {})),
            permissions=dict(payload.get("permissions", {})),
            preferred_agent=payload.get("preferred_agent"),
            allowed_agents=[str(item) for item in payload.get("allowed_agents", [])],
            cwd=payload.get("cwd"),
            created_at=str(payload.get("created_at", _utc_now())),
            metadata=dict(payload.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BridgeResult:
    task_id: str
    status: str
    output_text: str
    artifacts: list[str] = field(default_factory=list)
    adapter: str = ""
    created_at: str = field(default_factory=_utc_now)
    finished_at: str = field(default_factory=_utc_now)
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BridgeAdapter(Protocol):
    name: str

    def run(self, task: BridgeTask) -> BridgeResult:
        ...


class QueueBridgeAdapter:
    """Delegate work to another file-based agent queue under bridge control."""

    def __init__(self, name: str, queue_root: str | Path) -> None:
        self.name = name
        self.queue_root = Path(queue_root).expanduser().resolve()
        self.inbox = self.queue_root / "inbox"
        self.inbox.mkdir(parents=True, exist_ok=True)

    def run(self, task: BridgeTask) -> BridgeResult:
        delegated_payload = task.to_dict()
        delegated_payload["delegated_by"] = "gemia_master"
        delegated_payload["delegated_at"] = _utc_now()
        delegated_payload["preferred_agent"] = None
        delegated_payload["allowed_agents"] = []
        dest = self.inbox / f"{task.task_id}.json"
        dest.write_text(json.dumps(delegated_payload, ensure_ascii=False, indent=2) + "\n")
        return BridgeResult(
            task_id=task.task_id,
            status="delegated",
            output_text=f"Delegated to {self.name}",
            adapter=self.name,
            raw={"delegate_queue": str(dest)},
        )


class ClaudeCodeAdapter:
    """Minimal CLI adapter for Claude Code.

    The bridge contract is file-based. This adapter simply takes a task envelope,
    renders a prompt with asset/context hints, and forwards it to the `claude` CLI.
    """

    name = "claude_code"

    def __init__(
        self,
        *,
        claude_bin: str = "claude",
        extra_args: list[str] | None = None,
        timeout_sec: int = 600,
        default_cwd: str | None = None,
    ) -> None:
        self.claude_bin = claude_bin
        self.extra_args = list(extra_args or [])
        self.timeout_sec = timeout_sec
        self.default_cwd = default_cwd

    def build_prompt(self, task: BridgeTask) -> str:
        sections = [
            "You are handling a bridge task from Google Antigravity into Claude Code.",
            f"Task ID: {task.task_id}",
            f"Source: {task.source}",
            f"Intent: {task.intent}",
            "",
            "User request:",
            task.prompt.strip(),
        ]
        if task.assets:
            sections.extend(["", "Assets:", *[f"- {asset}" for asset in task.assets]])
        if task.context:
            sections.extend(["", "Context JSON:", json.dumps(task.context, ensure_ascii=False, indent=2)])
        if task.permissions:
            sections.extend(["", "Permissions JSON:", json.dumps(task.permissions, ensure_ascii=False, indent=2)])
        sections.extend(
            [
                "",
                "Respond with the final result only. If you create files, mention absolute paths clearly.",
            ]
        )
        return "\n".join(sections)

    def run(self, task: BridgeTask) -> BridgeResult:
        prompt = self.build_prompt(task)
        args = [
            self.claude_bin,
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
            *self.extra_args,
            prompt,
        ]
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                cwd=task.cwd or self.default_cwd,
                check=False,
            )
        except FileNotFoundError as exc:
            return BridgeResult(
                task_id=task.task_id,
                status="failed",
                output_text="",
                adapter=self.name,
                error=f"Claude binary not found: {exc}",
            )
        except subprocess.TimeoutExpired as exc:
            return BridgeResult(
                task_id=task.task_id,
                status="failed",
                output_text=exc.stdout or "",
                adapter=self.name,
                error=f"Claude timed out after {self.timeout_sec}s",
            )

        output_text, raw = self._parse_stream_json(proc.stdout)
        if not output_text:
            output_text = (proc.stderr or "").strip()
        status = "succeeded" if proc.returncode == 0 else "failed"
        return BridgeResult(
            task_id=task.task_id,
            status=status,
            output_text=output_text.strip(),
            adapter=self.name,
            error=None if proc.returncode == 0 else (proc.stderr.strip() or f"Exit code {proc.returncode}"),
            raw=raw,
        )

    @staticmethod
    def _parse_stream_json(stdout: str) -> tuple[str, dict[str, Any]]:
        text_blocks: list[str] = []
        raw_events: list[dict[str, Any]] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw_events.append(event)
            if event.get("type") == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "text":
                        text_blocks.append(str(block.get("text", "")))
            elif event.get("type") == "result":
                result = event.get("result")
                if isinstance(result, str) and result.strip():
                    text_blocks.append(result)
        return "\n".join(part for part in text_blocks if part.strip()).strip(), {"events": raw_events}


class MasterBridgeController:
    """Main controller that decides which sub-agent should handle a task."""

    def __init__(self, adapters: dict[str, BridgeAdapter], *, default_agent: str = "claude_code") -> None:
        if default_agent not in adapters:
            raise ValueError(f"default_agent '{default_agent}' missing from adapters")
        self.adapters = dict(adapters)
        self.default_agent = default_agent

    def list_agents(self) -> list[str]:
        return sorted(self.adapters.keys())

    def dispatch(self, task: BridgeTask) -> BridgeResult:
        agent, reason = self.route(task)
        result = self.adapters[agent].run(task)
        result.raw.setdefault("route", {"agent": agent, "reason": reason})
        return result

    def route(self, task: BridgeTask) -> tuple[str, str]:
        available = set(self.adapters.keys())
        allowed = [agent for agent in task.allowed_agents if agent in available]

        if task.preferred_agent and task.preferred_agent in available:
            if allowed and task.preferred_agent not in allowed:
                return allowed[0], f"preferred agent '{task.preferred_agent}' not in allowlist"
            return task.preferred_agent, "preferred_agent"

        intent = task.intent.lower().strip()
        prompt = task.prompt.lower()
        task_class = str(task.metadata.get("task_class", "")).strip().lower()
        media_intents = {"media", "image", "video", "design", "creative", "generate", "visual"}
        code_intents = {"code", "implement", "debug", "repo", "automation", "tooling"}

        candidate = self.default_agent
        reason = "default_agent"

        if task_class in {"architecture", "core_architecture", "system_design"} and "claude_code" in available:
            candidate = "claude_code"
            reason = f"task_class:{task_class}"
        elif task_class in {"review", "code_review"} and "antigravity" in available:
            candidate = "antigravity"
            reason = f"task_class:{task_class}"
            task.metadata.setdefault("review_mode", "code")
        elif task_class in {"frontend", "frontend_design", "ui", "design_review"} and "antigravity" in available:
            candidate = "antigravity"
            reason = f"task_class:{task_class}"
            task.metadata.setdefault("tooling", [])
            tooling = task.metadata["tooling"]
            if isinstance(tooling, list) and "playwright" not in tooling:
                tooling.append("playwright")
            task.metadata.setdefault("review_mode", "frontend_design")
        elif "antigravity" in prompt and "antigravity" in available:
            candidate = "antigravity"
            reason = "prompt_mentions_antigravity"
        elif intent in media_intents and "antigravity" in available:
            candidate = "antigravity"
            reason = "media_intent"
        elif intent in code_intents and "claude_code" in available:
            candidate = "claude_code"
            reason = "code_intent"
        elif "claude" in prompt and "claude_code" in available:
            candidate = "claude_code"
            reason = "prompt_mentions_claude"

        if allowed and candidate not in allowed:
            candidate = allowed[0]
            reason = f"allowlist_override:{reason}"
        return candidate, reason


class ControllerAdapter:
    """Adapter facade so the daemon can stay single-entry while controller fans out."""

    name = "gemia_master"

    def __init__(self, controller: MasterBridgeController) -> None:
        self.controller = controller

    def run(self, task: BridgeTask) -> BridgeResult:
        result = self.controller.dispatch(task)
        result.raw.setdefault("controller", self.name)
        return result


class BridgeDaemon:
    def __init__(
        self,
        paths: BridgePaths,
        adapter: BridgeAdapter,
        *,
        auto_heartbeat_interval_sec: int = DEFAULT_AUTO_HEARTBEAT_INTERVAL_SEC,
        auto_heartbeat_source: str = "codex",
        auto_heartbeat_instructions_path: str | None = None,
    ) -> None:
        self.paths = paths
        self.adapter = adapter
        self.paths.ensure()
        self.started_at = _utc_now()
        self.daemon_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.lease_stale_after_sec = DEFAULT_LEASE_STALE_AFTER_SEC
        self.processing_stale_after_sec = DEFAULT_PROCESSING_STALE_AFTER_SEC
        self.lease_path = self.paths.leases / f"{self.daemon_id}.json"
        self.auto_heartbeat_interval_sec = max(int(auto_heartbeat_interval_sec), 0)
        self.auto_heartbeat_source = auto_heartbeat_source
        self.auto_heartbeat_instructions_path = auto_heartbeat_instructions_path

    def submit_task(self, task: BridgeTask) -> Path:
        payload_path = self.paths.inbox / f"{task.task_id}.json"
        payload_path.write_text(json.dumps(task.to_dict(), ensure_ascii=False, indent=2) + "\n")
        return payload_path

    def process_once(self) -> int:
        self._refresh_lease(status="active")
        recovered = self._recover_stale_processing()
        processed = 0
        for task_file in sorted(self.paths.inbox.glob("*.json")):
            self._process_file(task_file)
            processed += 1
            self._refresh_lease(status="active")
        self._record_last_check(processed=processed, recovered=len(recovered))
        return processed

    def serve_forever(self, *, poll_interval: float = 1.0) -> None:
        self._refresh_lease(status="active", poll_interval=poll_interval)
        try:
            while True:
                self.schedule_heartbeat_if_due()
                processed = self.process_once()
                if processed == 0:
                    time.sleep(poll_interval)
        finally:
            self._refresh_lease(status="stopped", poll_interval=poll_interval)

    def heartbeat_processing(self, task_id: str) -> dict[str, Any]:
        processing_file = self.paths.processing / f"{task_id}.json"
        if not processing_file.exists():
            raise FileNotFoundError(f"Processing task not found: {task_id}")

        payload = _read_json_file(processing_file)
        metadata = payload.setdefault("metadata", {})
        processing = metadata.setdefault("processing", {})
        processing["last_heartbeat_at"] = _utc_now()
        processing["status"] = "processing"
        metadata["processing"] = processing
        payload["metadata"] = metadata
        _atomic_write_json(processing_file, payload)

        event = {
            "event": "heartbeat",
            "task_id": task_id,
            "state": "processing",
            "daemon_id": self.daemon_id,
        }
        self._write_log(task_id, event)

        state = _read_json_file(self.paths.heartbeat_state)
        state["last_processing_heartbeat"] = {
            "task_id": task_id,
            "state": "processing",
            "daemon_id": self.daemon_id,
            "checked_at": _utc_now(),
        }
        _atomic_write_json(self.paths.heartbeat_state, state)
        return event

    def recover_stale_processing(self, *, max_age_sec: int | None = None) -> list[str]:
        stale_after = self.processing_stale_after_sec if max_age_sec is None else max(int(max_age_sec), 0)
        recovered = self._recover_stale_processing(stale_after_sec=stale_after)
        return recovered

    def schedule_heartbeat_if_due(self) -> str | None:
        return self._maybe_submit_scheduled_heartbeat()

    def _process_file(self, inbox_file: Path) -> BridgeResult:
        processing_file = self.paths.processing / inbox_file.name
        shutil.move(str(inbox_file), str(processing_file))
        payload = json.loads(processing_file.read_text())
        payload = self._mark_processing_claim(payload)
        _atomic_write_json(processing_file, payload)
        task = BridgeTask.from_dict(payload)
        self._write_log(task.task_id, {"event": "started", "task": task.to_dict()})
        if self._is_heartbeat_task(task):
            result = self._handle_heartbeat(task)
        else:
            result = self.adapter.run(task)
        self._write_log(task.task_id, {"event": "finished", "result": result.to_dict()})

        is_failure = result.status == "failed"
        result_path = (self.paths.failed if is_failure else self.paths.outbox) / f"{task.task_id}.json"
        result_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n")
        processing_file.unlink(missing_ok=True)
        return result

    def _write_log(self, task_id: str, payload: dict[str, Any]) -> None:
        log_path = self.paths.logs / f"{task_id}.ndjson"
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": _utc_now(), **payload}, ensure_ascii=False) + "\n")

    def _refresh_lease(self, *, status: str, poll_interval: float | None = None) -> None:
        lease = {
            "daemon_id": self.daemon_id,
            "adapter": getattr(self.adapter, "name", self.adapter.__class__.__name__),
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "started_at": self.started_at,
            "last_heartbeat_at": _utc_now(),
            "status": status,
            "lease_stale_after_sec": self.lease_stale_after_sec,
        }
        if poll_interval is not None:
            lease["poll_interval"] = poll_interval
        _atomic_write_json(self.lease_path, lease)

    def _record_last_check(self, *, processed: int, recovered: int) -> None:
        state = _read_json_file(self.paths.heartbeat_state)
        state["last_check"] = {
            "daemon_id": self.daemon_id,
            "checked_at": _utc_now(),
            "processed": processed,
            "recovered": recovered,
            "queue_depths": self._queue_depths(),
        }
        _atomic_write_json(self.paths.heartbeat_state, state)

    def _maybe_submit_scheduled_heartbeat(self) -> str | None:
        if self.auto_heartbeat_interval_sec <= 0:
            return None

        state = _read_json_file(self.paths.heartbeat_state)
        auto_state = state.get("auto_heartbeat") or {}
        now = datetime.now(timezone.utc)

        last_submitted = _parse_utc(str(auto_state.get("last_submitted_at", "")))
        if last_submitted is not None:
            elapsed = (now - last_submitted).total_seconds()
            if elapsed < self.auto_heartbeat_interval_sec:
                return None

        for queue_dir in (self.paths.inbox, self.paths.processing):
            for task_file in queue_dir.glob("*.json"):
                payload = _read_json_file(task_file)
                if payload and self._is_heartbeat_task(BridgeTask.from_dict(payload)):
                    return None

        metadata: dict[str, Any] = {
            "task_class": "heartbeat",
            "heartbeat": True,
            "submitted_by": "bridge_auto_scheduler",
            "scheduler_daemon_id": self.daemon_id,
            "min_interval_sec": self.auto_heartbeat_interval_sec,
        }
        if self.auto_heartbeat_instructions_path:
            metadata["instructions_path"] = self.auto_heartbeat_instructions_path
        task = BridgeTask.new(
            source=self.auto_heartbeat_source,
            intent="heartbeat",
            prompt="Automatic two-hour heartbeat poll.",
            metadata=metadata,
            context={
                "heartbeat_action": "poll",
                "min_interval_sec": self.auto_heartbeat_interval_sec,
                "scheduled": True,
            },
        )
        self.submit_task(task)
        auto_state.update(
            {
                "enabled": True,
                "interval_sec": self.auto_heartbeat_interval_sec,
                "last_submitted_at": _utc_now(),
                "last_task_id": task.task_id,
                "daemon_id": self.daemon_id,
            }
        )
        state["auto_heartbeat"] = auto_state
        _atomic_write_json(self.paths.heartbeat_state, state)
        self._write_log(
            task.task_id,
            {
                "event": "scheduled_heartbeat_submitted",
                "task_id": task.task_id,
                "interval_sec": self.auto_heartbeat_interval_sec,
                "daemon_id": self.daemon_id,
            },
        )
        return task.task_id

    def _mark_processing_claim(self, payload: dict[str, Any]) -> dict[str, Any]:
        metadata = payload.setdefault("metadata", {})
        processing = metadata.setdefault("processing", {})
        processing["daemon_id"] = self.daemon_id
        processing["lease_path"] = str(self.lease_path)
        processing["claimed_at"] = _utc_now()
        processing["status"] = "active"
        processing["attempt"] = int(processing.get("attempt", 0)) + 1
        return payload

    def _is_heartbeat_task(self, task: BridgeTask) -> bool:
        intent = task.intent.strip().lower()
        task_class = str(task.metadata.get("task_class", "")).strip().lower()
        heartbeat_flag = task.metadata.get("heartbeat")
        if intent in HEARTBEAT_INTENTS or task_class in HEARTBEAT_TASK_CLASSES:
            return True
        return bool(heartbeat_flag)

    def _handle_heartbeat(self, task: BridgeTask) -> BridgeResult:
        action = str(
            task.metadata.get("heartbeat_action")
            or task.context.get("heartbeat_action")
            or task.context.get("action")
            or "poll"
        ).strip().lower()
        min_interval_sec = int(
            task.metadata.get("min_interval_sec")
            or task.context.get("min_interval_sec")
            or 0
        )
        state = _read_json_file(self.paths.heartbeat_state)
        last_check = _parse_utc(((state.get("heartbeat") or {}).get("checked_at")))
        throttled = False
        if min_interval_sec > 0 and last_check is not None:
            throttled = (datetime.now(timezone.utc) - last_check).total_seconds() < min_interval_sec

        instructions_path = str(task.metadata.get("instructions_path") or "").strip()
        instructions_text = ""
        if instructions_path:
            path = Path(instructions_path).expanduser()
            if path.exists():
                instructions_text = path.read_text(encoding="utf-8").strip()
        snapshot = {
            "action": action or "poll",
            "daemon": self._lease_snapshot(),
            "queues": self._queue_depths(),
            "instructions_path": instructions_path or None,
            "instructions_present": bool(instructions_text),
            "throttled": throttled,
        }
        state["heartbeat"] = {
            "task_id": task.task_id,
            "source": task.source,
            "action": snapshot["action"],
            "checked_at": _utc_now(),
            "daemon_id": self.daemon_id,
            "instructions_path": instructions_path or None,
            "throttled": throttled,
        }
        state["daemon"] = snapshot["daemon"]
        state["queues"] = snapshot["queues"]
        _atomic_write_json(self.paths.heartbeat_state, state)
        output_text = "HEARTBEAT_OK" if throttled else json.dumps(snapshot, ensure_ascii=False, indent=2)
        if instructions_text and not throttled:
            output_text = instructions_text
        return BridgeResult(
            task_id=task.task_id,
            status="succeeded",
            output_text=output_text,
            adapter="bridge_heartbeat",
            raw={
                "heartbeat": snapshot,
                "route": {"agent": "bridge_heartbeat", "reason": "local_heartbeat"},
            },
        )

    def _lease_snapshot(self) -> dict[str, Any]:
        lease = _read_json_file(self.lease_path)
        lease["stale"] = self._lease_is_stale(lease)
        return lease

    def _queue_depths(self) -> dict[str, int]:
        return {
            "inbox": sum(1 for _ in self.paths.inbox.glob("*.json")),
            "processing": sum(1 for _ in self.paths.processing.glob("*.json")),
            "outbox": sum(1 for _ in self.paths.outbox.glob("*.json")),
            "failed": sum(1 for _ in self.paths.failed.glob("*.json")),
        }

    def _recover_stale_processing(self, *, stale_after_sec: int | None = None) -> list[str]:
        recovered: list[str] = []
        threshold = self.processing_stale_after_sec if stale_after_sec is None else max(int(stale_after_sec), 0)
        now = datetime.now(timezone.utc)
        for processing_file in sorted(self.paths.processing.glob("*.json")):
            payload = _read_json_file(processing_file)
            metadata = payload.get("metadata", {})
            processing = metadata.get("processing", {})
            owner_id = str(processing.get("daemon_id", "")).strip()
            claimed_at = _parse_utc(processing.get("claimed_at")) or datetime.fromtimestamp(
                processing_file.stat().st_mtime,
                tz=timezone.utc,
            )
            age_sec = max(0.0, (now - claimed_at).total_seconds())
            if age_sec < threshold:
                continue
            owner_lease = self.paths.leases / f"{owner_id}.json" if owner_id else None
            if owner_id == self.daemon_id:
                continue
            if owner_lease and owner_lease.exists() and not self._lease_is_stale(_read_json_file(owner_lease)):
                continue

            processing["status"] = "requeued"
            processing["recovered_at"] = _utc_now()
            processing["recovered_by"] = self.daemon_id
            processing["recovered_from"] = owner_id or "unknown"
            metadata["processing"] = processing
            payload["metadata"] = metadata
            _atomic_write_json(processing_file, payload)
            inbox_file = self.paths.inbox / processing_file.name
            shutil.move(str(processing_file), str(inbox_file))
            self._write_log(
                payload.get("task_id", processing_file.stem),
                {
                    "event": "requeued_stale_processing",
                    "task_id": payload.get("task_id", processing_file.stem),
                    "owner": owner_id or "unknown",
                    "recovered_by": self.daemon_id,
                    "age_sec": age_sec,
                },
            )
            recovered.append(str(payload.get("task_id", processing_file.stem)))
        return recovered

    def _lease_is_stale(self, lease: dict[str, Any]) -> bool:
        heartbeat_at = _parse_utc(str(lease.get("last_heartbeat_at", "")))
        stale_after = int(lease.get("lease_stale_after_sec", self.lease_stale_after_sec) or self.lease_stale_after_sec)
        if heartbeat_at is None:
            return True
        age = (datetime.now(timezone.utc) - heartbeat_at).total_seconds()
        return age > stale_after
