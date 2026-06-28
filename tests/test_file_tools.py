"""Tests for the host-side file-management verbs (gemia/tools/files.py).

All filesystem effects are confined to ``tmp_path``. The move-approval flow is
exercised with a *fake* AskBridge injected via ``ctx.extra["ask_bridge"]`` — no
real TTY, network, or user interaction. Safety refusals are asserted directly on
``_safe_path``.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

import pytest

import gemia.tools as T
from gemia.errors import ToolError
from gemia.tools._context import AssetRegistry, ToolContext
from gemia.tools import files as F


# ── helpers ──────────────────────────────────────────────────────────────────


def _ctx(output_dir: Path, *, ask_bridge: Any = None) -> ToolContext:
    extra: dict[str, Any] = {}
    if ask_bridge is not None:
        extra["ask_bridge"] = ask_bridge
    return ToolContext(
        session_id="test_files",
        output_dir=output_dir,
        registry=AssetRegistry(),
        emit_progress=lambda _u: None,
        extra=extra,
    )


class FakeAskBridge:
    """Minimal AskBridge stand-in: returns a canned answer, records the ask.

    ``answer`` is the raw ``{control_key: value}`` dict the real bridge would
    resolve the awaiting future with (or ``None`` to simulate a timeout).
    """

    def __init__(self, answer: Optional[dict[str, Any]]) -> None:
        self.answer = answer
        self.questions: list[dict[str, Any]] = []

    async def emit_and_wait(
        self, question: dict[str, Any], *, timeout: Optional[float] = None
    ) -> Optional[dict[str, Any]]:
        self.questions.append(question)
        return self.answer


def _run(coro):
    return asyncio.run(coro)


# ── read / write roundtrip ───────────────────────────────────────────────────


def test_read_write_roundtrip(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    target = tmp_path / "sub" / "note.txt"

    w = _run(F.dispatch_write_file({"path": str(target), "content": "hello world"}, ctx))
    assert w["bytes_written"] == len(b"hello world")
    assert Path(w["path"]) == target.resolve()
    assert target.read_text() == "hello world"

    r = _run(F.dispatch_read_file({"path": str(target)}, ctx))
    assert r["text"] == "hello world"
    assert r["truncated"] is False
    assert r["size"] == len(b"hello world")
    assert r["binary"] is False


def test_read_file_truncates(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    target = tmp_path / "big.txt"
    target.write_text("x" * 100)

    r = _run(F.dispatch_read_file({"path": str(target), "max_bytes": 10}, ctx))
    assert r["truncated"] is True
    assert len(r["text"]) == 10
    assert r["size"] == 100


def test_read_file_binary_returns_note_not_bytes(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    target = tmp_path / "blob.bin"
    target.write_bytes(b"\x00\x01\x02\x03\xff\xfe")

    r = _run(F.dispatch_read_file({"path": str(target)}, ctx))
    assert r["binary"] is True
    assert "binary" in r["text"].lower()
    assert r["size"] == 6


def test_read_missing_file_raises(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    with pytest.raises(ToolError) as ei:
        _run(F.dispatch_read_file({"path": str(tmp_path / "nope.txt")}, ctx))
    assert ei.value.code == "E_NOT_FOUND"


# ── append ───────────────────────────────────────────────────────────────────


def test_write_append(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    target = tmp_path / "log.txt"

    _run(F.dispatch_write_file({"path": str(target), "content": "line1\n"}, ctx))
    _run(F.dispatch_write_file({"path": str(target), "content": "line2\n", "append": True}, ctx))

    assert target.read_text() == "line1\nline2\n"

    # Without append, the second write overwrites.
    _run(F.dispatch_write_file({"path": str(target), "content": "fresh"}, ctx))
    assert target.read_text() == "fresh"


# ── copy_in ──────────────────────────────────────────────────────────────────


def test_copy_in_lands_file_in_output_dir(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external" / "doc.txt"
    external.parent.mkdir()
    external.write_text("payload")

    ctx = _ctx(workspace)
    res = _run(F.dispatch_copy_in({"path": str(external)}, ctx))

    landed = workspace / "doc.txt"
    assert landed.exists()
    assert landed.read_text() == "payload"
    assert Path(res["workspace_path"]) == landed.resolve()
    assert res["name"] == "doc.txt"
    assert res["size"] == len(b"payload")
    # Original is untouched.
    assert external.exists()


def test_copy_in_with_as_name_strips_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "src.txt"
    external.write_text("data")

    ctx = _ctx(workspace)
    # as_name carrying a traversal must be reduced to a basename inside workspace.
    res = _run(F.dispatch_copy_in({"path": str(external), "as_name": "../escape.txt"}, ctx))
    landed = Path(res["workspace_path"])
    assert landed.parent == workspace.resolve()
    assert res["name"] == "escape.txt"
    assert landed.read_text() == "data"


# ── list_dir ─────────────────────────────────────────────────────────────────


def test_list_dir_returns_entries(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    (tmp_path / "a.txt").write_text("aa")
    (tmp_path / "b.txt").write_text("bbb")
    (tmp_path / "subdir").mkdir()

    res = _run(F.dispatch_list_dir({"path": str(tmp_path)}, ctx))
    # The external SSD sprinkles AppleDouble '._' sidecar files; ignore them.
    by_name = {e["name"]: e for e in res["entries"] if not e["name"].startswith("._")}
    assert {"a.txt", "b.txt", "subdir"} <= set(by_name)
    assert by_name["a.txt"]["is_dir"] is False
    assert by_name["a.txt"]["size"] == 2
    assert by_name["subdir"]["is_dir"] is True


def test_list_dir_max_entries_truncates(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text("x")
    res = _run(F.dispatch_list_dir({"path": str(tmp_path), "max_entries": 2}, ctx))
    assert len(res["entries"]) == 2
    assert res["truncated"] is True


# ── move_file approval flow (mocked AskBridge, no TTY) ────────────────────────


def test_move_file_approved_moves(tmp_path: Path) -> None:
    src = tmp_path / "old.txt"
    dst = tmp_path / "moved" / "new.txt"
    src.write_text("content")

    bridge = FakeAskBridge({"approve": "yes"})
    ctx = _ctx(tmp_path, ask_bridge=bridge)

    res = _run(F.dispatch_move_file({"src": str(src), "dst": str(dst)}, ctx))

    assert res["status"] == "moved"
    assert not src.exists()
    assert dst.exists()
    assert dst.read_text() == "content"
    # An approval question was actually emitted, describing the move.
    assert len(bridge.questions) == 1
    assert "old.txt" in bridge.questions[0]["description"]


def test_move_file_declined_leaves_untouched(tmp_path: Path) -> None:
    src = tmp_path / "keep.txt"
    dst = tmp_path / "elsewhere.txt"
    src.write_text("stay")

    bridge = FakeAskBridge({"approve": "no"})
    ctx = _ctx(tmp_path, ask_bridge=bridge)

    res = _run(F.dispatch_move_file({"src": str(src), "dst": str(dst)}, ctx))

    assert res["status"] == "declined"
    assert src.exists()
    assert src.read_text() == "stay"
    assert not dst.exists()


def test_move_file_timeout_no_answer_declines(tmp_path: Path) -> None:
    src = tmp_path / "keep.txt"
    dst = tmp_path / "elsewhere.txt"
    src.write_text("stay")

    bridge = FakeAskBridge(None)  # simulates timeout / no answer
    ctx = _ctx(tmp_path, ask_bridge=bridge)

    res = _run(F.dispatch_move_file({"src": str(src), "dst": str(dst)}, ctx))
    assert res["status"] == "declined"
    assert src.exists()
    assert not dst.exists()


def test_move_file_no_bridge_declines(tmp_path: Path) -> None:
    src = tmp_path / "keep.txt"
    dst = tmp_path / "elsewhere.txt"
    src.write_text("stay")

    ctx = _ctx(tmp_path)  # no ask_bridge wired in

    res = _run(F.dispatch_move_file({"src": str(src), "dst": str(dst)}, ctx))
    assert res["status"] == "declined"
    assert src.exists()
    assert not dst.exists()


def test_organize_files_batch_approval(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("AA")
    b.write_text("BB")
    da = tmp_path / "out" / "a.txt"
    db = tmp_path / "out" / "b.txt"

    bridge = FakeAskBridge({"approve": "yes"})
    ctx = _ctx(tmp_path, ask_bridge=bridge)

    res = _run(
        F.dispatch_organize_files(
            {"moves": [{"src": str(a), "dst": str(da)}, {"src": str(b), "dst": str(db)}]},
            ctx,
        )
    )
    assert res["status"] == "completed"
    assert res["moved"] == 2
    assert da.exists() and db.exists()
    assert not a.exists() and not b.exists()
    # Exactly one approval ask for the whole batch.
    assert len(bridge.questions) == 1


def test_organize_files_declined_moves_nothing(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    a.write_text("AA")
    da = tmp_path / "out" / "a.txt"

    bridge = FakeAskBridge({"approve": "no"})
    ctx = _ctx(tmp_path, ask_bridge=bridge)

    res = _run(
        F.dispatch_organize_files({"moves": [{"src": str(a), "dst": str(da)}]}, ctx)
    )
    assert res["status"] == "declined"
    assert a.exists()
    assert not da.exists()


# ── _safe_path refusals ──────────────────────────────────────────────────────


def test_safe_path_refuses_system_path() -> None:
    with pytest.raises(ToolError) as ei:
        F._safe_path("/etc/passwd")
    assert ei.value.code == "E_DENIED"

    with pytest.raises(ToolError):
        F._safe_path("/usr/bin/python3")

    with pytest.raises(ToolError):
        F._safe_path("/System/Library/foo")


def test_safe_path_refuses_secret_paths() -> None:
    # ~/.ssh/id_rsa: both the .ssh dir AND the id_rsa name should refuse it.
    with pytest.raises(ToolError) as ei:
        F._safe_path("~/.ssh/id_rsa")
    assert ei.value.code == "E_DENIED"

    # config.json under ~/.gemia (config/secrets dir).
    with pytest.raises(ToolError):
        F._safe_path("~/.gemia/config.json")

    # secret-looking name anywhere, even in tmp.
    with pytest.raises(ToolError):
        F._safe_path("/tmp/my_api_key.txt")

    with pytest.raises(ToolError):
        F._safe_path("/tmp/service_credentials.json")


def test_safe_path_refuses_git_internals(tmp_path: Path) -> None:
    with pytest.raises(ToolError):
        F._safe_path(str(tmp_path / ".git" / "config"))


def test_safe_path_refuses_empty() -> None:
    with pytest.raises(ToolError) as ei:
        F._safe_path("")
    assert ei.value.code == "E_BAD_ARG"


def test_safe_path_allows_normal_tmp_path(tmp_path: Path) -> None:
    # A plain file in tmp_path must pass.
    p = F._safe_path(str(tmp_path / "regular.txt"))
    assert p == (tmp_path / "regular.txt").resolve()


# ── registration ─────────────────────────────────────────────────────────────


def test_all_file_tools_registered() -> None:
    for name in ("read_file", "write_file", "copy_in", "list_dir", "move_file", "organize_files"):
        assert name in T.TOOL_NAMES, f"{name} missing from TOOL_NAMES"
        assert name in T.DISPATCHER, f"{name} missing from DISPATCHER"
        assert T.DISPATCHER[name].__name__ != f"stub_{name}", f"{name} is still a stub"
        schemas = [t for t in T.TOOL_SCHEMAS if t["function"]["name"] == name]
        assert len(schemas) == 1, f"{name} schema missing or duplicated"
        # required args present in the schema's parameters
        params = schemas[0]["function"]["parameters"]
        assert "properties" in params and "required" in params
