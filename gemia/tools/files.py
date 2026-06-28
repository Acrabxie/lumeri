"""File-management verbs: work with files OUTSIDE the session workspace.

These are *host-side* tools (like ``fetch`` / ``web_search``): they reach files
on the user's machine rather than the network-denied sandbox. Because they touch
real user files, every write/move/copy target — and every source — is run through
:func:`_safe_path`, which:

  - resolves symlinks (so a symlink to ``/etc`` cannot smuggle a write through),
  - refuses operations on system directories (``/``, ``/System``, ``/usr``,
    ``/bin``, ``/sbin``, ``/etc``, ``/var``, top-level ``/Library``,
    ``/private/var``),
  - refuses sensitive credential locations (``~/.ssh``, ``~/.gnupg``,
    ``~/.config/gcloud``, ``~/.gemia``) and any path whose name matches
    credential / secret / api_key / token / private_key / id_rsa,
  - refuses ``.git`` internals,
  - and enforces a byte cap.

Verbs:

    - ``read_file``   — read a host text file (binary → note + size, not bytes).
    - ``write_file``  — write/overwrite/append a host file (ALLOWED, no approval).
    - ``copy_in``     — copy an external file INTO ``ctx.output_dir`` so the agent
                        can edit it safely in the workspace.
    - ``list_dir``    — list a directory (read-only).
    - ``move_file``   — MOVE/RENAME a host file. REQUIRES EXPLICIT USER APPROVAL
                        via the AskBridge (``ctx.extra["ask_bridge"]``) first.
    - ``organize_files`` — batch move: ONE approval listing all moves, then
                        execute the approved ones.

Dispatcher signature: ``async def dispatch(args, ctx) -> dict``. Dispatchers must
NOT swallow errors; the agent loop turns a raised :class:`ToolError` into a
``tool_exec_error`` event and surfaces the recovery hint to the model.
"""
from __future__ import annotations

import asyncio
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gemia.tools._context import ToolContext
from gemia.errors import (
    RECOVERY_FIX_ARGS,
    RECOVERY_NONE,
    RECOVERY_SWITCH_TOOL,
    ToolError,
)

# ── limits ──────────────────────────────────────────────────────────────────

DEFAULT_MAX_BYTES = 2_000_000          # read_file default cap (2 MB)
HARD_MAX_BYTES = 50 * 1024 * 1024      # absolute ceiling for any single op (50 MB)
DEFAULT_MAX_ENTRIES = 500              # list_dir default cap

# ── safety policy ───────────────────────────────────────────────────────────

# Top-level system directories we never operate on (after symlink resolution).
# Each entry matches the directory itself or anything beneath it.
_SYSTEM_PREFIXES: tuple[str, ...] = (
    "/System",
    "/usr",
    "/bin",
    "/sbin",
    "/etc",
    "/var",
    "/Library",        # top-level /Library (user's ~/Library is fine)
    "/private/var",
    "/private/etc",
)

# Sensitive locations under the user's home (credentials / secrets / config).
_SENSITIVE_HOME_DIRS: tuple[str, ...] = (
    ".ssh",
    ".gnupg",
    ".config/gcloud",
    ".gemia",           # gemia config / secrets
    ".aws",
    ".kube",
)

# A filename matching any of these tokens is treated as a secret and refused.
_SECRET_NAME_RE = re.compile(
    r"(credential|secret|api[_-]?key|token|private[_-]?key|id_rsa|id_ed25519|\.pem$|\.key$)",
    re.IGNORECASE,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _refuse(path_repr: str, why: str) -> ToolError:
    return ToolError(
        f"Refused for safety: {why} — {path_repr}",
        code="E_DENIED",
        recovery=RECOVERY_NONE,
        hint=(
            "This path is a protected system location, a credential/secret file, "
            "or a .git internal. Choose a different, non-sensitive path."
        ),
    )


def _safe_path(raw: str, *, for_read: bool = False) -> Path:
    """Resolve ``raw`` and refuse protected / sensitive targets.

    Applied to ALL write/move/copy targets *and* sources. Resolves symlinks so a
    link cannot smuggle access to a protected location. ``for_read=True`` is
    slightly more permissive about *where* a path lives (reads outside the home
    tree are allowed) but still refuses secret-looking files and credential dirs.

    Raises :class:`ToolError` (code ``E_DENIED``) on refusal — never operates.
    """
    if raw is None or str(raw).strip() == "":
        raise ToolError(
            "path is required and cannot be empty.",
            code="E_BAD_ARG",
            recovery=RECOVERY_FIX_ARGS,
            hint="Pass a non-empty filesystem path.",
        )

    text = str(raw).strip()
    expanded = Path(text).expanduser()

    # Resolve symlinks where possible. ``strict=False`` lets us validate a
    # not-yet-existing write target while still resolving any existing parent
    # components (so a symlinked parent dir is caught).
    try:
        resolved = expanded.resolve(strict=False)
    except (OSError, RuntimeError) as exc:  # RuntimeError: symlink loop
        raise ToolError(
            f"Could not resolve path: {text} ({exc})",
            code="E_BAD_ARG",
            recovery=RECOVERY_FIX_ARGS,
        ) from exc

    resolved_str = str(resolved)
    home = Path.home().resolve()

    # 1. Refuse the filesystem root itself.
    if resolved == Path(resolved.anchor) or resolved_str == "/":
        raise _refuse(resolved_str, "filesystem root")

    # 2. Refuse system directory prefixes (the dir itself or anything under it).
    #    Note: /var on macOS is a symlink to /private/var, which .resolve()
    #    already expands — both forms are covered.
    for prefix in _SYSTEM_PREFIXES:
        if resolved_str == prefix or resolved_str.startswith(prefix + "/"):
            raise _refuse(resolved_str, f"protected system path ({prefix})")

    # 3. Refuse sensitive credential/config directories under home.
    for rel in _SENSITIVE_HOME_DIRS:
        sensitive = (home / rel)
        sensitive_str = str(sensitive)
        if resolved_str == sensitive_str or resolved_str.startswith(sensitive_str + "/"):
            raise _refuse(resolved_str, f"sensitive credential/config location (~/{rel})")

    # 4. Refuse .git internals anywhere in the path.
    parts = resolved.parts
    if ".git" in parts:
        raise _refuse(resolved_str, "git internal (.git)")

    # 5. Refuse secret-looking filenames (any component, not just the basename —
    #    a directory named e.g. 'secrets' or a file 'config.api_key' both match).
    for component in parts:
        if _SECRET_NAME_RE.search(component):
            raise _refuse(resolved_str, "credential/secret file name")

    return resolved


def _looks_binary(data: bytes) -> bool:
    """Heuristic: a NUL byte or a high ratio of non-text bytes => binary."""
    if b"\x00" in data:
        return True
    sample = data[:8192]
    if not sample:
        return False
    # Printable ASCII + common whitespace are "text"; count the rest.
    text_bytes = bytes(range(0x20, 0x7F)) + b"\n\r\t\f\b"
    nontext = sum(1 for b in sample if b not in text_bytes)
    return (nontext / len(sample)) > 0.30


# ── read_file ───────────────────────────────────────────────────────────────


async def dispatch_read_file(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Read a host text file.

    Returns ``{path, text, truncated, size}``. Binary files return a note in
    ``text`` plus ``binary: True`` rather than raw bytes.
    """
    del ctx  # read_file does not touch the workspace
    path = _safe_path(args.get("path"), for_read=True)

    if not path.exists():
        raise ToolError(
            f"No such file: {path}",
            code="E_NOT_FOUND",
            recovery=RECOVERY_FIX_ARGS,
            hint="Check the path; use list_dir to discover files.",
        )
    if path.is_dir():
        raise ToolError(
            f"Path is a directory, not a file: {path}",
            code="E_BAD_ARG",
            recovery=RECOVERY_SWITCH_TOOL,
            hint="Use list_dir for directories.",
        )

    try:
        max_bytes = int(args.get("max_bytes") or DEFAULT_MAX_BYTES)
    except (TypeError, ValueError):
        max_bytes = DEFAULT_MAX_BYTES
    max_bytes = max(1, min(max_bytes, HARD_MAX_BYTES))

    def _read_blocking() -> tuple[bytes, int]:
        full_size = path.stat().st_size
        with open(path, "rb") as fh:
            chunk = fh.read(max_bytes + 1)  # +1 to detect truncation
        return chunk, full_size

    chunk, full_size = await asyncio.to_thread(_read_blocking)
    truncated = len(chunk) > max_bytes
    chunk = chunk[:max_bytes]

    if _looks_binary(chunk):
        return {
            "path": str(path),
            "text": f"<binary file: {full_size} bytes, not shown as text>",
            "truncated": truncated,
            "size": full_size,
            "binary": True,
        }

    text = chunk.decode("utf-8", errors="replace")
    return {
        "path": str(path),
        "text": text,
        "truncated": truncated,
        "size": full_size,
        "binary": False,
    }


# ── write_file (no approval) ─────────────────────────────────────────────────


async def dispatch_write_file(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Write / overwrite / append a host file. Creates parent dirs.

    Allowed WITHOUT approval. Returns ``{path, bytes_written}``.
    """
    del ctx
    path = _safe_path(args.get("path"), for_read=False)

    content = args.get("content")
    if content is None:
        raise ToolError(
            "content is required.",
            code="E_BAD_ARG",
            recovery=RECOVERY_FIX_ARGS,
            hint="Pass the text to write.",
        )
    data = str(content).encode("utf-8")
    if len(data) > HARD_MAX_BYTES:
        raise ToolError(
            f"content ({len(data)} bytes) exceeds the {HARD_MAX_BYTES} byte cap.",
            code="E_BUDGET",
            recovery=RECOVERY_SWITCH_TOOL,
            hint="Write less data per call.",
        )

    append = bool(args.get("append", False))

    if path.is_dir():
        raise ToolError(
            f"Target is an existing directory: {path}",
            code="E_BAD_ARG",
            recovery=RECOVERY_FIX_ARGS,
        )

    def _write_blocking() -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "ab" if append else "wb"
        with open(path, mode) as fh:
            return fh.write(data)

    try:
        written = await asyncio.to_thread(_write_blocking)
    except OSError as exc:
        raise ToolError(
            f"Failed to write {path}: {exc}",
            code="E_BAD_ARG",
            recovery=RECOVERY_FIX_ARGS,
            hint="Check that the parent directory is writable.",
        ) from exc

    return {"path": str(path), "bytes_written": written, "append": append}


# ── copy_in ──────────────────────────────────────────────────────────────────


async def dispatch_copy_in(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Copy an external file INTO the session workspace (``ctx.output_dir``).

    Lets the agent edit a host file safely without touching the original.
    Returns ``{workspace_path, name, size}``.
    """
    src = _safe_path(args.get("path"), for_read=True)

    if not src.exists():
        raise ToolError(
            f"No such file to copy: {src}",
            code="E_NOT_FOUND",
            recovery=RECOVERY_FIX_ARGS,
        )
    if src.is_dir():
        raise ToolError(
            f"copy_in copies a single file, not a directory: {src}",
            code="E_BAD_ARG",
            recovery=RECOVERY_FIX_ARGS,
        )

    size = src.stat().st_size
    if size > HARD_MAX_BYTES:
        raise ToolError(
            f"File ({size} bytes) exceeds the {HARD_MAX_BYTES} byte copy cap.",
            code="E_BUDGET",
            recovery=RECOVERY_SWITCH_TOOL,
        )

    # Determine the destination name inside the workspace (basename only — never
    # let as_name carry a path component out of the workspace).
    as_name = args.get("as_name")
    name = str(as_name).strip() if as_name else src.name
    name = Path(name).name  # strip any directory parts / traversal
    if not name or name in (".", ".."):
        name = src.name

    output_dir = Path(ctx.output_dir)
    dest = (output_dir / name).resolve()

    # Containment: the destination must stay inside the workspace.
    if not str(dest).startswith(str(output_dir.resolve()) + "/") and dest != output_dir.resolve():
        raise ToolError(
            f"copy_in destination escapes the workspace: {dest}",
            code="E_BAD_ARG",
            recovery=RECOVERY_FIX_ARGS,
        )

    def _copy_blocking() -> int:
        output_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return dest.stat().st_size

    try:
        written = await asyncio.to_thread(_copy_blocking)
    except OSError as exc:
        raise ToolError(
            f"Failed to copy {src} -> {dest}: {exc}",
            code="E_BAD_ARG",
            recovery=RECOVERY_FIX_ARGS,
        ) from exc

    return {
        "workspace_path": str(dest),
        "name": name,
        "size": written,
    }


# ── list_dir (read-only) ─────────────────────────────────────────────────────


async def dispatch_list_dir(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """List a directory. Returns ``{path, entries:[{name, is_dir, size}], truncated}``."""
    del ctx
    path = _safe_path(args.get("path"), for_read=True)

    if not path.exists():
        raise ToolError(
            f"No such directory: {path}",
            code="E_NOT_FOUND",
            recovery=RECOVERY_FIX_ARGS,
        )
    if not path.is_dir():
        raise ToolError(
            f"Path is not a directory: {path}",
            code="E_BAD_ARG",
            recovery=RECOVERY_SWITCH_TOOL,
            hint="Use read_file for files.",
        )

    try:
        max_entries = int(args.get("max_entries") or DEFAULT_MAX_ENTRIES)
    except (TypeError, ValueError):
        max_entries = DEFAULT_MAX_ENTRIES
    max_entries = max(1, max_entries)

    def _list_blocking() -> tuple[list[dict[str, Any]], bool]:
        names = sorted(p.name for p in path.iterdir())
        truncated = len(names) > max_entries
        out: list[dict[str, Any]] = []
        for name in names[:max_entries]:
            child = path / name
            try:
                is_dir = child.is_dir()
                size = (child.stat().st_size if not is_dir else 0)
            except OSError:
                is_dir = False
                size = 0
            out.append({"name": name, "is_dir": is_dir, "size": size})
        return out, truncated

    entries, truncated = await asyncio.to_thread(_list_blocking)
    return {"path": str(path), "entries": entries, "truncated": truncated}


# ── move_file (REQUIRES APPROVAL) ────────────────────────────────────────────


def _build_move_question(qid: str, title: str, description: str) -> dict[str, Any]:
    """Build a yes/no approval ask_question dict (matches gemia.tools.ask)."""
    from gemia.tools.ask import AskQuestion, SelectControl

    question = AskQuestion(
        question_id=qid,
        title=title,
        description=description,
        controls={
            "approve": SelectControl(
                options=[
                    {"label": "Yes, move it", "value": "yes"},
                    {"label": "No, cancel", "value": "no"},
                ],
                default="no",
            )
        },
        metadata={"emitted_at": _now(), "kind": "move_approval"},
    )
    return question.to_dict()


def _answer_is_yes(raw: Any) -> bool:
    """Interpret an AskBridge answer dict as an explicit yes.

    Only an explicit affirmative approves the move. ``None`` (timeout / no
    bridge), missing key, or anything other than 'yes'/'approve'/'true'/'y'
    declines — safe by default.
    """
    if not isinstance(raw, dict):
        return False
    val = raw.get("approve")
    if val is None:
        # tolerate a bare {"value": ...} or {"answer": ...} shape too
        val = raw.get("value", raw.get("answer"))
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in {"yes", "y", "approve", "approved", "true", "ok"}


async def _request_approval(
    ctx: ToolContext,
    *,
    title: str,
    description: str,
    timeout: Any = None,
) -> tuple[bool, str]:
    """Emit an approval ask via the AskBridge and await the answer.

    Returns ``(approved, reason)``. Safe by default: with no bridge wired in, or
    on timeout, returns ``(False, ...)`` so nothing is moved.
    """
    bridge = (getattr(ctx, "extra", None) or {}).get("ask_bridge")
    if bridge is None:
        return False, "no approval bridge available (cannot get user consent)"

    qid = f"move_{uuid.uuid4().hex[:12]}"
    question = _build_move_question(qid, title, description)
    raw = await bridge.emit_and_wait(
        question,
        timeout=float(timeout) if timeout is not None else None,
    )
    if raw is None:
        return False, "no answer received (timed out); not moving"
    return _answer_is_yes(raw), "user responded"


def _validated_move_pair(src_raw: Any, dst_raw: Any) -> tuple[Path, Path]:
    """Resolve + safety-check a (src, dst) move pair. Raises on refusal."""
    src = _safe_path(src_raw, for_read=False)
    dst = _safe_path(dst_raw, for_read=False)
    if not src.exists():
        raise ToolError(
            f"No such file to move: {src}",
            code="E_NOT_FOUND",
            recovery=RECOVERY_FIX_ARGS,
        )
    return src, dst


def _do_move(src: Path, dst: Path) -> str:
    """Move src -> dst, creating parent dirs. Returns the final destination path."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    final = shutil.move(str(src), str(dst))
    return str(final)


async def dispatch_move_file(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """MOVE/RENAME a host file. Requires EXPLICIT user approval first.

    Emits an approval ask (``move <src> -> <dst>?``) via the AskBridge and awaits
    a yes/no. On approval, performs the move and returns
    ``{status:"moved", src, dst}``. Otherwise returns ``{status:"declined", ...}``
    and DOES NOT move.
    """
    src, dst = _validated_move_pair(args.get("src"), args.get("dst"))

    approved, reason = await _request_approval(
        ctx,
        title="Approve file move?",
        description=f"move {src} -> {dst}?",
        timeout=args.get("timeout"),
    )
    if not approved:
        return {"status": "declined", "src": str(src), "dst": str(dst), "reason": reason}

    try:
        final = await asyncio.to_thread(_do_move, src, dst)
    except OSError as exc:
        raise ToolError(
            f"Failed to move {src} -> {dst}: {exc}",
            code="E_BAD_ARG",
            recovery=RECOVERY_FIX_ARGS,
        ) from exc

    return {"status": "moved", "src": str(src), "dst": str(final)}


# ── organize_files (batch move, ONE approval) ────────────────────────────────


async def dispatch_organize_files(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Batch MOVE: one approval listing all moves, then execute approved ones.

    ``moves`` is a list of ``{src, dst}``. Every src and dst is safety-checked
    BEFORE the approval ask (a refusal aborts the whole batch). On approval,
    executes each move; returns ``{status, results:[{src, dst, moved|error}]}``.
    On decline, returns ``{status:"declined"}`` and moves nothing.
    """
    moves = args.get("moves")
    if not isinstance(moves, list) or not moves:
        raise ToolError(
            "moves must be a non-empty list of {src, dst} objects.",
            code="E_BAD_ARG",
            recovery=RECOVERY_FIX_ARGS,
        )

    # Validate every pair first — a single refusal aborts before any approval.
    pairs: list[tuple[Path, Path]] = []
    for i, m in enumerate(moves):
        if not isinstance(m, dict):
            raise ToolError(
                f"moves[{i}] must be an object with src and dst.",
                code="E_BAD_ARG",
                recovery=RECOVERY_FIX_ARGS,
            )
        pairs.append(_validated_move_pair(m.get("src"), m.get("dst")))

    listing = "\n".join(f"  {src} -> {dst}" for src, dst in pairs)
    approved, reason = await _request_approval(
        ctx,
        title=f"Approve {len(pairs)} file move(s)?",
        description=f"Move these files?\n{listing}",
        timeout=args.get("timeout"),
    )
    if not approved:
        return {
            "status": "declined",
            "reason": reason,
            "moves": [{"src": str(s), "dst": str(d)} for s, d in pairs],
        }

    results: list[dict[str, Any]] = []
    moved_count = 0
    for src, dst in pairs:
        try:
            final = await asyncio.to_thread(_do_move, src, dst)
            results.append({"src": str(src), "dst": str(final), "moved": True})
            moved_count += 1
        except OSError as exc:
            results.append({"src": str(src), "dst": str(dst), "moved": False, "error": str(exc)})

    return {"status": "completed", "moved": moved_count, "results": results}


__all__ = [
    "dispatch_read_file",
    "dispatch_write_file",
    "dispatch_copy_in",
    "dispatch_list_dir",
    "dispatch_move_file",
    "dispatch_organize_files",
    "_safe_path",
]
