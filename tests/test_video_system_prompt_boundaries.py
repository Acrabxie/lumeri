from pathlib import Path


SYSTEM_PROMPT = (
    Path(__file__).resolve().parents[1] / "gemia" / "prompts" / "system_v3.md"
)


def test_video_system_prompt_does_not_include_quanta_guidance() -> None:
    prompt = SYSTEM_PROMPT.read_text(encoding="utf-8").casefold()

    assert "quanta" not in prompt
    assert "quantum" not in prompt
