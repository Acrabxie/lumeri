"""Tests for v3 memory injection + auto daily-log + the remember/log_note verbs.

Coverage:
  - format_memory_for_prompt reads a planted MEMORY.md, caps length, and never
    raises on a missing store.
  - The assembled v3 system prompt contains the injected memory block and has
    NO leftover ``{{memory}}`` placeholder.
  - remember persists a fact; assert_memory_safe rejects secret-bearing text.
  - append_daily_entry appends a line to the day file (and creates it).
  - the log_note tool appends to today's log.
  - the remember tool persists via the dispatcher.
  - the step-narration directive text is present in system_v3.md.
  - the new tools are registered in TOOL_NAMES + DISPATCHER + the schema list.

All filesystem state is redirected to ``tmp_path`` by monkeypatching
``memory.memory_root`` — the real ``~/.gemia`` is NEVER touched.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from gemia import memory


@pytest.fixture
def mem_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the Gemia memory root to a tmp dir for the whole test."""
    root = tmp_path / "gemia_memory"
    monkeypatch.setattr(memory, "memory_root", lambda: root)
    return root


# ── format_memory_for_prompt ──────────────────────────────────────────


def test_format_memory_reads_planted_memory(mem_root: Path) -> None:
    mem_root.mkdir(parents=True, exist_ok=True)
    planted = "# Gemia Durable Memory\n\n- User prefers a DaVinci-grade UI bar.\n"
    memory.durable_memory_path().write_text(planted, encoding="utf-8")

    block = memory.format_memory_for_prompt()
    assert "DaVinci-grade UI bar" in block


def test_format_memory_missing_never_raises(mem_root: Path) -> None:
    # No files planted at all — must return a short placeholder, not raise.
    block = memory.format_memory_for_prompt()
    assert isinstance(block, str)
    assert block.strip() != ""
    assert "no durable memory" in block.lower()


def test_format_memory_is_capped(mem_root: Path) -> None:
    mem_root.mkdir(parents=True, exist_ok=True)
    memory.durable_memory_path().write_text("A" * 50000, encoding="utf-8")

    block = memory.format_memory_for_prompt(max_chars=1000)
    assert len(block) <= 1000


# ── assembled v3 prompt contains injected memory, no leftover placeholder ──


def test_v3_prompt_injects_memory_and_has_no_leftover_placeholder(
    mem_root: Path, tmp_path: Path
) -> None:
    mem_root.mkdir(parents=True, exist_ok=True)
    marker = "MEMORY-MARKER-XYZZY user likes cool grades"
    memory.durable_memory_path().write_text(
        f"# Gemia Durable Memory\n\n- {marker}\n", encoding="utf-8"
    )

    from gemia.agent_loop_v3 import AgentLoopV3

    loop = AgentLoopV3(
        session_id="sess_mem_inject",
        output_dir=tmp_path / "outputs",
        budget_max_usd=1.0,
        budget_max_seconds=60.0,
    )
    msgs = loop.render_messages()
    system = msgs[0]["content"]

    assert "{{memory}}" not in system  # placeholder fully replaced
    assert marker in system  # planted memory is injected
    assert "## What you remember" in system  # the section header is present


# ── remember (function + tool) + secret rejection ─────────────────────


def test_remember_fact_persists(mem_root: Path) -> None:
    record = memory.remember_fact(
        "User prefers FileBeam for APK delivery when adb fails.",
        title="APK delivery",
        kind="workflow",
    )
    assert record["action"] == "appended"
    stored = memory.durable_memory_path().read_text(encoding="utf-8")
    assert "FileBeam for APK delivery" in stored
    assert "**APK delivery**" in stored


def test_remember_fact_idempotent_update_by_title(mem_root: Path) -> None:
    memory.remember_fact("First version", title="UI bar")
    memory.remember_fact("Updated version", title="UI bar")
    stored = memory.durable_memory_path().read_text(encoding="utf-8")
    # Only one bullet for this title, carrying the updated text.
    assert stored.count("**UI bar**") == 1
    assert "Updated version" in stored
    assert "First version" not in stored


def test_assert_memory_safe_rejects_secret_text() -> None:
    with pytest.raises(ValueError):
        memory.assert_memory_safe("here is my api_key = sk-abcdef0123456789abcdef")


def test_remember_fact_rejects_secret(mem_root: Path) -> None:
    with pytest.raises(ValueError):
        memory.remember_fact("token = ghp_abcdefghijklmnopqrstuvwxyz0123456789")


def test_remember_tool_dispatch_persists(mem_root: Path) -> None:
    from gemia.tools import DISPATCHER

    result = asyncio.run(
        DISPATCHER["remember"](
            {"content": "User is haibogavin@example test fact.", "title": "Handle"},
            None,
        )
    )
    assert result["remembered"] is True
    stored = memory.durable_memory_path().read_text(encoding="utf-8")
    assert "test fact" in stored


# ── append_daily_entry (function + log_note tool) ─────────────────────


def test_append_daily_entry_creates_and_appends(mem_root: Path) -> None:
    day = "2026-06-28"
    out = memory.append_daily_entry("did a useful thing", day=day)
    assert out["written"] is True
    path = memory.daily_path(day)
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "did a useful thing" in text

    # A second entry appends, not overwrites.
    memory.append_daily_entry("did another thing", day=day)
    text2 = path.read_text(encoding="utf-8")
    assert "did a useful thing" in text2
    assert "did another thing" in text2


def test_append_daily_entry_collapses_newlines(mem_root: Path) -> None:
    out = memory.append_daily_entry("line one\nline two", day="2026-06-28")
    assert out["written"] is True
    assert "\n" not in out["entry"].split("] ")[-1]  # single logical line
    assert "line one line two" in out["entry"]


def test_append_daily_entry_skips_secret(mem_root: Path) -> None:
    out = memory.append_daily_entry("password = supersecretvalue12345", day="2026-06-28")
    assert out["written"] is False
    assert out.get("reason") == "secret"


def test_log_note_tool_appends(mem_root: Path) -> None:
    from gemia.tools import DISPATCHER

    result = asyncio.run(
        DISPATCHER["log_note"]({"text": "breadcrumb from the agent"}, None)
    )
    assert result["logged"] is True
    path = memory.daily_path()
    assert path.exists()
    assert "breadcrumb from the agent" in path.read_text(encoding="utf-8")


# ── narration directive present in the prompt ─────────────────────────


def test_narration_directive_present_in_prompt() -> None:
    tpl = (
        Path(__file__).resolve().parent.parent
        / "gemia"
        / "prompts"
        / "system_v3.md"
    ).read_text(encoding="utf-8")
    assert "Narrate before you act" in tpl
    # The single-line constraint is part of the directive.
    assert "one line" in tpl.lower()


# ── new tools registered ──────────────────────────────────────────────


def test_new_tools_registered() -> None:
    from gemia.tools import DISPATCHER, TOOL_NAMES
    from gemia.tools._schema import TOOL_NAMES as SCHEMA_NAMES

    for name in ("remember", "log_note"):
        assert name in TOOL_NAMES
        assert name in SCHEMA_NAMES
        assert name in DISPATCHER
        assert callable(DISPATCHER[name])
