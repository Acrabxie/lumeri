from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ALLOWED_IMPORT_ROOTS = {"lumerai", "json", "math", "re", "datetime", "pathlib", "typing"}
BLOCKED_IMPORT_ROOTS = {
    "builtins",
    "codecs",
    "ctypes",
    "importlib",
    "marshal",
    "multiprocessing",
    "os",
    "pickle",
    "requests",
    "shutil",
    "socket",
    "subprocess",
    "sys",
    "urllib",
}
BLOCKED_CALL_NAMES = {"eval", "exec", "__import__", "compile", "open", "getattr", "setattr", "delattr", "globals", "locals"}
BLOCKED_ATTRS = {
    "eval",
    "exec",
    "__import__",
    "compile",
    "open",
    "popen",
    "system",
    "spawn",
    "fork",
    "remove",
    "unlink",
    "rmdir",
    "rename",
    "replace",
    "write_text",
    "write_bytes",
    "read_text",
    "read_bytes",
}
BLOCKED_NAMES = {"__builtins__", "__loader__", "__spec__", "__package__", "__file__", "__cached__"}


class SandboxViolation(ValueError):
    pass


@dataclass
class SandboxResult:
    ok: bool
    returncode: int
    patches: list[dict[str, Any]]
    stdout: str
    stderr: str
    script_hash: str
    error: str | None = None


def validate_script(script: str) -> None:
    try:
        tree = ast.parse(script)
    except SyntaxError as exc:
        raise SandboxViolation(f"Syntax error: {exc.msg} at line {exc.lineno}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _validate_import(alias.name, node.lineno)
        elif isinstance(node, ast.ImportFrom):
            _validate_import(node.module or "", node.lineno)
        elif isinstance(node, ast.Call):
            _validate_call(node)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in BLOCKED_NAMES:
                raise SandboxViolation(f"Blocked name at line {node.lineno}: {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr in BLOCKED_ATTRS or node.attr.startswith("__"):
                raise SandboxViolation(f"Blocked attribute at line {node.lineno}: {node.attr}")


def execute_script(
    script: str,
    *,
    project_state: dict[str, Any] | None = None,
    output_dir: str | Path,
    project_root: str | Path,
    workspace_dir: str | Path | None = None,
    session_id: str,
    ai_model: str = "unknown",
    timeout_sec: int = 30,
    dry_run: bool = False,
) -> SandboxResult:
    script_hash = hashlib.sha256(script.encode("utf-8")).hexdigest()
    try:
        validate_script(script)
    except SandboxViolation as exc:
        return SandboxResult(
            ok=False,
            returncode=2,
            patches=[],
            stdout="",
            stderr=str(exc),
            script_hash=script_hash,
            error=str(exc),
        )
    if dry_run:
        return SandboxResult(ok=True, returncode=0, patches=[], stdout="", stderr="", script_hash=script_hash)

    project_root = Path(project_root).resolve()
    output_dir = Path(output_dir).resolve()
    workspace_root = project_root / "workspaces"
    workspace = Path(workspace_dir).resolve() if workspace_dir is not None else workspace_root / _safe_path_segment(session_id)
    try:
        workspace.relative_to(workspace_root.resolve())
    except ValueError as exc:
        raise ValueError(f"workspace_dir must stay under {workspace_root}") from exc
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    sandbox_root = Path(os.environ.get("LUMERAI_SANDBOX_TMP") or project_root / "temp" / "lumerai-sandbox")
    sandbox_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="run-", dir=str(sandbox_root)) as td:
        tmp = Path(td)
        script_path = tmp / "script.py"
        state_path = tmp / "project_state.json"
        script_path.write_text(script, encoding="utf-8")
        state_path.write_text(json.dumps(project_state or {}, ensure_ascii=False), encoding="utf-8")
        env = _child_env(
            project_root=project_root,
            output_dir=output_dir,
            project_state_path=state_path,
            session_id=session_id,
            ai_model=ai_model,
            script_hash=script_hash,
            script_path=script_path,
            workspace_dir=workspace,
        )
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "lumerai._sandbox_child", str(script_path)],
                cwd=str(project_root),
                env=env,
                text=True,
                capture_output=True,
                timeout=timeout_sec,
                preexec_fn=_resource_limiter(timeout_sec),
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxResult(
                ok=False,
                returncode=124,
                patches=[],
                stdout=exc.stdout or "",
                stderr=exc.stderr or f"Script timed out after {timeout_sec}s",
                script_hash=script_hash,
                error=f"Script timed out after {timeout_sec}s",
            )
    patches = _parse_patch_stdout(proc.stdout)
    ok = proc.returncode == 0
    return SandboxResult(
        ok=ok,
        returncode=proc.returncode,
        patches=patches,
        stdout=proc.stdout,
        stderr=proc.stderr,
        script_hash=script_hash,
        error=None if ok else _summarize_stderr(proc.stderr, proc.returncode),
    )


def _summarize_stderr(stderr: str, returncode: int) -> str:
    """Return a short model/user-facing error while keeping full stderr in logs."""
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith("^") or line.startswith("~"):
            continue
        if line.startswith("Traceback "):
            continue
        return line
    return f"Script exited with {returncode}"


def _validate_import(module: str, lineno: int) -> None:
    root = (module or "").split(".", 1)[0]
    if root in BLOCKED_IMPORT_ROOTS or root not in ALLOWED_IMPORT_ROOTS:
        raise SandboxViolation(f"Blocked import at line {lineno}: {module}")


def _validate_call(node: ast.Call) -> None:
    func = node.func
    if isinstance(func, ast.Name) and func.id in BLOCKED_CALL_NAMES:
        raise SandboxViolation(f"Blocked call at line {node.lineno}: {func.id}")
    if isinstance(func, ast.Attribute) and (func.attr in BLOCKED_ATTRS or func.attr.startswith("__")):
        raise SandboxViolation(f"Blocked call at line {node.lineno}: {func.attr}")


def _safe_path_segment(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(value or "session"))
    safe = safe.strip("_-")
    return safe[:64] or "session"


def _child_env(
    *,
    project_root: Path,
    output_dir: Path,
    project_state_path: Path,
    session_id: str,
    ai_model: str,
    script_hash: str,
    script_path: Path,
    workspace_dir: Path,
) -> dict[str, str]:
    package_root = Path(__file__).resolve().parent.parent
    python_path = os.pathsep.join(
        part for part in [str(project_root), str(package_root), os.environ.get("PYTHONPATH", "")] if part
    )
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": python_path,
        "LUMERAI_PROJECT_ROOT": str(project_root),
        "LUMERAI_OUTPUT_DIR": str(output_dir),
        "LUMERAI_PROJECT_STATE_PATH": str(project_state_path),
        "LUMERAI_SESSION_ID": session_id,
        "LUMERAI_AI_MODEL": ai_model,
        "LUMERAI_SCRIPT_HASH": script_hash,
        "LUMERAI_SCRIPT_PATH": str(script_path),
        "LUMERAI_WORKSPACE_DIR": str(workspace_dir),
    }
    if os.environ.get("DYLD_LIBRARY_PATH"):
        env["DYLD_LIBRARY_PATH"] = os.environ["DYLD_LIBRARY_PATH"]
    return env


def _resource_limiter(timeout_sec: int):
    if os.name != "posix":
        return None

    def limit() -> None:
        try:
            import resource

            cpu = max(int(timeout_sec), 1)
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
            if hasattr(resource, "RLIMIT_AS"):
                mem = 2 * 1024 * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        except Exception:
            pass

    return limit


def _parse_patch_stdout(stdout: str) -> list[dict[str, Any]]:
    patches: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("version") == 1 and isinstance(payload.get("ops"), list):
            patches.append(payload)
    return patches
