import json
import sys
from pathlib import Path

import server
from gemia.creative_sandbox import (
    CREATIVE_SANDBOX_EVENT_TYPES,
    CreativeSandboxError,
    CreativeSandboxService,
)
from tests_http_harness import create_raw_request, run_server_handler


def test_creative_sandbox_creates_auditable_workspace_layout(tmp_path: Path) -> None:
    service = CreativeSandboxService(tmp_path)

    result = service.create_workspace(
        {
            "session_id": "devsess_1",
            "project_id": "proj_dev",
            "goal": "build a coded preview",
        },
        account_id="acct_demo",
    )

    assert result["status"] == "succeeded"
    workspace = result["workspace"]
    assert workspace["meta"]["status"] == "ready"
    assert workspace["meta"]["project_id"] == "proj_dev"
    assert set(workspace["layout"]) == {"scripts", "artifacts", "previews", "skills", "logs"}
    assert "dev_workspace_ready" in CREATIVE_SANDBOX_EVENT_TYPES
    for path in workspace["layout"].values():
        assert Path(path).is_dir()
    assert result["events"][-1]["type"] == "dev_workspace_ready"
    assert "Traceback" not in json.dumps(result, ensure_ascii=False)


def test_creative_sandbox_writes_only_workspace_files_and_emits_events(tmp_path: Path) -> None:
    service = CreativeSandboxService(tmp_path)
    service.create_workspace({"session_id": "devsess_files"})

    script = service.write_file(
        "devsess_files",
        {"kind": "scripts", "path": "scene.py", "content": "print('scene')\n"},
    )
    artifact = service.write_file(
        "devsess_files",
        {"kind": "artifacts", "path": "reports/summary.json", "content": "{\"ok\": true}\n"},
    )
    skill = service.write_file(
        "devsess_files",
        {"kind": "skills", "path": "draft.md", "content": "# Draft\n"},
    )

    assert script["events"][0]["type"] == "dev_file_written"
    assert [event["type"] for event in artifact["events"]] == ["dev_file_written", "dev_artifact_ready"]
    assert [event["type"] for event in skill["events"]] == ["dev_file_written", "dev_skill_draft_ready"]
    artifacts = service.list_artifacts("devsess_files")["artifacts"]
    assert {item["path"] for item in artifacts} >= {
        "scripts/scene.py",
        "artifacts/reports/summary.json",
        "skills/draft.md",
    }
    assert service.latest_preview("devsess_files")["has_preview"] is False
    preview = service.write_file(
        "devsess_files",
        {"kind": "previews", "path": "runtime-preview.mp4", "content": "fake mp4\n"},
    )
    assert preview["events"][-1]["type"] == "dev_artifact_ready"
    latest_preview = service.latest_preview("devsess_files")
    assert latest_preview["has_preview"] is True
    assert latest_preview["preview"]["path"] == "previews/runtime-preview.mp4"
    saved = service.read_file("devsess_files", {"kind": "scripts", "path": "scene.py"})
    assert saved["file"]["path"] == "scripts/scene.py"
    assert saved["content"] == "print('scene')\n"
    logs = service.list_logs("devsess_files")
    assert any(event["type"] == "dev_skill_draft_ready" for event in logs["events"])
    assert logs["events"][-1]["type"] == "dev_artifact_ready"
    report = service.report("devsess_files")
    assert report["brief"]["state"] == "preview_ready"
    assert report["brief"]["primary_path"] == "previews/runtime-preview.mp4"
    assert "reviewable preview" in report["brief"]["summary"]
    assert report["summary"]["file_counts"]["scripts"] == 1
    assert report["summary"]["has_preview"] is True
    assert report["summary"]["latest_preview_path"] == "previews/runtime-preview.mp4"
    assert report["next_diagnostic"]["kind"] == "review_preview"


def test_creative_sandbox_rejects_escape_and_secret_writes(tmp_path: Path) -> None:
    service = CreativeSandboxService(tmp_path)
    service.create_workspace({"session_id": "devsess_safe"})

    for payload in [
        {"kind": "scripts", "path": "../server.py", "content": "print(1)\n"},
        {"kind": "scripts", "path": "/tmp/server.py", "content": "print(1)\n"},
        {"kind": "scripts", "path": ".env", "content": "OPENROUTER_API_KEY=x\n"},
        {"kind": "scripts", "path": "ok.py", "content": "API_KEY=x\n"},
    ]:
        try:
            service.write_file("devsess_safe", payload)
        except CreativeSandboxError as exc:
            assert exc.code in {
                "path_forbidden",
                "secret_path_forbidden",
                "secret_content_forbidden",
            }
        else:
            raise AssertionError(f"unsafe write was accepted: {payload}")

    assert not (tmp_path / "server.py").exists()


def test_creative_sandbox_command_events_are_audit_only(tmp_path: Path) -> None:
    service = CreativeSandboxService(tmp_path)
    service.create_workspace({"session_id": "devsess_cmd"})

    started = service.record_command_event(
        "devsess_cmd",
        {"phase": "started", "command_id": "cmd_demo", "command": "python scene.py"},
    )
    finished = service.record_command_event(
        "devsess_cmd",
        {"phase": "finished", "command_id": "cmd_demo", "exit_code": 0, "summary": "ok"},
    )

    assert started["event"]["type"] == "dev_command_started"
    assert started["event"]["payload"]["executed"] is False
    assert finished["event"]["type"] == "dev_command_finished"
    assert finished["event"]["payload"]["exit_code"] == 0


def test_creative_sandbox_http_routes_are_vnext_gated(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(server, "_BASE_DIR", tmp_path)
    monkeypatch.delenv("LUMERAI_VNEXT", raising=False)

    disabled = run_server_handler(
        server._Handler,
        create_raw_request("POST", "/runtime/dev/workspace", body={"session_id": "dev_http"}),
    )
    assert disabled["status"] == 404

    monkeypatch.setenv("LUMERAI_VNEXT", "1")
    created = run_server_handler(
        server._Handler,
        create_raw_request(
            "POST",
            "/runtime/dev/workspace",
            body={"session_id": "dev_http", "project_id": "proj_http"},
        ),
    )
    assert created["status"] == 200
    assert created["body_json"]["workspace"]["meta"]["project_id"] == "proj_http"

    written = run_server_handler(
        server._Handler,
        create_raw_request(
            "POST",
            "/runtime/dev/workspace/dev_http/files",
            body={"kind": "artifacts", "path": "status.json", "content": "{\"ready\": true}\n"},
        ),
    )
    assert written["status"] == 200
    assert [event["type"] for event in written["body_json"]["events"]] == [
        "dev_file_written",
        "dev_artifact_ready",
    ]

    script = run_server_handler(
        server._Handler,
        create_raw_request(
            "POST",
            "/runtime/dev/workspace/dev_http/files",
            body={"kind": "scripts", "path": "runtime/script.py", "content": "print('saved')\n"},
        ),
    )
    assert script["status"] == 200
    assert script["body_json"]["file"]["path"] == "scripts/runtime/script.py"
    assert [event["type"] for event in script["body_json"]["events"]] == ["dev_file_written"]

    read_script = run_server_handler(
        server._Handler,
        create_raw_request("GET", "/runtime/dev/workspace/dev_http/files?kind=scripts&path=runtime/script.py"),
    )
    assert read_script["status"] == 200
    assert read_script["body_json"]["file"]["path"] == "scripts/runtime/script.py"
    assert read_script["body_json"]["content"] == "print('saved')\n"

    preview = run_server_handler(
        server._Handler,
        create_raw_request(
            "POST",
            "/runtime/dev/workspace/dev_http/files",
            body={"kind": "previews", "path": "runtime-preview.mp4", "content": "fake mp4 bytes"},
        ),
    )
    assert preview["status"] == 200
    raw_preview = run_server_handler(
        server._Handler,
        create_raw_request("GET", "/runtime/dev/workspace/dev_http/files?raw=1&kind=previews&path=runtime-preview.mp4"),
    )
    assert raw_preview["status"] == 200
    assert raw_preview["headers"]["content-type"].startswith("video/mp4")
    assert raw_preview["body"] == b"fake mp4 bytes"
    latest_preview = run_server_handler(
        server._Handler,
        create_raw_request("GET", "/runtime/dev/workspace/dev_http/preview"),
    )
    assert latest_preview["status"] == 200
    assert latest_preview["body_json"]["has_preview"] is True
    assert latest_preview["body_json"]["preview"]["path"] == "previews/runtime-preview.mp4"
    assert latest_preview["body_json"]["raw_url"].endswith("kind=previews&path=runtime-preview.mp4")
    report = run_server_handler(
        server._Handler,
        create_raw_request("GET", "/runtime/dev/workspace/dev_http/report"),
    )
    assert report["status"] == 200
    assert report["body_json"]["summary"]["has_preview"] is True
    assert report["body_json"]["brief"]["state"] == "preview_ready"
    assert report["body_json"]["brief"]["primary_path"] == "previews/runtime-preview.mp4"
    assert report["body_json"]["summary"]["latest_preview_path"] == "previews/runtime-preview.mp4"
    assert report["body_json"]["preview"]["raw_url"].endswith("kind=previews&path=runtime-preview.mp4")
    assert report["body_json"]["next_diagnostic"]["kind"] == "review_preview"

    artifacts = run_server_handler(
        server._Handler,
        create_raw_request("GET", "/runtime/dev/workspace/dev_http/artifacts"),
    )
    assert artifacts["status"] == 200
    paths = {item["path"] for item in artifacts["body_json"]["artifacts"]}
    assert "artifacts/status.json" in paths
    assert "scripts/runtime/script.py" in paths
    assert "previews/runtime-preview.mp4" in paths

    logs = run_server_handler(
        server._Handler,
        create_raw_request("GET", "/runtime/dev/workspace/dev_http/logs"),
    )
    assert logs["status"] == 200
    assert any(event["type"] == "dev_file_written" for event in logs["body_json"]["events"])
    assert any(event["type"] == "dev_artifact_ready" for event in logs["body_json"]["events"])
    assert "Traceback" not in json.dumps(logs["body_json"], ensure_ascii=False)


def test_creative_sandbox_http_run_executes_allowed_command(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(server, "_BASE_DIR", tmp_path)
    monkeypatch.setenv("LUMERAI_VNEXT", "1")

    created = run_server_handler(
        server._Handler,
        create_raw_request("POST", "/runtime/dev/workspace", body={"session_id": "dev_run"}),
    )
    assert created["status"] == 200

    result = run_server_handler(
        server._Handler,
        create_raw_request(
            "POST",
            "/runtime/dev/workspace/dev_run/run",
            body={
                "args": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path\nPath('artifacts/out.txt').write_text('ok', encoding='utf-8')\nPath('previews').mkdir(exist_ok=True)\nPath('previews/runtime-preview.mp4').write_bytes(b'fake mp4')\nprint('done')\n",
                ],
                "timeout_sec": 5,
                "declared_artifact_paths": ["previews/runtime-preview.mp4"],
            },
        ),
    )

    assert result["status"] == 200
    payload = result["body_json"]
    assert payload["status"] == "succeeded"
    assert payload["result"]["exit_code"] == 0
    assert payload["result"]["stdout_tail"].strip() == "done"
    assert any(event["type"] == "dev_command_finished" for event in payload["events"])
    assert any(event["type"] == "dev_artifact_ready" for event in payload["events"])
    assert any(item["path"] == "artifacts/out.txt" for item in payload["artifacts"])
    assert payload["preview"]["has_preview"] is True
    assert payload["preview"]["preview"]["path"] == "previews/runtime-preview.mp4"
    assert payload["preview"]["raw_url"].endswith("kind=previews&path=runtime-preview.mp4")
    assert payload["report"]["summary"]["command_count"] == 1
    assert payload["report"]["brief"]["state"] == "preview_ready"
    assert payload["report"]["brief"]["primary_path"] == "previews/runtime-preview.mp4"
    assert payload["report"]["next_diagnostic"]["kind"] == "review_preview"
    assert (tmp_path / "workspaces" / "dev_run" / "previews" / "runtime-preview.mp4").is_file()
    assert "Traceback" not in json.dumps(payload, ensure_ascii=False)


def test_creative_sandbox_http_run_blocks_network_command(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(server, "_BASE_DIR", tmp_path)
    monkeypatch.setenv("LUMERAI_VNEXT", "1")
    run_server_handler(
        server._Handler,
        create_raw_request("POST", "/runtime/dev/workspace", body={"session_id": "dev_block"}),
    )

    result = run_server_handler(
        server._Handler,
        create_raw_request(
            "POST",
            "/runtime/dev/workspace/dev_block/run",
            body={"args": ["curl", "https://example.com"]},
        ),
    )

    assert result["status"] == 200
    payload = result["body_json"]
    assert payload["status"] == "blocked"
    assert payload["result"]["error"]["code"] == "blocked_command"
    assert [event["type"] for event in payload["events"]][-1] == "dev_command_finished"
    assert payload["report"]["summary"]["failure_count"] == 1
    assert payload["report"]["brief"]["state"] == "failed"
    assert payload["report"]["brief"]["next_action"] == "Inspect failure logs and revise the runtime script."
    assert payload["report"]["next_diagnostic"]["kind"] == "inspect_failure"
