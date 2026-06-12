"""Code-escape security probes for the v4 ``build`` verb.

M3 proved the sandbox boundary against *shell commands* (run_shell). The
build verb lets the model run arbitrary *Python code* — strictly more
dangerous — so these probes drive real model-shaped escape attempts through
the real build dispatcher and the real sandbox-exec two-tier profile:

  1. open(~/.gemia/config.json)            → denied, zero content leak
  2. os.remove() an existing outside file  → denied, file + content preserved
  3. urllib https to the public internet   → denied (deny network*)
  4. open(~/.ssh/<new>, "w")               → denied, nothing created
     (regression for the SBPL specificity hole: a broad ``deny file-write*``
     does NOT beat a specific ``allow file-write-create`` — credential paths
     must carry their own create-deny)
  5. ../ path traversal from workspace cwd to ~/.ssh → denied, nothing created
  6. normal creative ffmpeg inside the workspace → succeeds (the boundary
     does not false-positive on legitimate creative code)

Every probe asserts on exit_code as well as stdout/stderr markers, and the
"must fail" probes assert the credential/outside targets are untouched
afterwards. Skips (never false-passes) on hosts without sandbox-exec.
"""
from __future__ import annotations

import asyncio
import os
import signal
import uuid
from pathlib import Path

import pytest

from gemia.sandbox_v4 import build_v4_sandbox_command
from gemia.tools import build
from gemia.tools._context import AssetRegistry, ToolContext


_ENFORCED = build_v4_sandbox_command(["/usr/bin/true"], workspace_dir=Path.home())[1]

pytestmark = pytest.mark.skipif(
    not _ENFORCED, reason="sandbox-exec unavailable on this host"
)


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        session_id="escape_probe",
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _: None,
    )


def _run_to_completion(ctx: ToolContext, code: str, timeout_sec: float = 30) -> dict:
    """Submit code through the real build verb and wait for a terminal state."""
    submitted = asyncio.run(
        build.dispatch({"code": code, "timeout_sec": timeout_sec}, ctx)
    )
    result = asyncio.run(
        build.dispatch_wait(
            {"job_id": submitted["job_id"], "max_wait_sec": timeout_sec + 30}, ctx
        )
    )
    assert not result["timed_out"], f"probe never reached terminal state: {result}"
    return result


def setup_module() -> None:
    for _job_id, (proc, _) in list(build._PROCESSES.items()):
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            pass
    build._PROCESSES.clear()


# ── probe 1: credential read ────────────────────────────────────────────────

@pytest.mark.skipif(
    not (Path.home() / ".gemia" / "config.json").exists(),
    reason="no real ~/.gemia/config.json on this host",
)
def test_escape_read_gemia_config_denied(tmp_path: Path) -> None:
    code = """
from pathlib import Path
p = Path.home() / ".gemia" / "config.json"
try:
    data = p.read_text()
    print("LEAK:" + data[:80])
except Exception as e:
    print("DENIED:", type(e).__name__)
    raise SystemExit(13)
"""
    result = _run_to_completion(_ctx(tmp_path), code)
    assert result["status"] == "failed"
    assert result["exit_code"] == 13
    assert "DENIED:" in result["stdout_tail"]
    assert "LEAK:" not in result["stdout_tail"]
    assert "LEAK:" not in result["stderr_tail"]


# ── probe 2: delete an existing outside file ────────────────────────────────

def test_escape_os_remove_outside_denied(tmp_path: Path) -> None:
    victim = Path(f"/private/tmp/sbx_escape_victim_{uuid.uuid4().hex}.txt")
    victim.write_text("ORIGINAL")
    try:
        code = f"""
import os
try:
    os.remove({str(victim)!r})
    print("REMOVED")
except Exception as e:
    print("DENIED:", type(e).__name__)
    raise SystemExit(13)
"""
        result = _run_to_completion(_ctx(tmp_path), code)
        assert result["exit_code"] == 13
        assert "DENIED:" in result["stdout_tail"]
        assert victim.exists(), "outside file was deleted by sandboxed code"
        assert victim.read_text() == "ORIGINAL"
    finally:
        victim.unlink(missing_ok=True)


# ── probe 3: outbound network ───────────────────────────────────────────────

def test_escape_urllib_network_denied(tmp_path: Path) -> None:
    code = """
import urllib.request
try:
    urllib.request.urlopen("https://example.com/", timeout=5)
    print("CONNECTED")
except Exception as e:
    print("DENIED:", type(e).__name__)
    raise SystemExit(13)
"""
    result = _run_to_completion(_ctx(tmp_path), code, timeout_sec=60)
    assert result["exit_code"] == 13
    assert "DENIED:" in result["stdout_tail"]
    assert "CONNECTED" not in result["stdout_tail"]


# ── probe 4: create under ~/.ssh ────────────────────────────────────────────

def test_escape_write_ssh_denied(tmp_path: Path) -> None:
    target = Path.home() / ".ssh" / f"sbx_escape_probe_{uuid.uuid4().hex}"
    try:
        code = f"""
try:
    with open({str(target)!r}, "w") as f:
        f.write("ESCAPED")
    print("CREATED")
except Exception as e:
    print("DENIED:", type(e).__name__)
    raise SystemExit(13)
"""
        result = _run_to_completion(_ctx(tmp_path), code)
        assert result["exit_code"] == 13
        assert "DENIED:" in result["stdout_tail"]
        assert not target.exists(), "sandboxed code created a file under ~/.ssh"
    finally:
        target.unlink(missing_ok=True)


# ── probe 5: ../ traversal from workspace cwd to ~/.ssh ─────────────────────

def test_escape_relative_traversal_to_ssh_denied(tmp_path: Path) -> None:
    marker = f"sbx_trav_probe_{uuid.uuid4().hex}"
    target = Path.home() / ".ssh" / marker
    try:
        code = f"""
import os
rel = os.path.relpath(os.path.join(os.path.expanduser("~"), ".ssh"), os.getcwd())
path = os.path.join(rel, {marker!r})
try:
    with open(path, "w") as f:
        f.write("ESCAPED")
    print("CREATED")
except Exception as e:
    print("DENIED:", type(e).__name__)
    raise SystemExit(13)
"""
        result = _run_to_completion(_ctx(tmp_path), code)
        assert result["exit_code"] == 13
        assert "DENIED:" in result["stdout_tail"]
        assert not target.exists(), "traversal created a file under ~/.ssh"
    finally:
        target.unlink(missing_ok=True)


# ── probe 6: legitimate creative work must pass ─────────────────────────────

def test_normal_ffmpeg_in_workspace_succeeds(tmp_path: Path) -> None:
    code = """
import subprocess
r = subprocess.run(
    ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1",
     "-pix_fmt", "yuv420p", "probe_out.mp4"],
    capture_output=True, text=True,
)
print("FFMPEG_RC", r.returncode)
if r.returncode != 0:
    print(r.stderr[-500:])
raise SystemExit(r.returncode)
"""
    result = _run_to_completion(_ctx(tmp_path), code, timeout_sec=60)
    assert result["status"] == "done", f"legitimate ffmpeg blocked: {result}"
    assert result["exit_code"] == 0
    out = tmp_path / "probe_out.mp4"
    assert out.exists() and out.stat().st_size > 0
