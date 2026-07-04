"""First-class file tools for the v3 agent loop.

Policy:
- Inside the session workspace: full read/write/copy/move/delete.
- Outside the workspace: read allowed except credential paths; writes are limited
  to explicit create/copy/move targets under approved create roots and do not
  overwrite existing files.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from gemia.sandbox_v4 import DEFAULT_CREDENTIAL_DENY, DEFAULT_OUTSIDE_CREATE_ROOTS
from gemia.tools._context import ToolContext

_MAX_READ_BYTES = 512_000
_MAX_WRITE_BYTES = 512_000


def _resolve(path: str | Path, ctx: ToolContext) -> Path:
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("path must be non-empty")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = Path(ctx.output_dir) / p
    return p.resolve()


def _workspace(ctx: ToolContext) -> Path:
    return Path(ctx.output_dir).resolve()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _is_workspace(path: Path, ctx: ToolContext) -> bool:
    return _is_within(path, _workspace(ctx))


def _credential_paths() -> list[Path]:
    return [Path(p).expanduser().resolve() for p in DEFAULT_CREDENTIAL_DENY]


def _outside_roots() -> list[Path]:
    return [Path(p).expanduser().resolve() for p in DEFAULT_OUTSIDE_CREATE_ROOTS]


def _is_credential_path(path: Path) -> bool:
    return any(_is_within(path, cred) or path == cred for cred in _credential_paths())


def _is_allowed_outside_target(path: Path) -> bool:
    return any(_is_within(path, root) or path == root for root in _outside_roots())


def _ensure_readable(path: Path, ctx: ToolContext) -> None:
    if _is_credential_path(path):
        raise PermissionError(f"credential path is not readable: {path}")
    if not path.exists():
        raise FileNotFoundError(f"file does not exist: {path}")
    if not path.is_file():
        raise IsADirectoryError(f"expected a file: {path}")


def _ensure_movable_source(path: Path, ctx: ToolContext) -> None:
    _ensure_readable(path, ctx)
    if _is_workspace(path, ctx) or _is_allowed_outside_target(path):
        return
    roots = ", ".join(str(root) for root in _outside_roots())
    raise PermissionError(f"outside move source must be inside workspace or an approved outside root ({roots}): {path}")


def _ensure_write_target(path: Path, ctx: ToolContext, *, overwrite: bool) -> None:
    if _is_credential_path(path):
        raise PermissionError(f"credential path is not writable: {path}")
    if _is_workspace(path, ctx):
        if path.exists() and path.is_dir():
            raise IsADirectoryError(f"target is a directory: {path}")
        if path.exists() and not overwrite:
            raise FileExistsError(f"target exists; pass overwrite=true to replace inside workspace: {path}")
        return
    if not _is_allowed_outside_target(path):
        roots = ", ".join(str(root) for root in _outside_roots())
        raise PermissionError(f"outside target must be under an approved create root ({roots}): {path}")
    if path.exists():
        raise FileExistsError(f"outside target exists; refusing to overwrite: {path}")


def _rel(path: Path, ctx: ToolContext) -> str:
    try:
        return str(path.relative_to(_workspace(ctx)))
    except ValueError:
        return str(path)


def _payload(path: Path, ctx: ToolContext) -> dict[str, Any]:
    exists = path.exists()
    return {
        "path": str(path),
        "workspace_relative_path": _rel(path, ctx) if _is_workspace(path, ctx) else "",
        "inside_workspace": _is_workspace(path, ctx),
        "exists": exists,
        "size_bytes": path.stat().st_size if exists and path.is_file() else None,
    }


async def dispatch_list(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    root = _resolve(args.get("path") or ".", ctx)
    if _is_credential_path(root):
        raise PermissionError(f"credential path is not listable: {root}")
    if not root.exists():
        raise FileNotFoundError(f"directory does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"expected a directory: {root}")
    max_entries = int(args.get("max_entries") or 100)
    max_entries = max(1, min(max_entries, 500))
    entries = []
    for child in sorted(root.iterdir(), key=lambda p: p.name)[:max_entries]:
        if _is_credential_path(child):
            continue
        entries.append(
            {
                "name": child.name,
                "path": str(child),
                "workspace_relative_path": _rel(child, ctx) if _is_workspace(child, ctx) else "",
                "kind": "dir" if child.is_dir() else "file",
                "size_bytes": child.stat().st_size if child.is_file() else None,
            }
        )
    return {"directory": str(root), "inside_workspace": _is_workspace(root, ctx), "entries": entries}


async def dispatch_read(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    path = _resolve(args.get("path"), ctx)
    _ensure_readable(path, ctx)
    max_bytes = int(args.get("max_bytes") or _MAX_READ_BYTES)
    max_bytes = max(1, min(max_bytes, 2_000_000))
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"file is {size} bytes, above max_bytes={max_bytes}")
    return {**_payload(path, ctx), "content": path.read_text(encoding="utf-8")}


async def dispatch_write(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    path = _resolve(args.get("path"), ctx)
    content = args.get("content", "")
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    if len(content.encode("utf-8")) > _MAX_WRITE_BYTES:
        raise ValueError(f"content exceeds {_MAX_WRITE_BYTES} bytes")
    overwrite = bool(args.get("overwrite", False))
    _ensure_write_target(path, ctx, overwrite=overwrite)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"status": "written", "file": _payload(path, ctx)}


async def dispatch_copy(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    source = _resolve(args.get("source"), ctx)
    dest = _resolve(args.get("dest"), ctx)
    _ensure_readable(source, ctx)
    overwrite = bool(args.get("overwrite", False))
    _ensure_write_target(dest, ctx, overwrite=overwrite)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return {"status": "copied", "source": _payload(source, ctx), "dest": _payload(dest, ctx)}


async def dispatch_move(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    source = _resolve(args.get("source"), ctx)
    dest = _resolve(args.get("dest"), ctx)
    _ensure_movable_source(source, ctx)
    overwrite = bool(args.get("overwrite", False))
    _ensure_write_target(dest, ctx, overwrite=overwrite)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(dest))
    return {"status": "moved", "source_path": str(source), "dest": _payload(dest, ctx)}


async def dispatch_delete(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    path = _resolve(args.get("path"), ctx)
    if not _is_workspace(path, ctx):
        raise PermissionError("file_delete is limited to the workspace")
    _ensure_readable(path, ctx)
    path.unlink()
    return {"status": "deleted", "path": str(path), "workspace_relative_path": _rel(path, ctx)}


__all__ = [
    "dispatch_list",
    "dispatch_read",
    "dispatch_write",
    "dispatch_copy",
    "dispatch_move",
    "dispatch_delete",
]
