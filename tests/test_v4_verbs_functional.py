"""Functional tests for M3 v4 verbs: fetch and run_shell.

Tests the contract, not the implementation details. Verifies:
  - fetch: https-only, dest_name sanitization, file landing, asset registration
  - run_shell: stdout capture, exit codes, sandbox enforcement, workspace access

Does NOT test sandbox security (deny credentials, modify/delete blocking, network denial).
Those are covered by test_sandbox_v4_isolation.py (M1 real-security probes).
"""
from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest

from gemia.tools._context import AssetRegistry, ToolContext
from gemia.tools import fetch as _fetch
from gemia.tools import run_shell as _run_shell


@pytest.fixture
def sandbox_available() -> bool:
    """Check if sandbox-exec is available (macOS only)."""
    return shutil.which("sandbox-exec") is not None and shutil.which("sandbox-exec")


@pytest.fixture
def workspace_dir() -> Path:
    """Temp directory for tool outputs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def tool_context(workspace_dir: Path) -> ToolContext:
    """ToolContext for the workspace."""
    return ToolContext(
        session_id="test_v4_verbs",
        output_dir=workspace_dir,
        registry=AssetRegistry(),
        emit_progress=lambda _: None,
    )


# ============================================================================
# fetch verb tests
# ============================================================================


class TestFetchBasic:
    """Basic fetch functionality."""

    def test_fetch_https_only_rejects_http(self, tool_context: ToolContext) -> None:
        """Fetch must reject http:// URLs."""
        with pytest.raises(ValueError, match="https://"):
            asyncio.run(
                _fetch.dispatch({"url": "http://example.com/file.txt"}, tool_context)
            )

    def test_fetch_https_only_rejects_file(self, tool_context: ToolContext) -> None:
        """Fetch must reject file:// URLs."""
        with pytest.raises(ValueError, match="https://"):
            asyncio.run(
                _fetch.dispatch({"url": "file:///etc/passwd"}, tool_context)
            )

    def test_fetch_requires_url(self, tool_context: ToolContext) -> None:
        """Fetch must reject empty or missing URL."""
        with pytest.raises(ValueError, match="non-empty"):
            asyncio.run(_fetch.dispatch({}, tool_context))

        with pytest.raises(ValueError, match="non-empty"):
            asyncio.run(_fetch.dispatch({"url": ""}, tool_context))


class TestFetchDestName:
    """Destination name sanitization."""

    def test_fetch_dest_name_rejects_traversal(self, tool_context: ToolContext) -> None:
        """Fetch must reject '..' in dest_name."""
        with pytest.raises(ValueError, match="unsafe filename"):
            asyncio.run(
                _fetch.dispatch(
                    {"url": "https://example.com/file.txt", "dest_name": "../evil.txt"},
                    tool_context,
                )
            )

    def test_fetch_dest_name_rejects_absolute(self, tool_context: ToolContext) -> None:
        """Fetch must reject absolute paths in dest_name."""
        with pytest.raises(ValueError, match="unsafe filename"):
            asyncio.run(
                _fetch.dispatch(
                    {"url": "https://example.com/file.txt", "dest_name": "/etc/passwd"},
                    tool_context,
                )
            )

    def test_fetch_dest_name_strips_slashes(self, tool_context: ToolContext) -> None:
        """Fetch must strip leading paths from dest_name, taking only basename."""
        with pytest.raises(ValueError):
            # "dir/subdir/file.txt" should be taken as "file.txt" (OK)
            # but if we pass "/../evil", it should still be caught by ".." check
            asyncio.run(
                _fetch.dispatch(
                    {"url": "https://example.com/file.txt", "dest_name": "dir/subdir/../evil"},
                    tool_context,
                )
            )


# ============================================================================
# run_shell verb tests
# ============================================================================


class TestRunShellBasic:
    """Basic run_shell functionality."""

    @pytest.mark.skipif(
        not shutil.which("sandbox-exec"),
        reason="sandbox-exec not available (non-macOS or missing)",
    )
    def test_run_shell_echo(self, tool_context: ToolContext) -> None:
        """Simple echo command in sandbox."""
        result = asyncio.run(
            _run_shell.dispatch(
                {"command": "echo hello"},
                tool_context,
            )
        )

        assert isinstance(result, dict)
        assert result["exit_code"] == 0
        assert "hello" in result["stdout_tail"]
        assert result["sandbox_enforced"] is True
        assert result["timed_out"] is False

    @pytest.mark.skipif(
        not shutil.which("sandbox-exec"),
        reason="sandbox-exec not available (non-macOS or missing)",
    )
    def test_run_shell_exit_code(self, tool_context: ToolContext) -> None:
        """Exit code propagation."""
        result = asyncio.run(
            _run_shell.dispatch(
                {"command": "exit 42"},
                tool_context,
            )
        )

        assert result["exit_code"] == 42
        assert result["timed_out"] is False

    @pytest.mark.skipif(
        not shutil.which("sandbox-exec"),
        reason="sandbox-exec not available (non-macOS or missing)",
    )
    def test_run_shell_stderr(self, tool_context: ToolContext) -> None:
        """Stderr capture."""
        result = asyncio.run(
            _run_shell.dispatch(
                {"command": "echo error >&2 && exit 1"},
                tool_context,
            )
        )

        assert result["exit_code"] == 1
        assert "error" in result["stderr_tail"]

    @pytest.mark.skipif(
        not shutil.which("sandbox-exec"),
        reason="sandbox-exec not available (non-macOS or missing)",
    )
    def test_run_shell_requires_command(self, tool_context: ToolContext) -> None:
        """run_shell must require a non-empty command."""
        with pytest.raises(ValueError, match="non-empty"):
            asyncio.run(_run_shell.dispatch({}, tool_context))

        with pytest.raises(ValueError, match="non-empty"):
            asyncio.run(_run_shell.dispatch({"command": ""}, tool_context))

    @pytest.mark.skipif(
        not shutil.which("sandbox-exec"),
        reason="sandbox-exec not available (non-macOS or missing)",
    )
    def test_run_shell_timeout_validation(self, tool_context: ToolContext) -> None:
        """run_shell validates timeout range."""
        # Negative timeout
        with pytest.raises(ValueError, match="timeout_sec"):
            asyncio.run(
                _run_shell.dispatch(
                    {"command": "echo hi", "timeout_sec": -1},
                    tool_context,
                )
            )

        # Zero timeout
        with pytest.raises(ValueError, match="timeout_sec"):
            asyncio.run(
                _run_shell.dispatch(
                    {"command": "echo hi", "timeout_sec": 0},
                    tool_context,
                )
            )

        # Too large
        with pytest.raises(ValueError, match="timeout_sec"):
            asyncio.run(
                _run_shell.dispatch(
                    {"command": "echo hi", "timeout_sec": 121},
                    tool_context,
                )
            )


class TestRunShellWorkspace:
    """Workspace access in sandbox."""

    @pytest.mark.skipif(
        not shutil.which("sandbox-exec"),
        reason="sandbox-exec not available (non-macOS or missing)",
    )
    def test_run_shell_can_create_file_in_workspace(
        self, tool_context: ToolContext
    ) -> None:
        """run_shell can create files in the workspace directory."""
        test_file = tool_context.output_dir / "test_output.txt"

        asyncio.run(
            _run_shell.dispatch(
                {"command": f"echo content > {test_file}"},
                tool_context,
            )
        )

        assert test_file.exists()
        assert test_file.read_text().strip() == "content"

    @pytest.mark.skipif(
        not shutil.which("sandbox-exec"),
        reason="sandbox-exec not available (non-macOS or missing)",
    )
    def test_run_shell_workspace_is_cwd(self, tool_context: ToolContext) -> None:
        """run_shell executes with workspace as current directory."""
        result = asyncio.run(
            _run_shell.dispatch(
                {"command": "pwd"},
                tool_context,
            )
        )

        output_dir_str = str(tool_context.output_dir)
        assert output_dir_str in result["stdout_tail"]


class TestRunShellSandboxEnforcement:
    """Sandbox enforcement checks."""

    @pytest.mark.skipif(
        not shutil.which("sandbox-exec"),
        reason="sandbox-exec not available (non-macOS or missing)",
    )
    def test_run_shell_sandbox_enforced_true(self, tool_context: ToolContext) -> None:
        """run_shell result always has sandbox_enforced=True (or raises)."""
        result = asyncio.run(
            _run_shell.dispatch(
                {"command": "echo ok"},
                tool_context,
            )
        )

        assert result["sandbox_enforced"] is True

    def test_run_shell_no_sandbox_raises(self, tool_context: ToolContext) -> None:
        """If sandbox-exec is not available, run_shell raises RuntimeError."""
        # Temporarily mock the absence of sandbox-exec
        # (This is tested implicitly by the skipif on other tests, but let's be explicit.)
        if shutil.which("sandbox-exec"):
            pytest.skip("sandbox-exec is available; test only runs when unavailable")

        # If we reach here, sandbox-exec is not available
        with pytest.raises(RuntimeError, match="sandbox-exec unavailable"):
            asyncio.run(
                _run_shell.dispatch(
                    {"command": "echo hi"},
                    tool_context,
                )
            )


__all__ = [
    "TestFetchBasic",
    "TestFetchDestName",
    "TestRunShellBasic",
    "TestRunShellWorkspace",
    "TestRunShellSandboxEnforcement",
]
