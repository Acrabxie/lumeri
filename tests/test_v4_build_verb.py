"""Tests for v4 build verb family: build, check_job, wait_for_job, save_skill."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from gemia.tools._context import AssetRegistry, ToolContext
from gemia.tools._jobs import JobRegistry
from gemia.tools import build


def _run_async(coro):
    """Helper to run async function in sync test."""
    return asyncio.run(coro)


class TestToolContextDefaults:
    """Test that ToolContext defaults include JobRegistry."""

    def test_tool_context_has_job_registry(self, tmp_path: Path) -> None:
        """Verify ToolContext instantiates with default JobRegistry."""
        ctx = ToolContext(
            session_id="test_session",
            output_dir=tmp_path,
            registry=AssetRegistry(),
            emit_progress=lambda _: None,
        )
        assert isinstance(ctx.jobs, JobRegistry)
        assert ctx.jobs.list_pending() == []


class TestBuildDispatcher:
    """Test the build verb (async submit)."""

    def setup_method(self):
        """Clean up module-level _PROCESSES before each test."""
        # Kill any lingering processes
        for job_id, (proc, _) in list(build._PROCESSES.items()):
            try:
                import os
                import signal
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except:
                pass
        build._PROCESSES.clear()

    @pytest.fixture(autouse=True)
    def _force_sandbox_enabled(self):
        """Build tests verify sandbox behavior; force-enable regardless of user toggle."""
        from gemia.sandbox_v4 import is_sandbox_disabled, set_sandbox_disabled
        was_disabled = is_sandbox_disabled()
        set_sandbox_disabled(False)
        yield
        set_sandbox_disabled(was_disabled)

    def test_build_happy_path(self, tmp_path: Path) -> None:
        """Test successful build submission and execution."""
        ctx = ToolContext(
            session_id="test_session",
            output_dir=tmp_path,
            registry=AssetRegistry(),
            emit_progress=lambda _: None,
        )

        code = 'print("hello world")'
        result = _run_async(build.dispatch(
            {
                "code": code,
                "filename": "script.py",
                "timeout_sec": 10,
                "note": "test script",
            },
            ctx,
        ))

        assert "job_id" in result
        assert result["status"] == "submitted"
        assert result["sandbox_enforced"] is True
        assert "script_path" in result
        assert "stdout_log" in result
        assert "stderr_log" in result

        # Verify script was written
        script_path = tmp_path / result["script_path"]
        assert script_path.exists()
        assert script_path.read_text() == code

    def test_build_empty_code_raises(self, tmp_path: Path) -> None:
        """Test that empty code raises ValueError."""
        ctx = ToolContext(
            session_id="test_session",
            output_dir=tmp_path,
            registry=AssetRegistry(),
            emit_progress=lambda _: None,
        )

        with pytest.raises(ValueError, match="non-empty"):
            _run_async(build.dispatch({"code": ""}, ctx))

    def test_build_filename_with_slash_raises(self, tmp_path: Path) -> None:
        """Test that filename with path separators raises ValueError."""
        ctx = ToolContext(
            session_id="test_session",
            output_dir=tmp_path,
            registry=AssetRegistry(),
            emit_progress=lambda _: None,
        )

        with pytest.raises(ValueError, match="path separators"):
            _run_async(build.dispatch(
                {"code": "print('hi')", "filename": "subdir/script.py"},
                ctx,
            ))

    def test_build_timeout_clamping(self, tmp_path: Path) -> None:
        """Test timeout_sec clamping."""
        ctx = ToolContext(
            session_id="test_session",
            output_dir=tmp_path,
            registry=AssetRegistry(),
            emit_progress=lambda _: None,
        )

        # timeout <= 0
        with pytest.raises(ValueError, match="timeout_sec must be in"):
            _run_async(build.dispatch(
                {"code": "print('hi')", "timeout_sec": 0},
                ctx,
            ))

        # timeout > 600
        with pytest.raises(ValueError, match="timeout_sec must be in"):
            _run_async(build.dispatch(
                {"code": "print('hi')", "timeout_sec": 601},
                ctx,
            ))

    def test_build_pending_limit(self, tmp_path: Path) -> None:
        """Test that >3 pending builds are rejected."""
        ctx = ToolContext(
            session_id="test_session",
            output_dir=tmp_path,
            registry=AssetRegistry(),
            emit_progress=lambda _: None,
        )

        # Submit 3 builds
        for i in range(3):
            _run_async(build.dispatch(
                {
                    "code": "import time; time.sleep(10)",
                    "timeout_sec": 30,
                    "note": f"build {i}",
                },
                ctx,
            ))

        # Fourth should fail
        with pytest.raises(ValueError, match="Too many pending builds"):
            _run_async(build.dispatch(
                {"code": "print('hi')", "timeout_sec": 10},
                ctx,
            ))


class TestCheckJobDispatcher:
    """Test the check_job verb (poll)."""

    def setup_method(self):
        """Clean up module-level _PROCESSES before each test."""
        # Kill any lingering processes
        for job_id, (proc, _) in list(build._PROCESSES.items()):
            try:
                import os
                import signal
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except:
                pass
        build._PROCESSES.clear()

    def test_check_job_not_found(self, tmp_path: Path) -> None:
        """Test check_job with unknown job_id raises KeyError."""
        ctx = ToolContext(
            session_id="test_session",
            output_dir=tmp_path,
            registry=AssetRegistry(),
            emit_progress=lambda _: None,
        )

        with pytest.raises(KeyError):
            _run_async(build.dispatch_check({"job_id": "unknown_id"}, ctx))

    def test_check_job_returns_structure(self, tmp_path: Path) -> None:
        """Test check_job result shape."""
        ctx = ToolContext(
            session_id="test_session",
            output_dir=tmp_path,
            registry=AssetRegistry(),
            emit_progress=lambda _: None,
        )

        # Submit a quick build
        submit_result = _run_async(build.dispatch(
            {"code": "print('quick')", "timeout_sec": 5},
            ctx,
        ))
        job_id = submit_result["job_id"]

        # Poll it
        check_result = _run_async(build.dispatch_check({"job_id": job_id}, ctx))

        assert check_result["job_id"] == job_id
        assert check_result["status"] in ("submitted", "running", "done", "failed")
        assert isinstance(check_result["stdout_tail"], str)
        assert isinstance(check_result["stderr_tail"], str)
        assert "summary" in check_result

    def test_check_job_timeout(self, tmp_path: Path) -> None:
        """Test that check_job kills process after timeout."""
        ctx = ToolContext(
            session_id="test_session",
            output_dir=tmp_path,
            registry=AssetRegistry(),
            emit_progress=lambda _: None,
        )

        # Submit a sleep(10) with 2s timeout
        submit_result = _run_async(build.dispatch(
            {"code": "import time; time.sleep(10)", "timeout_sec": 2},
            ctx,
        ))
        job_id = submit_result["job_id"]

        # Wait a bit then check — should timeout
        time.sleep(2.5)
        check_result = _run_async(build.dispatch_check({"job_id": job_id}, ctx))

        # Status should be failed due to timeout
        assert check_result["status"] == "failed"
        # Verify the job record itself has the timeout error
        job_record = ctx.jobs.get(job_id)
        assert job_record.last_polled_status == "failed"
        assert "timeout" in (job_record.final_error or "").lower()


class TestWaitForJobDispatcher:
    """Test the wait_for_job verb (blocking poll)."""

    def setup_method(self):
        """Clean up module-level _PROCESSES before each test."""
        # Kill any lingering processes
        for job_id, (proc, _) in list(build._PROCESSES.items()):
            try:
                import os
                import signal
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except:
                pass
        build._PROCESSES.clear()

    def test_wait_for_job_quick_return(self, tmp_path: Path) -> None:
        """Test wait_for_job on a fast-finishing build."""
        ctx = ToolContext(
            session_id="test_session",
            output_dir=tmp_path,
            registry=AssetRegistry(),
            emit_progress=lambda _: None,
        )

        # Submit a quick build
        submit_result = _run_async(build.dispatch(
            {"code": "print('fast')", "timeout_sec": 10},
            ctx,
        ))
        job_id = submit_result["job_id"]

        # Wait for it
        wait_result = _run_async(build.dispatch_wait(
            {"job_id": job_id, "max_wait_sec": 30},
            ctx,
        ))

        assert wait_result["job_id"] == job_id
        assert wait_result["status"] in ("done", "failed")
        assert "waited_sec" in wait_result
        assert "timed_out" in wait_result
        assert wait_result["timed_out"] is False

    def test_wait_for_job_max_wait_timeout(self, tmp_path: Path) -> None:
        """Test wait_for_job respects max_wait_sec."""
        ctx = ToolContext(
            session_id="test_session",
            output_dir=tmp_path,
            registry=AssetRegistry(),
            emit_progress=lambda _: None,
        )

        # Submit a long build
        submit_result = _run_async(build.dispatch(
            {"code": "import time; time.sleep(100)", "timeout_sec": 120},
            ctx,
        ))
        job_id = submit_result["job_id"]

        # Wait with short timeout
        wait_result = _run_async(build.dispatch_wait(
            {"job_id": job_id, "max_wait_sec": 2},
            ctx,
        ))

        assert wait_result["timed_out"] is True
        assert wait_result["waited_sec"] >= 2.0

    def test_wait_for_job_max_wait_clamping(self, tmp_path: Path) -> None:
        """Test max_wait_sec clamping."""
        ctx = ToolContext(
            session_id="test_session",
            output_dir=tmp_path,
            registry=AssetRegistry(),
            emit_progress=lambda _: None,
        )

        # max_wait <= 0
        with pytest.raises(ValueError, match="max_wait_sec must be in"):
            _run_async(build.dispatch_wait(
                {"job_id": "any", "max_wait_sec": 0},
                ctx,
            ))

        # max_wait > 300
        with pytest.raises(ValueError, match="max_wait_sec must be in"):
            _run_async(build.dispatch_wait(
                {"job_id": "any", "max_wait_sec": 301},
                ctx,
            ))


class TestSaveSkillDispatcher:
    """Test the save_skill verb (persistence)."""

    def test_save_skill_basic(self, tmp_path: Path) -> None:
        """Test basic skill save."""
        ctx = ToolContext(
            session_id="test_session",
            output_dir=tmp_path,
            registry=AssetRegistry(),
            emit_progress=lambda _: None,
        )

        # Create a source file
        builds_dir = tmp_path / "builds" / "test_build"
        builds_dir.mkdir(parents=True)
        source_file = builds_dir / "my_script.py"
        source_file.write_text("print('skill')")

        result = _run_async(build.dispatch_save_skill(
            {
                "source": "builds/test_build/my_script.py",
                "name": "My Cool Skill",
                "description": "A test skill",
            },
            ctx,
        ))

        assert result["skill"] == "my-cool-skill"
        assert "path" in result
        skill_file = Path(result["path"])
        assert skill_file.exists()
        assert skill_file.read_text() == "print('skill')"

        # Check metadata
        meta_file = skill_file.parent / f"{result['skill']}.json"
        assert meta_file.exists()
        meta = json.loads(meta_file.read_text())
        assert meta["name"] == "My Cool Skill"
        assert meta["slug"] == "my-cool-skill"
        assert meta["description"] == "A test skill"

    def test_save_skill_path_traversal_blocked(self, tmp_path: Path) -> None:
        """Test that path traversal is blocked."""
        ctx = ToolContext(
            session_id="test_session",
            output_dir=tmp_path,
            registry=AssetRegistry(),
            emit_progress=lambda _: None,
        )

        with pytest.raises(ValueError, match="outside workspace"):
            _run_async(build.dispatch_save_skill(
                {
                    "source": "../../etc/passwd",
                    "name": "evil",
                },
                ctx,
            ))

    def test_save_skill_not_found(self, tmp_path: Path) -> None:
        """Test that nonexistent source raises FileNotFoundError."""
        ctx = ToolContext(
            session_id="test_session",
            output_dir=tmp_path,
            registry=AssetRegistry(),
            emit_progress=lambda _: None,
        )

        with pytest.raises(FileNotFoundError):
            _run_async(build.dispatch_save_skill(
                {
                    "source": "nonexistent.py",
                    "name": "ghost",
                },
                ctx,
            ))

    def test_save_skill_no_overwrite(self, tmp_path: Path) -> None:
        """Test that duplicate skill name raises without overwrite."""
        ctx = ToolContext(
            session_id="test_session",
            output_dir=tmp_path,
            registry=AssetRegistry(),
            emit_progress=lambda _: None,
        )

        # Create two source files
        src1 = tmp_path / "script1.py"
        src1.write_text("print(1)")
        src2 = tmp_path / "script2.py"
        src2.write_text("print(2)")

        # Save first skill
        _run_async(build.dispatch_save_skill(
            {"source": "script1.py", "name": "dupe-skill"},
            ctx,
        ))

        # Try to save with same name without overwrite
        with pytest.raises(ValueError, match="already exists"):
            _run_async(build.dispatch_save_skill(
                {"source": "script2.py", "name": "dupe-skill", "overwrite": False},
                ctx,
            ))

    def test_save_skill_with_overwrite(self, tmp_path: Path) -> None:
        """Test that overwrite=true replaces existing skill."""
        ctx = ToolContext(
            session_id="test_session",
            output_dir=tmp_path,
            registry=AssetRegistry(),
            emit_progress=lambda _: None,
        )

        src1 = tmp_path / "script1.py"
        src1.write_text("print(1)")
        src2 = tmp_path / "script2.py"
        src2.write_text("print(2)")

        # Save first skill
        _run_async(build.dispatch_save_skill(
            {"source": "script1.py", "name": "dupe-skill"},
            ctx,
        ))

        # Overwrite with second
        result2 = _run_async(build.dispatch_save_skill(
            {"source": "script2.py", "name": "dupe-skill", "overwrite": True},
            ctx,
        ))

        assert result2["skill"] == "dupe-skill"
        skill_file = Path(result2["path"])
        assert skill_file.read_text() == "print(2)"


class TestWiring:
    """Test that verbs are wired into the dispatch table."""

    def test_verbs_in_tool_schemas(self) -> None:
        """Test that build verbs are in TOOL_SCHEMAS."""
        from gemia.tools._schema import TOOL_SCHEMAS

        names = [t["function"]["name"] for t in TOOL_SCHEMAS]
        assert "build" in names
        assert "check_job" in names
        assert "wait_for_job" in names
        assert "save_skill" in names

    def test_verbs_in_tool_names(self) -> None:
        """Test that build verbs are in TOOL_NAMES."""
        from gemia.tools._schema import TOOL_NAMES

        assert "build" in TOOL_NAMES
        assert "check_job" in TOOL_NAMES
        assert "wait_for_job" in TOOL_NAMES
        assert "save_skill" in TOOL_NAMES

    def test_verbs_in_dispatcher(self) -> None:
        """Test that build verbs are in DISPATCHER."""
        from gemia.tools import DISPATCHER

        assert "build" in DISPATCHER
        assert "check_job" in DISPATCHER
        assert "wait_for_job" in DISPATCHER
        assert "save_skill" in DISPATCHER

        # Verify they're not stubs
        assert DISPATCHER["build"].__name__ == "dispatch"
        assert DISPATCHER["check_job"].__name__ == "dispatch_check"
        assert DISPATCHER["wait_for_job"].__name__ == "dispatch_wait"
        assert DISPATCHER["save_skill"].__name__ == "dispatch_save_skill"

    def test_verbs_in_budget_guard(self) -> None:
        """Test that build verbs have cost entries."""
        from gemia.budget_guard import _TOOL_COSTS

        assert "build" in _TOOL_COSTS
        assert "check_job" in _TOOL_COSTS
        assert "wait_for_job" in _TOOL_COSTS
        assert "save_skill" in _TOOL_COSTS

        # Verify they're zero-cost
        assert _TOOL_COSTS["build"]["usd"] == 0.0
        assert _TOOL_COSTS["check_job"]["usd"] == 0.0
        assert _TOOL_COSTS["wait_for_job"]["usd"] == 0.0
        assert _TOOL_COSTS["save_skill"]["usd"] == 0.0


class TestSandboxEnforcement:
    """Test sandbox enforcement behavior."""

    def setup_method(self):
        """Clean up module-level _PROCESSES before each test."""
        # Kill any lingering processes
        for job_id, (proc, _) in list(build._PROCESSES.items()):
            try:
                import os
                import signal
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except:
                pass
        build._PROCESSES.clear()

    @pytest.fixture(autouse=True)
    def _force_sandbox_enabled(self):
        """These tests verify sandbox enforcement behavior; force-enable regardless of user toggle."""
        from gemia.sandbox_v4 import is_sandbox_disabled, set_sandbox_disabled
        was_disabled = is_sandbox_disabled()
        set_sandbox_disabled(False)
        yield
        set_sandbox_disabled(was_disabled)

    def test_build_no_sandbox_enforced_raises(self, tmp_path: Path) -> None:
        """Test that build raises RuntimeError if sandbox not enforced."""
        ctx = ToolContext(
            session_id="test_session",
            output_dir=tmp_path,
            registry=AssetRegistry(),
            emit_progress=lambda _: None,
        )

        # Mock build_v4_sandbox_command to return enforced=False
        with patch(
            "gemia.tools.build.build_v4_sandbox_command",
            return_value=(["echo", "hi"], False),
        ):
            with pytest.raises(RuntimeError, match="sandbox enforcement"):
                _run_async(build.dispatch(
                    {"code": "print('hi')", "timeout_sec": 10},
                    ctx,
                ))
