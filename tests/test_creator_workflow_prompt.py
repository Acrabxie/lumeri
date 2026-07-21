from __future__ import annotations

from pathlib import Path


PROMPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "gemia"
    / "prompts"
    / "system_v3.md"
)


def _prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _normalized_prompt() -> str:
    return " ".join(_prompt().split())


def test_creator_loop_is_explicitly_advisory_and_user_directed() -> None:
    prompt = _normalized_prompt()

    assert "understand → plan → edit → inspect → revise → export" in prompt
    assert "not a required checklist, workflow state machine" in prompt
    assert "or completion gate" in prompt
    assert "The user may skip, combine, or reorder them" in prompt
    assert "Follow the user's explicit scope and sequence" in prompt


def test_recoverable_failures_stop_after_three_same_root_cause_rounds() -> None:
    prompt = _normalized_prompt()

    assert "Handle recoverable failures internally when possible" in prompt
    assert "Three consecutive recovery rounds" in prompt
    assert "the same underlying failure" in prompt
    assert "After the third round, stop that recovery loop" in prompt
    assert "not a host state machine, global tool limit, or completion gate" in prompt


def test_preview_first_does_not_block_an_explicit_direct_export() -> None:
    prompt = _normalized_prompt()

    assert "prefer to produce a playable preview" in prompt
    assert "not an export prerequisite" in prompt
    assert "explicitly asks to export the current work directly" in prompt
    assert "honor that request" in prompt
    assert "Skip a redundant preview" in prompt
