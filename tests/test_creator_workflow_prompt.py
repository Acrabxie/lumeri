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


def test_exploratory_questions_remain_advisory_until_user_agrees() -> None:
    prompt = _normalized_prompt()

    assert "Exploratory questions stay advisory" in prompt
    assert "give a concise recommendation with the key tradeoffs" in prompt
    assert "do not implement it until the user agrees" in prompt


def test_recoverable_failures_switch_route_after_three_same_root_cause_rounds() -> None:
    prompt = _normalized_prompt()

    assert "Handle recoverable failures internally when possible" in prompt
    assert "Three consecutive recovery rounds" in prompt
    assert "the same underlying failure" in prompt
    assert "After the third round, stop that recovery path, not the task" in prompt
    assert "switch to a materially different in-scope" in prompt
    assert "not a host state machine, global tool limit, or completion gate" in prompt


def test_preview_first_does_not_block_an_explicit_direct_export() -> None:
    prompt = _normalized_prompt()

    assert "prefer to produce a playable preview" in prompt
    assert "not an export prerequisite" in prompt
    assert "explicitly asks to export the current work directly" in prompt
    assert "honor that request" in prompt
    assert "Skip a redundant preview" in prompt


def test_direct_frontend_edits_are_preserved_as_shared_project_state() -> None:
    prompt = _normalized_prompt()

    assert "Respect the user's direct edits" in prompt
    assert "are part of the shared project state" in prompt
    assert "do not arbitrarily override or undo them" in prompt


def test_storyboard_and_media_source_choices_follow_user_intent() -> None:
    prompt = _normalized_prompt()

    assert "storyboard is one available structure" in prompt
    assert "Do not introduce or rebuild a shotlist for a local edit" in prompt
    assert "Follow the requested source strategy" in prompt
    assert "explicitly requests generated imagery" in prompt


def test_craft_libraries_are_preferences_not_absolute_bans() -> None:
    prompt = _normalized_prompt()

    assert "prefer `vector_motion` when its semantic controls fit the brief" in prompt
    assert "Direct keyframes remain valid" in prompt
    assert "they do not override precise direct edits" in prompt
    assert "never hand-key" not in prompt
