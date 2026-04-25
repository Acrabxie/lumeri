from __future__ import annotations

import json
from pathlib import Path

import pytest

from gemia.bridge import (
    BridgeDaemon,
    BridgePaths,
    BridgeResult,
    BridgeTask,
    ControllerAdapter,
    MasterBridgeController,
    QueueBridgeAdapter,
)


class FakeAdapter:
    name = "fake"

    def __init__(self) -> None:
        self.seen: list[str] = []

    def run(self, task: BridgeTask) -> BridgeResult:
        self.seen.append(task.task_id)
        return BridgeResult(
            task_id=task.task_id,
            status="succeeded",
            output_text=f"handled: {task.prompt}",
            artifacts=task.assets,
            adapter=self.name,
            raw={"intent": task.intent},
        )


class FailingAdapter:
    name = "fake_fail"

    def run(self, task: BridgeTask) -> BridgeResult:
        return BridgeResult(
            task_id=task.task_id,
            status="failed",
            output_text="",
            adapter=self.name,
            error="boom",
        )


class TestBridgePaths:
    def test_ensure_creates_expected_directories(self, tmp_path: Path) -> None:
        paths = BridgePaths.from_root(tmp_path / "bridge")
        paths.ensure()
        assert paths.inbox.is_dir()
        assert paths.processing.is_dir()
        assert paths.outbox.is_dir()
        assert paths.failed.is_dir()
        assert paths.logs.is_dir()


class TestBridgeDaemon:
    def test_submit_and_process_success(self, tmp_path: Path) -> None:
        paths = BridgePaths.from_root(tmp_path / "bridge")
        adapter = FakeAdapter()
        daemon = BridgeDaemon(paths, adapter)
        task = BridgeTask.new(
            source="antigravity",
            intent="edit",
            prompt="do the thing",
            assets=["/tmp/a.png"],
        )

        payload_path = daemon.submit_task(task)
        assert payload_path.exists()
        assert payload_path.parent == paths.inbox

        processed = daemon.process_once()
        assert processed == 1
        result_path = paths.outbox / f"{task.task_id}.json"
        assert result_path.exists()
        payload = json.loads(result_path.read_text())
        assert payload["status"] == "succeeded"
        assert payload["output_text"] == "handled: do the thing"
        assert adapter.seen == [task.task_id]
        assert not (paths.processing / f"{task.task_id}.json").exists()

    def test_process_failure_goes_to_failed(self, tmp_path: Path) -> None:
        paths = BridgePaths.from_root(tmp_path / "bridge")
        daemon = BridgeDaemon(paths, FailingAdapter())
        task = BridgeTask.new(source="antigravity", intent="generate", prompt="fail")
        daemon.submit_task(task)

        processed = daemon.process_once()
        assert processed == 1
        result_path = paths.failed / f"{task.task_id}.json"
        assert result_path.exists()
        payload = json.loads(result_path.read_text())
        assert payload["status"] == "failed"
        assert payload["error"] == "boom"

    def test_process_once_noop_when_inbox_empty(self, tmp_path: Path) -> None:
        daemon = BridgeDaemon(BridgePaths.from_root(tmp_path / "bridge"), FakeAdapter())
        assert daemon.process_once() == 0

    def test_process_heartbeat_task_locally_writes_state(self, tmp_path: Path) -> None:
        paths = BridgePaths.from_root(tmp_path / "bridge")
        daemon = BridgeDaemon(paths, FakeAdapter())
        instructions = tmp_path / "HEARTBEAT.md"
        instructions.write_text("check bridge health\n", encoding="utf-8")
        task = BridgeTask.new(
            source="codex",
            intent="heartbeat",
            prompt="heartbeat poll",
            metadata={"heartbeat": True, "instructions_path": str(instructions)},
        )
        daemon.submit_task(task)

        processed = daemon.process_once()

        assert processed == 1
        result = json.loads((paths.outbox / f"{task.task_id}.json").read_text())
        assert result["status"] == "succeeded"
        assert result["adapter"] == "bridge_heartbeat"
        assert result["output_text"] == "check bridge health"
        heartbeat_state = json.loads(paths.heartbeat_state.read_text())
        assert heartbeat_state["heartbeat"]["task_id"] == task.task_id
        assert heartbeat_state["heartbeat"]["instructions_path"] == str(instructions)

    def test_process_heartbeat_task_can_throttle_to_heartbeat_ok(self, tmp_path: Path) -> None:
        paths = BridgePaths.from_root(tmp_path / "bridge")
        daemon = BridgeDaemon(paths, FakeAdapter())
        task = BridgeTask.new(
            source="codex",
            intent="heartbeat",
            prompt="heartbeat poll",
            metadata={"heartbeat": True},
            context={"min_interval_sec": 3600},
        )
        daemon.submit_task(task)
        assert daemon.process_once() == 1

        task_2 = BridgeTask.new(
            source="codex",
            intent="heartbeat",
            prompt="heartbeat poll",
            metadata={"heartbeat": True},
            context={"min_interval_sec": 3600},
        )
        daemon.submit_task(task_2)
        assert daemon.process_once() == 1

        result = json.loads((paths.outbox / f"{task_2.task_id}.json").read_text())
        assert result["output_text"] == "HEARTBEAT_OK"
        assert result["raw"]["heartbeat"]["throttled"] is True

    def test_schedule_heartbeat_if_due_submits_heartbeat_task(self, tmp_path: Path) -> None:
        paths = BridgePaths.from_root(tmp_path / "bridge")
        instructions = tmp_path / "HEARTBEAT.md"
        instructions.write_text("do background work\n", encoding="utf-8")
        daemon = BridgeDaemon(
            paths,
            FakeAdapter(),
            auto_heartbeat_interval_sec=7200,
            auto_heartbeat_instructions_path=str(instructions),
        )

        task_id = daemon.schedule_heartbeat_if_due()

        assert task_id is not None
        inbox_file = paths.inbox / f"{task_id}.json"
        assert inbox_file.exists()
        payload = json.loads(inbox_file.read_text())
        assert payload["intent"] == "heartbeat"
        assert payload["metadata"]["submitted_by"] == "bridge_auto_scheduler"
        assert payload["metadata"]["instructions_path"] == str(instructions)

    def test_schedule_heartbeat_if_due_does_not_duplicate_within_interval(self, tmp_path: Path) -> None:
        paths = BridgePaths.from_root(tmp_path / "bridge")
        daemon = BridgeDaemon(paths, FakeAdapter(), auto_heartbeat_interval_sec=7200)

        first = daemon.schedule_heartbeat_if_due()
        second = daemon.schedule_heartbeat_if_due()

        assert first is not None
        assert second is None
        inbox_files = list(paths.inbox.glob("*.json"))
        assert len(inbox_files) == 1

    def test_heartbeat_writes_progress_event_for_processing_task(self, tmp_path: Path) -> None:
        paths = BridgePaths.from_root(tmp_path / "bridge")
        daemon = BridgeDaemon(paths, FakeAdapter())
        task = BridgeTask.new(source="antigravity", intent="edit", prompt="long running task")

        processing_file = paths.processing / f"{task.task_id}.json"
        processing_file.write_text(json.dumps(task.to_dict(), ensure_ascii=False, indent=2) + "\n")

        daemon.heartbeat_processing(task.task_id)

        log_path = paths.logs / f"{task.task_id}.ndjson"
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert any(event["event"] == "heartbeat" for event in events)
        heartbeat = next(event for event in events if event["event"] == "heartbeat")
        assert heartbeat["task_id"] == task.task_id
        assert heartbeat["state"] == "processing"

    def test_recover_stale_processing_requeues_task_for_retry(self, tmp_path: Path) -> None:
        paths = BridgePaths.from_root(tmp_path / "bridge")
        daemon = BridgeDaemon(paths, FakeAdapter())
        task = BridgeTask.new(source="antigravity", intent="edit", prompt="retry me")

        processing_file = paths.processing / f"{task.task_id}.json"
        processing_file.write_text(json.dumps(task.to_dict(), ensure_ascii=False, indent=2) + "\n")

        recovered = daemon.recover_stale_processing(max_age_sec=0)

        assert recovered == [task.task_id]
        assert not processing_file.exists()
        assert (paths.inbox / f"{task.task_id}.json").exists()

        log_path = paths.logs / f"{task.task_id}.ndjson"
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert any(event["event"] == "requeued_stale_processing" for event in events)


class TestMasterController:
    def test_preferred_agent_routes_to_claude(self, tmp_path: Path) -> None:
        claude = FakeAdapter()
        antigravity = QueueBridgeAdapter("antigravity", tmp_path / "ag")
        controller = MasterBridgeController(
            {"claude_code": claude, "antigravity": antigravity},
            default_agent="claude_code",
        )
        daemon = BridgeDaemon(BridgePaths.from_root(tmp_path / "bridge"), ControllerAdapter(controller))
        task = BridgeTask.new(source="antigravity", intent="edit", prompt="fix code")
        task.preferred_agent = "claude_code"
        daemon.submit_task(task)

        assert daemon.process_once() == 1
        result = json.loads((daemon.paths.outbox / f"{task.task_id}.json").read_text())
        assert result["adapter"] == "fake"
        assert result["raw"]["route"]["agent"] == "claude_code"

    def test_media_intent_delegates_to_antigravity_queue(self, tmp_path: Path) -> None:
        controller = MasterBridgeController(
            {"claude_code": FakeAdapter(), "antigravity": QueueBridgeAdapter("antigravity", tmp_path / "ag")},
            default_agent="claude_code",
        )
        daemon = BridgeDaemon(BridgePaths.from_root(tmp_path / "bridge"), ControllerAdapter(controller))
        task = BridgeTask.new(source="gemia", intent="media", prompt="generate visual concepts")
        daemon.submit_task(task)

        assert daemon.process_once() == 1
        result = json.loads((daemon.paths.outbox / f"{task.task_id}.json").read_text())
        assert result["status"] == "delegated"
        assert result["adapter"] == "antigravity"
        delegated = tmp_path / "ag" / "inbox" / f"{task.task_id}.json"
        assert delegated.exists()

    def test_allowlist_overrides_default_route(self, tmp_path: Path) -> None:
        controller = MasterBridgeController(
            {"claude_code": FakeAdapter(), "antigravity": QueueBridgeAdapter("antigravity", tmp_path / "ag")},
            default_agent="claude_code",
        )
        daemon = BridgeDaemon(BridgePaths.from_root(tmp_path / "bridge"), ControllerAdapter(controller))
        task = BridgeTask.new(source="gemia", intent="implement", prompt="build a feature")
        task.allowed_agents = ["antigravity"]
        daemon.submit_task(task)

        assert daemon.process_once() == 1
        result = json.loads((daemon.paths.outbox / f"{task.task_id}.json").read_text())
        assert result["raw"]["route"]["agent"] == "antigravity"

    def test_architecture_task_class_routes_to_claude(self, tmp_path: Path) -> None:
        claude = FakeAdapter()
        controller = MasterBridgeController(
            {"claude_code": claude, "antigravity": QueueBridgeAdapter("antigravity", tmp_path / "ag")},
            default_agent="claude_code",
        )
        daemon = BridgeDaemon(BridgePaths.from_root(tmp_path / "bridge"), ControllerAdapter(controller))
        task = BridgeTask.new(source="gemia", intent="design", prompt="refactor the engine")
        task.metadata["task_class"] = "architecture"
        daemon.submit_task(task)

        assert daemon.process_once() == 1
        result = json.loads((daemon.paths.outbox / f"{task.task_id}.json").read_text())
        assert result["raw"]["route"]["agent"] == "claude_code"
        assert claude.seen == [task.task_id]

    def test_review_task_class_routes_to_antigravity(self, tmp_path: Path) -> None:
        controller = MasterBridgeController(
            {"claude_code": FakeAdapter(), "antigravity": QueueBridgeAdapter("antigravity", tmp_path / "ag")},
            default_agent="claude_code",
        )
        daemon = BridgeDaemon(BridgePaths.from_root(tmp_path / "bridge"), ControllerAdapter(controller))
        task = BridgeTask.new(source="gemia", intent="review", prompt="review this patch")
        task.metadata["task_class"] = "review"
        daemon.submit_task(task)

        assert daemon.process_once() == 1
        result = json.loads((daemon.paths.outbox / f"{task.task_id}.json").read_text())
        assert result["raw"]["route"]["agent"] == "antigravity"
        delegated = json.loads((tmp_path / "ag" / "inbox" / f"{task.task_id}.json").read_text())
        assert delegated["metadata"]["review_mode"] == "code"

    def test_frontend_task_class_routes_to_antigravity_with_playwright(self, tmp_path: Path) -> None:
        controller = MasterBridgeController(
            {"claude_code": FakeAdapter(), "antigravity": QueueBridgeAdapter("antigravity", tmp_path / "ag")},
            default_agent="claude_code",
        )
        daemon = BridgeDaemon(BridgePaths.from_root(tmp_path / "bridge"), ControllerAdapter(controller))
        task = BridgeTask.new(source="gemia", intent="design", prompt="design a landing page")
        task.metadata["task_class"] = "frontend"
        daemon.submit_task(task)

        assert daemon.process_once() == 1
        delegated = json.loads((tmp_path / "ag" / "inbox" / f"{task.task_id}.json").read_text())
        assert delegated["metadata"]["review_mode"] == "frontend_design"
        assert "playwright" in delegated["metadata"]["tooling"]
