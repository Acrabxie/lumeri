"""Single-writer discipline for project state.

Two writer populations exist in production: agent verbs on the session's
asyncio thread, and /timeline/op + undo on ThreadingHTTPServer handler
threads. Before the per-project lock, both could read ``patch_seq == N`` and
write ``patches/000(N+1).json`` — one history entry silently clobbered and the
last ``state.json`` write won. These tests hammer the store from many threads
and assert the log is gapless and every write survived; plus the
SessionRunner.run_project_edit loop-hop contract.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from gemia.project_store import ProjectStore


def _seed_store(tmp_path: Path) -> tuple[ProjectStore, str]:
    store = ProjectStore(tmp_path / "projects")
    pid = "proj-conc"
    store.create(pid)
    return store, pid


def test_concurrent_apply_patches_loses_nothing(tmp_path: Path) -> None:
    store, pid = _seed_store(tmp_path)
    threads, per_thread = 8, 12
    errors: list[BaseException] = []
    barrier = threading.Barrier(threads)

    def _worker(worker_id: int) -> None:
        try:
            barrier.wait(timeout=10)
            for i in range(per_thread):
                asset_id = f"a{worker_id:02d}_{i:02d}"
                store.apply_patches(
                    pid,
                    [{"version": 1, "ops": [{"op": "upsert_asset", "asset": {
                        "id": asset_id, "asset_id": asset_id,
                        "name": f"{asset_id}.mp4", "media_kind": "video",
                        "source_path": f"/tmp/{asset_id}.mp4", "duration": 1.0,
                    }}]}],
                    session_id=f"sid-{worker_id}",
                    script_hash="conc-test",
                )
        except BaseException as exc:  # noqa: BLE001 — surface in main thread
            errors.append(exc)

    ts = [threading.Thread(target=_worker, args=(w,)) for w in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=60)
    assert not errors, f"writer raised: {errors[:3]}"

    total = threads * per_thread
    meta = store.load_meta(pid)
    assert int(meta["patch_seq"]) == total

    # Gapless patch log: every seq 1..total exists exactly once.
    files = sorted(store.patches_dir(pid).glob("*.json"))
    seqs = sorted(int(json.loads(p.read_text())["seq"]) for p in files)
    assert seqs == list(range(1, total + 1))

    # No lost update: every worker's every asset survived into final state.
    assets = {a["id"] for a in store.load(pid)["assets"]}
    expected = {f"a{w:02d}_{i:02d}" for w in range(threads) for i in range(per_thread)}
    assert expected <= assets


def test_concurrent_undo_and_apply_keep_log_consistent(tmp_path: Path) -> None:
    """undo_to_seq rebuilds from seed while apply appends — under the lock the
    two serialize; afterwards the meta seq must equal the surviving on-disk
    log's max seq (no orphaned or clobbered files)."""
    store, pid = _seed_store(tmp_path)
    for i in range(10):
        store.apply_patches(
            pid,
            [{"version": 1, "ops": [{"op": "upsert_asset", "asset": {
                "id": f"seed{i}", "asset_id": f"seed{i}", "name": f"s{i}.mp4",
                "media_kind": "video", "source_path": f"/tmp/s{i}.mp4",
                "duration": 1.0,
            }}]}],
            session_id="seed", script_hash="seed",
        )

    stop = threading.Event()
    errors: list[BaseException] = []

    def _appender() -> None:
        i = 0
        try:
            while not stop.is_set():
                store.apply_patches(
                    pid,
                    [{"version": 1, "ops": [{"op": "upsert_asset", "asset": {
                        "id": f"app{i}", "asset_id": f"app{i}", "name": "a.mp4",
                        "media_kind": "video", "source_path": "/tmp/a.mp4",
                        "duration": 1.0,
                    }}]}],
                    session_id="appender", script_hash="conc",
                )
                i += 1
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def _undoer() -> None:
        try:
            for _ in range(5):
                meta = store.load_meta(pid)
                target = max(0, int(meta["patch_seq"]) - 3)
                store.undo_to_seq(pid, target)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    ta, tu = threading.Thread(target=_appender), threading.Thread(target=_undoer)
    ta.start(); tu.start()
    tu.join(timeout=60)
    stop.set()
    ta.join(timeout=60)
    assert not errors, f"writer raised: {errors[:3]}"

    meta = store.load_meta(pid)
    files = sorted(store.patches_dir(pid).glob("*.json"))
    seqs = sorted(int(json.loads(p.read_text())["seq"]) for p in files)
    assert seqs == list(range(1, int(meta["patch_seq"]) + 1))


def test_run_project_edit_executes_on_the_session_loop_thread() -> None:
    """The loop-hop contract, tested against a real runner-shaped object:
    fn runs on the session's loop thread and results/exceptions propagate."""
    import asyncio

    from gemia.session_manager import SessionRunner

    runner = SessionRunner.__new__(SessionRunner)  # skip agent creation
    runner._loop = asyncio.new_event_loop()
    runner._state_lock = threading.Lock()
    runner.last_used_at = 0.0
    ready = threading.Event()

    def _run() -> None:
        asyncio.set_event_loop(runner._loop)
        ready.set()
        runner._loop.run_forever()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    ready.wait(timeout=5)
    try:
        seen: dict[str, Any] = {}

        def _edit() -> str:
            seen["thread"] = threading.current_thread()
            return "done"

        assert runner.run_project_edit(_edit) == "done"
        assert seen["thread"] is t

        with pytest.raises(ValueError, match="boom"):
            runner.run_project_edit(lambda: (_ for _ in ()).throw(ValueError("boom")))

        runner._loop.call_soon_threadsafe(runner._loop.stop)
        t.join(timeout=5)
        runner._loop.close()
        with pytest.raises(RuntimeError, match="closed"):
            runner.run_project_edit(lambda: "nope")
    finally:
        if not runner._loop.is_closed():
            runner._loop.call_soon_threadsafe(runner._loop.stop)
            t.join(timeout=5)
            runner._loop.close()
