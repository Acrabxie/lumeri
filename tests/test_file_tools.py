from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

import pytest

from gemia.budget_guard import BudgetGuard
from gemia.errors import ToolError
from gemia.tools import DISPATCHER
from gemia.tools._context import AssetRegistry, ToolContext
from gemia.tools import files as file_tools


def _ctx(workspace: Path) -> ToolContext:
    workspace.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        session_id="file_tools",
        output_dir=workspace,
        registry=AssetRegistry(),
        emit_progress=lambda _u: None,
    )


def _run(name: str, args: dict, ctx: ToolContext) -> dict:
    return asyncio.run(DISPATCHER[name](args, ctx))


def _schema_names() -> set[str]:
    from gemia.tools._schema import TOOL_SCHEMAS

    return {tool["function"]["name"] for tool in TOOL_SCHEMAS}


def test_file_tools_are_registered_and_budgeted() -> None:
    guard = BudgetGuard()
    for name in ("file_list", "file_read", "file_write", "file_copy", "file_move", "file_delete"):
        assert name in DISPATCHER
        assert not DISPATCHER[name].__name__.startswith("stub_")
        assert name in _schema_names()
        assert guard.estimate(name)[0] == 0.0


def test_legacy_file_tools_are_registered() -> None:
    for name in ("read_file", "write_file", "copy_in", "list_dir", "move_file", "organize_files"):
        assert name in DISPATCHER
        assert not DISPATCHER[name].__name__.startswith("stub_")
    assert hasattr(file_tools, "dispatch_read_file")


def test_workspace_has_full_file_permissions(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path / "ws")
    written = _run("file_write", {"path": "notes/a.txt", "content": "one"}, ctx)
    assert written["file"]["inside_workspace"] is True

    with pytest.raises(FileExistsError):
        _run("file_write", {"path": "notes/a.txt", "content": "blocked"}, ctx)

    _run("file_write", {"path": "notes/a.txt", "content": "two", "overwrite": True}, ctx)
    assert _run("file_read", {"path": "notes/a.txt"}, ctx)["content"] == "two"

    _run("file_copy", {"source": "notes/a.txt", "dest": "notes/b.txt"}, ctx)
    assert (ctx.output_dir / "notes" / "b.txt").read_text(encoding="utf-8") == "two"

    _run("file_move", {"source": "notes/b.txt", "dest": "notes/c.txt"}, ctx)
    assert not (ctx.output_dir / "notes" / "b.txt").exists()
    assert (ctx.output_dir / "notes" / "c.txt").exists()

    listed = _run("file_list", {"path": "notes"}, ctx)
    assert [entry["name"] for entry in listed["entries"]] == ["a.txt", "c.txt"]

    _run("file_delete", {"path": "notes/c.txt"}, ctx)
    assert not (ctx.output_dir / "notes" / "c.txt").exists()


def test_outside_workspace_can_add_copy_and_move_new_files(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path / "ws")
    outside = Path("/private/tmp") / f"lumeri_filetools_{uuid.uuid4().hex[:8]}"
    outside.mkdir(parents=True)
    try:
        added = outside / "added.txt"
        _run("file_write", {"path": str(added), "content": "outside"}, ctx)
        assert added.read_text(encoding="utf-8") == "outside"

        workspace_src = ctx.output_dir / "source.txt"
        workspace_src.write_text("copy me", encoding="utf-8")
        copied = outside / "copied.txt"
        _run("file_copy", {"source": "source.txt", "dest": str(copied)}, ctx)
        assert copied.read_text(encoding="utf-8") == "copy me"

        outside_src = outside / "outside-source.txt"
        outside_src.write_text("move me", encoding="utf-8")
        moved = outside / "moved.txt"
        _run("file_move", {"source": str(outside_src), "dest": str(moved)}, ctx)
        assert not outside_src.exists()
        assert moved.read_text(encoding="utf-8") == "move me"
    finally:
        shutil.rmtree(outside, ignore_errors=True)


def test_outside_existing_targets_are_not_overwritten(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path / "ws")
    outside = Path("/private/tmp") / f"lumeri_filetools_{uuid.uuid4().hex[:8]}"
    outside.mkdir(parents=True)
    try:
        victim = outside / "victim.txt"
        victim.write_text("original", encoding="utf-8")
        with pytest.raises(FileExistsError):
            _run("file_write", {"path": str(victim), "content": "changed", "overwrite": True}, ctx)
        assert victim.read_text(encoding="utf-8") == "original"

        (ctx.output_dir / "source.txt").write_text("source", encoding="utf-8")
        with pytest.raises(FileExistsError):
            _run("file_copy", {"source": "source.txt", "dest": str(victim), "overwrite": True}, ctx)
        assert victim.read_text(encoding="utf-8") == "original"

        with pytest.raises(PermissionError):
            _run("file_delete", {"path": str(victim)}, ctx)
        assert victim.exists()
    finally:
        shutil.rmtree(outside, ignore_errors=True)


def test_move_source_outside_approved_roots_is_blocked(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path / "ws")
    system_file = Path("/etc/hosts")
    if not system_file.exists():
        pytest.skip("/etc/hosts not present")
    with pytest.raises(PermissionError):
        _run("file_move", {"source": str(system_file), "dest": str(ctx.output_dir / "hosts")}, ctx)


def test_credential_paths_are_blocked(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path / "ws")
    with pytest.raises(PermissionError):
        _run("file_write", {"path": str(Path.home() / ".ssh" / f"probe_{uuid.uuid4().hex}"), "content": "x"}, ctx)

    with pytest.raises(PermissionError):
        _run("file_list", {"path": str(Path.home() / ".ssh")}, ctx)


def test_legacy_read_write_copy_in_and_move(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path / "ws")
    out = _run("write_file", {"path": "legacy.txt", "content": "hello"}, ctx)
    assert out["bytes_written"] == 5
    assert _run("read_file", {"path": "legacy.txt"}, ctx)["text"] == "hello"

    outside = Path("/private/tmp") / f"lumeri_filetools_{uuid.uuid4().hex[:8]}"
    outside.mkdir(parents=True)
    try:
        src = outside / "import.txt"
        src.write_text("copy-in", encoding="utf-8")
        copied = _run("copy_in", {"source": str(src)}, ctx)
        assert Path(copied["path"]).read_text(encoding="utf-8") == "copy-in"

        moved = _run("move_file", {"source": "legacy.txt", "dest": "legacy-moved.txt"}, ctx)
        assert Path(moved["dest"]["path"]).exists()
    finally:
        shutil.rmtree(outside, ignore_errors=True)


def test_copy_in_registers_workspace_media_without_self_copy(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path / "ws")
    logo = ctx.output_dir / "lumeri_logo.png"
    logo.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    copied = _run(
        "copy_in",
        {"path": "lumeri_logo.png", "as_name": "lumeri_logo.png"},
        ctx,
    )

    assert copied["copied"] is False
    assert copied["path"] == str(logo)
    assert copied["workspace_path"] == str(logo)
    assert copied["name"] == "lumeri_logo.png"
    assert copied["asset_id"] == "img_001"
    assert copied["kind"] == "image"
    assert ctx.registry.get("img_001").path == logo.resolve()


def test_legacy_read_missing_raises_tool_error(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path / "ws")
    with pytest.raises(ToolError) as ei:
        _run("read_file", {"path": "missing.txt"}, ctx)
    assert ei.value.code == "E_NOT_FOUND"
