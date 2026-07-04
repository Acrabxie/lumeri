"""Tests for gemia.env_probe — dynamic dependency awareness.

The agent must see its REAL interpreter and installed packages, not a static
"Full access to NumPy/PIL/OpenCV/pandas" claim. These tests pin:

- probe_environment() reports python_version, a packages dict where a
  known-present package (numpy, installed in this venv) shows up, and an
  ffmpeg/ffprobe tools entry.
- format_environment_summary() is a non-empty string that names the python
  version, the ffmpeg status, and tells the agent to use python3.
- A package that is genuinely absent is marked absent (None) — verified by
  monkeypatching the probe to inject a fake never-installed module.
- The assembled v3 system prompt actually contains the summary text, so the
  agent sees its live environment each session.
"""
from __future__ import annotations

from pathlib import Path
import uuid

import pytest

from gemia import env_probe
from gemia.env_probe import (
    clear_cache,
    format_environment_summary,
    probe_environment,
)


@pytest.fixture(autouse=True)
def _fresh_cache():
    """Each test starts from a clean probe cache."""
    clear_cache()
    yield
    clear_cache()


def test_probe_returns_python_version_and_executable() -> None:
    env = probe_environment()
    assert isinstance(env, dict)
    # python_version like "3.12.12"
    assert isinstance(env["python_version"], str)
    assert env["python_version"]
    assert env["python_version"].split(".")[0].isdigit()
    # python_executable is sys.executable (a real path string)
    assert isinstance(env["python_executable"], str)
    assert env["python_executable"]


def test_probe_packages_dict_has_numpy_present() -> None:
    env = probe_environment()
    packages = env["packages"]
    assert isinstance(packages, dict)
    # numpy IS installed in this venv -> a truthy version string, not None.
    assert "numpy" in packages
    assert packages["numpy"] is not None
    assert isinstance(packages["numpy"], str)
    # The relevant set is all probed (each key exists, value str-or-None).
    for key in ("numpy", "cv2", "PIL", "scipy", "pandas"):
        assert key in packages
        assert packages[key] is None or isinstance(packages[key], str)


def test_probe_has_ffmpeg_and_ffprobe_keys() -> None:
    env = probe_environment()
    tools = env["tools"]
    assert "ffmpeg" in tools
    assert "ffprobe" in tools
    # ffmpeg is version-or-None; ffprobe is a bool.
    assert tools["ffmpeg"] is None or isinstance(tools["ffmpeg"], str)
    assert isinstance(tools["ffprobe"], bool)


def test_probe_is_cached() -> None:
    first = probe_environment()
    second = probe_environment()
    # Same object identity -> result was cached, not recomputed.
    assert first is second


def test_summary_is_nonempty_str_with_version_ffmpeg_and_python3() -> None:
    env = probe_environment()
    summary = format_environment_summary(env)
    assert isinstance(summary, str)
    assert summary.strip()
    # Contains the python version.
    assert env["python_version"] in summary
    # Mentions python3 explicitly (the whole point — don't assume `python`).
    assert "python3" in summary
    # Reports ffmpeg status one way or the other.
    assert "ffmpeg" in summary


def test_summary_robust_when_env_arg_omitted() -> None:
    # Called with no arg it probes internally and must still be a valid block.
    summary = format_environment_summary()
    assert isinstance(summary, str)
    assert summary.strip()
    assert "python3" in summary


def test_absent_package_marked_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A package that is never installed is reported as None, not assumed present."""
    fake_pkg = ("totally_fake_pkg", "totally_fake_pkg_import", "totally-fake-pkg")
    # Inject a fake package into the probed set; nothing by that name exists,
    # so importlib.import_module will raise -> probe must record None.
    patched = env_probe._RELEVANT_PACKAGES + (fake_pkg,)
    monkeypatch.setattr(env_probe, "_RELEVANT_PACKAGES", patched)
    clear_cache()

    env = probe_environment()
    assert "totally_fake_pkg" in env["packages"]
    assert env["packages"]["totally_fake_pkg"] is None

    summary = format_environment_summary(env)
    # The absent package shows up under the NOT installed list.
    assert "NOT installed" in summary
    assert "totally_fake_pkg" in summary


def test_summary_marks_absent_tool_when_ffmpeg_missing() -> None:
    """When ffmpeg is absent, the summary says so without claiming a version."""
    env = {
        "python_version": "3.12.0",
        "python_executable": "/usr/bin/python3",
        "os": "Linux",
        "platform": "Linux-x",
        "packages": {"numpy": "2.0.0", "pandas": None},
        "tools": {"ffmpeg": None, "ffprobe": False},
    }
    summary = format_environment_summary(env)
    assert "3.12.0" in summary
    assert "python3" in summary
    assert "ffmpeg" in summary
    # pandas absent -> appears in NOT installed; numpy present -> in Installed.
    assert "pandas" in summary
    assert "numpy" in summary


def test_probe_never_raises_on_broken_internals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even if a tool probe blows up, probe_environment returns a valid dict."""
    def _boom(_name: str):  # noqa: ANN202
        raise RuntimeError("simulated probe failure")

    monkeypatch.setattr(env_probe, "_tool_version", _boom)
    clear_cache()
    env = probe_environment()
    assert isinstance(env, dict)
    assert env["tools"]["ffmpeg"] is None  # swallowed -> absent


def test_assembled_v3_prompt_contains_environment_summary(tmp_path: Path) -> None:
    """The live environment summary is injected into the assembled v3 prompt."""
    from gemia.agent_loop_v3 import AgentLoopV3

    loop = AgentLoopV3(
        session_id=f"env_test_{uuid.uuid4().hex[:8]}",
        output_dir=tmp_path / "outputs",
        max_visual_inspections=1,
        budget_max_usd=1.0,
        budget_max_seconds=30.0,
    )
    msgs = loop.render_messages()
    system_content = msgs[0]["content"]
    assert msgs[0]["role"] == "system"

    expected = format_environment_summary()
    assert expected in system_content
    # The placeholder must be fully resolved (no leftover token).
    assert "{{environment}}" not in system_content
    # And it carries the live, real signal: python3 guidance + ffmpeg status.
    assert "python3" in system_content
    assert "ffmpeg" in system_content
