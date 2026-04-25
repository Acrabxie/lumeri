from __future__ import annotations

import json
from collections import namedtuple
from pathlib import Path

from gemia.automation.common import automation_env, automation_tool_path, proxyless_env, safe_slug
from gemia.automation import loop_controller
from gemia.automation.loop_controller import (
    IMAGE_TARGET,
    VIDEO_TARGET,
    _build_catalog,
    _catalog_progress,
    _rollover_queue_snapshot,
    _stock_root,
    build_rollover_prompt,
)


def test_build_catalog_hits_requested_media_targets() -> None:
    catalog = _build_catalog()
    progress = _catalog_progress(catalog)

    assert progress["images_done"] == 0
    assert progress["videos_done"] == 0
    assert progress["images_target"] == IMAGE_TARGET
    assert progress["videos_target"] == VIDEO_TARGET
    assert sum(task.get("count", 0) for task in catalog if task["kind"] == "image") == IMAGE_TARGET
    assert sum(1 for task in catalog if task["kind"] == "video") == VIDEO_TARGET


def test_build_rollover_prompt_mentions_core_loop_contract() -> None:
    state = {
        "stock_root": "/Volumes/NO NAME/gemia-stock",
        "last_heartbeat_at": "2026-04-20T00:00:00+00:00",
        "last_rollover_at": "2026-04-20T01:00:00+00:00",
        "last_stock_fill_at": "2026-04-20T02:00:00+00:00",
    }
    prompt = build_rollover_prompt(
        state,
        {
            "videos_done": 10,
            "videos_target": VIDEO_TARGET,
            "images_done": 50,
            "images_target": IMAGE_TARGET,
        },
    )

    assert "Gemini-native generation via GEMINI_API_KEY" in prompt
    assert "three lanes every session" in prompt
    assert "/Volumes/NO NAME/gemia-stock" in prompt
    assert "Videos: 10/150" in prompt
    assert "Images: 50/1500" in prompt


def test_legacy_loop_entrypoint_delegates_to_five_day_controller() -> None:
    script = (Path(__file__).resolve().parents[1] / "scripts" / "gemia_loop.sh").read_text(encoding="utf-8")

    assert "_run_gemia_controller.sh" in script
    assert "tick-once" in script
    assert "agent_log.md" not in script
    assert "41" not in script


def test_proxyless_env_strips_proxy_settings() -> None:
    env = proxyless_env(
        {
            "HTTP_PROXY": "http://127.0.0.1:7890",
            "HTTPS_PROXY": "http://127.0.0.1:7890",
            "FOO": "bar",
        }
    )

    assert "HTTP_PROXY" not in env
    assert "HTTPS_PROXY" not in env
    assert env["FOO"] == "bar"
    assert env["NO_PROXY"] == "*"


def test_safe_slug_compacts_non_alnum_text() -> None:
    assert safe_slug("Rainy Alley / Blue Hour") == "rainy-alley-blue-hour"


def test_automation_env_adds_launchd_safe_tool_paths() -> None:
    env = automation_env({"PATH": "/usr/bin", "HTTP_PROXY": "http://127.0.0.1:7890"})

    assert env["NO_PROXY"] == "*"
    assert "HTTP_PROXY" not in env
    assert "/opt/homebrew/bin" in env["PATH"].split(":")
    assert "/usr/bin" in env["PATH"].split(":")


def test_automation_tool_path_deduplicates_paths() -> None:
    path = automation_tool_path("/usr/bin:/usr/bin")

    parts = path.split(":")
    assert parts.count("/usr/bin") == 1


def test_stock_root_migrates_to_preferred_free_space(tmp_path, monkeypatch) -> None:
    low_root = tmp_path / "low" / "gemia-stock"
    high_root = tmp_path / "high" / "gemia-stock"
    usage = namedtuple("usage", "total used free")

    def fake_disk_usage(path):
        free = 5 * 1024**3 if "low" in str(path) else 50 * 1024**3
        return usage(total=100 * 1024**3, used=100 * 1024**3 - free, free=free)

    state = {"stock_root": str(low_root)}
    monkeypatch.setenv("GEMIA_STOCK_PREFERRED_FREE_GIB", "20")
    monkeypatch.setattr("gemia.automation.loop_controller.shutil.disk_usage", fake_disk_usage)
    monkeypatch.setattr("gemia.automation.loop_controller.choose_stock_root", lambda min_free_bytes: high_root)
    monkeypatch.setattr("gemia.automation.loop_controller.append_agent_log", lambda message: None)
    monkeypatch.setattr("gemia.automation.loop_controller._save_state", lambda payload: None)

    assert _stock_root(state) == high_root
    assert state["stock_root"] == str(high_root)


def test_stock_root_migrates_when_configured_root_is_not_writable(tmp_path, monkeypatch) -> None:
    blocked_root = tmp_path / "blocked" / "gemia-stock"
    fallback_root = tmp_path / "fallback" / "gemia-stock"
    usage = namedtuple("usage", "total used free")

    monkeypatch.setattr(
        "gemia.automation.loop_controller.shutil.disk_usage",
        lambda path: usage(total=100 * 1024**3, used=50 * 1024**3, free=50 * 1024**3),
    )
    monkeypatch.setattr(
        "gemia.automation.loop_controller._can_write_stock_root",
        lambda path: path != blocked_root,
    )
    monkeypatch.setattr("gemia.automation.loop_controller.choose_stock_root", lambda min_free_bytes: fallback_root)
    monkeypatch.setattr("gemia.automation.loop_controller.append_agent_log", lambda message: None)
    monkeypatch.setattr("gemia.automation.loop_controller._save_state", lambda payload: None)

    state = {"stock_root": str(blocked_root)}

    assert _stock_root(state) == fallback_root
    assert state["stock_root"] == str(fallback_root)


def test_stock_root_falls_back_to_minimum_free_space_when_preferred_is_unavailable(tmp_path, monkeypatch) -> None:
    blocked_root = tmp_path / "blocked" / "gemia-stock"
    fallback_root = tmp_path / "fallback" / "gemia-stock"
    usage = namedtuple("usage", "total used free")

    def choose(min_free_bytes=3 * 1024**3):
        if min_free_bytes > 3 * 1024**3:
            raise RuntimeError("preferred root unavailable")
        return fallback_root

    monkeypatch.setattr(
        "gemia.automation.loop_controller.shutil.disk_usage",
        lambda path: usage(total=100 * 1024**3, used=50 * 1024**3, free=50 * 1024**3),
    )
    monkeypatch.setattr("gemia.automation.loop_controller._can_write_stock_root", lambda path: path != blocked_root)
    monkeypatch.setattr("gemia.automation.loop_controller.choose_stock_root", choose)
    monkeypatch.setattr("gemia.automation.loop_controller.append_agent_log", lambda message: None)
    monkeypatch.setattr("gemia.automation.loop_controller._save_state", lambda payload: None)

    state = {"stock_root": str(blocked_root)}

    assert _stock_root(state) == fallback_root
    assert state["stock_root"] == str(fallback_root)


def test_stock_root_prefers_minimum_external_root_over_local_fallback(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local" / "gemia-stock"
    external_root = tmp_path / "external" / "gemia-stock"
    usage = namedtuple("usage", "total used free")

    def choose(min_free_bytes=3 * 1024**3):
        if min_free_bytes > 3 * 1024**3:
            raise RuntimeError("preferred root unavailable")
        return external_root

    monkeypatch.setattr(
        "gemia.automation.loop_controller.shutil.disk_usage",
        lambda path: usage(total=100 * 1024**3, used=50 * 1024**3, free=50 * 1024**3),
    )
    monkeypatch.setattr("gemia.automation.loop_controller.choose_stock_root", choose)
    monkeypatch.setattr("gemia.automation.loop_controller.append_agent_log", lambda message: None)
    monkeypatch.setattr("gemia.automation.loop_controller._save_state", lambda payload: None)

    state = {"stock_root": str(local_root)}

    assert _stock_root(state) == external_root
    assert state["stock_root"] == str(external_root)


def test_stock_fill_uses_local_real_video_when_gemini_is_paused(tmp_path, monkeypatch) -> None:
    state_path = tmp_path / "state.json"
    catalog_path = tmp_path / "stock_catalog.json"
    manifest_path = tmp_path / "stock_manifest.json"
    stock_root = tmp_path / "stock"
    local_root = tmp_path / "local-media"
    local_root.mkdir()
    local_video = local_root / "real-source.mp4"
    local_video.write_bytes(b"fake mp4 bytes")
    state_path.write_text(
        json.dumps({"stock_root": str(stock_root), "stock_paused_reason": "gemini_location_unsupported"}),
        encoding="utf-8",
    )
    catalog_path.write_text(
        json.dumps(
            [
                {
                    "id": "video-0001",
                    "kind": "video",
                    "prompt": "mountain lake drone reveal natural motion",
                    "status": "pending",
                    "attempts": 0,
                    "outputs": [],
                }
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("GEMIA_LOCAL_STOCK_ROOTS", str(local_root))
    monkeypatch.setattr("gemia.automation.stock_sources.LocalStockClient._default_roots", staticmethod(lambda: [local_root]))
    monkeypatch.setattr(loop_controller, "runtime_state_path", lambda: state_path)
    monkeypatch.setattr(loop_controller, "stock_catalog_path", lambda: catalog_path)
    monkeypatch.setattr(loop_controller, "stock_manifest_path", lambda: manifest_path)
    monkeypatch.setattr(loop_controller, "choose_stock_root", lambda min_free_bytes: stock_root)
    monkeypatch.setattr(loop_controller, "append_agent_log", lambda message: None)
    monkeypatch.setattr(loop_controller, "append_human_needed", lambda title, details: None)

    result = loop_controller.stock_fill_once(image_limit=0, video_limit=1)
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    output_path = Path(catalog[0]["outputs"][0])

    assert result["fallback"] is True
    assert result["completed"] == ["video-0001"]
    assert catalog[0]["backend"] == "local_real_video"
    assert output_path.exists()
    assert output_path.read_bytes() == b"fake mp4 bytes"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["root_class"] == "local_workspace_fallback"
    assert manifest["root_outputs"]["videos"] == 1


def test_stock_fill_pauses_local_workspace_fallback_after_seed_cap(tmp_path, monkeypatch) -> None:
    state_path = tmp_path / "state.json"
    catalog_path = tmp_path / "stock_catalog.json"
    manifest_path = tmp_path / "stock_manifest.json"
    stock_root = tmp_path / "stock"
    state_path.write_text(
        json.dumps({"stock_root": str(stock_root), "stock_paused_reason": "gemini_location_unsupported"}),
        encoding="utf-8",
    )
    catalog_path.write_text(
        json.dumps(
            [
                {
                    "id": "video-0001",
                    "kind": "video",
                    "prompt": "mountain lake drone reveal natural motion",
                    "status": "completed",
                    "outputs": [str(stock_root / "videos" / "seed.mp4")],
                    "backend": "local_real_video",
                },
                {
                    "id": "image-0001",
                    "kind": "image",
                    "prompt": "mountain lake editorial visual treatment",
                    "status": "completed",
                    "outputs": [
                        str(stock_root / "images" / "image-0001" / f"seed_{index}.png")
                        for index in range(4)
                    ],
                    "backend": "local_video_frame",
                },
                {
                    "id": "video-0002",
                    "kind": "video",
                    "prompt": "desert highway slow pan",
                    "status": "pending",
                    "outputs": [],
                },
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("GEMIA_LOCAL_FALLBACK_MAX_VIDEOS", "1")
    monkeypatch.setenv("GEMIA_LOCAL_FALLBACK_MAX_IMAGES", "4")
    monkeypatch.setattr(loop_controller, "runtime_state_path", lambda: state_path)
    monkeypatch.setattr(loop_controller, "stock_catalog_path", lambda: catalog_path)
    monkeypatch.setattr(loop_controller, "stock_manifest_path", lambda: manifest_path)
    monkeypatch.setattr(loop_controller, "choose_stock_root", lambda min_free_bytes: stock_root)
    monkeypatch.setattr(loop_controller, "append_agent_log", lambda message: None)
    monkeypatch.setattr(loop_controller, "append_human_needed", lambda title, details: None)

    result = loop_controller.stock_fill_once(image_limit=1, video_limit=1)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert result["completed"] == []
    assert result["storage_paused"] == "external_storage_needed"
    assert state["stock_local_fallback_paused_reason"] == "external_storage_needed"
    assert manifest["external_storage_needed"] is True
    assert manifest["root_outputs"] == {"videos": 1, "images": 4}


def test_stock_fill_records_fallback_failures_for_breaker(tmp_path, monkeypatch) -> None:
    state_path = tmp_path / "state.json"
    catalog_path = tmp_path / "stock_catalog.json"
    manifest_path = tmp_path / "stock_manifest.json"
    stock_root = tmp_path / "stock"
    empty_local_root = tmp_path / "empty-media"
    empty_local_root.mkdir()
    state_path.write_text(
        json.dumps({"stock_root": str(stock_root), "stock_paused_reason": "gemini_location_unsupported"}),
        encoding="utf-8",
    )
    catalog_path.write_text(
        json.dumps(
            [
                {
                    "id": "video-0001",
                    "kind": "video",
                    "prompt": "mountain lake drone reveal natural motion",
                    "status": "pending",
                    "attempts": 0,
                    "outputs": [],
                }
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("gemia.automation.stock_sources.LocalStockClient._default_roots", staticmethod(lambda: [empty_local_root]))
    monkeypatch.setattr(loop_controller, "runtime_state_path", lambda: state_path)
    monkeypatch.setattr(loop_controller, "stock_catalog_path", lambda: catalog_path)
    monkeypatch.setattr(loop_controller, "stock_manifest_path", lambda: manifest_path)
    monkeypatch.setattr(loop_controller, "choose_stock_root", lambda min_free_bytes: stock_root)
    monkeypatch.setattr(loop_controller, "append_agent_log", lambda message: None)
    monkeypatch.setattr(loop_controller, "append_human_needed", lambda title, details: None)

    result = loop_controller.stock_fill_once(image_limit=0, video_limit=1)
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert result["completed"] == []
    assert state["failure_counts"]["stock-fallback:video-0001"] == 1


def test_local_stock_fallback_ignores_already_used_sources(tmp_path) -> None:
    from gemia.automation.stock_sources import LocalStockClient

    local_root = tmp_path / "local-media"
    local_root.mkdir()
    first = local_root / "first.mp4"
    second = local_root / "second.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    client = LocalStockClient(roots=[local_root])
    output = tmp_path / "out" / "copied.mp4"

    asset = client.copy_video(prompt="city skyline", output_path=output, used_sources={str(first.resolve())})

    assert asset.backend == "local_real_video"
    assert asset.source == str(second.resolve())
    assert output.read_bytes() == b"second"


def test_rollover_failure_persists_local_queue_without_tripping_breaker(tmp_path, monkeypatch) -> None:
    state_path = tmp_path / "state.json"
    catalog_path = tmp_path / "stock_catalog.json"
    logs_dir = tmp_path / "logs"
    rollover_dir = tmp_path / "rollovers"
    bridge_root = tmp_path / "bridge"
    state_path.write_text(json.dumps({"stock_root": str(tmp_path / "stock")}), encoding="utf-8")
    catalog_path.write_text(
        json.dumps(
            [
                {
                    "id": "video-0001",
                    "kind": "video",
                    "prompt": "street market camera move",
                    "status": "pending",
                    "outputs": [],
                }
            ]
        ),
        encoding="utf-8",
    )

    class FailedRun:
        returncode = 1
        stdout = "partial"
        stderr = "network unavailable"

    monkeypatch.setattr(loop_controller, "runtime_state_path", lambda: state_path)
    monkeypatch.setattr(loop_controller, "stock_catalog_path", lambda: catalog_path)
    monkeypatch.setattr(loop_controller, "runtime_logs_dir", lambda: logs_dir)
    monkeypatch.setattr(loop_controller, "rollover_queue_dir", lambda: rollover_dir)
    monkeypatch.setattr(loop_controller, "bridge_root", lambda: bridge_root)
    monkeypatch.setattr(loop_controller.subprocess, "run", lambda *args, **kwargs: FailedRun())
    monkeypatch.setattr(loop_controller, "append_agent_log", lambda message: None)
    monkeypatch.setattr(loop_controller, "append_human_needed", lambda title, details: None)

    result = loop_controller.rollover_once(force=True)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    fallback = Path(result["queue_path"])
    payload = json.loads(fallback.read_text(encoding="utf-8"))

    assert result["queued"] is True
    assert payload["status"] == "pending"
    assert payload["reason"] == "codex_acp_failed"
    assert payload["next_agent"] == "claude_code"
    assert payload["review_agent"] == "antigravity"
    assert Path(payload["claude_code_queue"]).exists()
    assert state["last_rollover_status"] == "queued_offline"
    assert "rollover-codex" not in state.get("failure_counts", {})


def test_rollover_offline_only_persists_queue_without_calling_codex(tmp_path, monkeypatch) -> None:
    state_path = tmp_path / "state.json"
    catalog_path = tmp_path / "stock_catalog.json"
    logs_dir = tmp_path / "logs"
    rollover_dir = tmp_path / "rollovers"
    bridge_root = tmp_path / "bridge"
    state_path.write_text(json.dumps({"stock_root": str(tmp_path / "stock")}), encoding="utf-8")
    catalog_path.write_text(json.dumps([]), encoding="utf-8")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Codex ACP should be skipped in offline-only mode")

    monkeypatch.setenv("GEMIA_ROLLOVER_OFFLINE_ONLY", "1")
    monkeypatch.setattr(loop_controller, "runtime_state_path", lambda: state_path)
    monkeypatch.setattr(loop_controller, "stock_catalog_path", lambda: catalog_path)
    monkeypatch.setattr(loop_controller, "runtime_logs_dir", lambda: logs_dir)
    monkeypatch.setattr(loop_controller, "rollover_queue_dir", lambda: rollover_dir)
    monkeypatch.setattr(loop_controller, "bridge_root", lambda: bridge_root)
    monkeypatch.setattr(loop_controller.subprocess, "run", fail_if_called)
    monkeypatch.setattr(loop_controller, "append_agent_log", lambda message: None)

    result = loop_controller.rollover_once(force=True)

    assert result["returncode"] is None
    assert result["queued"] is True
    assert Path(result["queue_path"]).exists()


def test_rollover_queue_snapshot_reports_pending_files(tmp_path, monkeypatch) -> None:
    rollover_dir = tmp_path / "rollovers"
    rollover_dir.mkdir()
    first = rollover_dir / "rollover-1.json"
    first.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(loop_controller, "rollover_queue_dir", lambda: rollover_dir)

    snapshot = _rollover_queue_snapshot(
        {
            "last_rollover_status": "queued_offline",
            "last_rollover_fallback_path": str(first),
        }
    )

    assert snapshot["pending"] == 1
    assert snapshot["oldest_pending"] == str(first)
