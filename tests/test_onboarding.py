"""Tests for the first-run onboarding wizard.

No real TTY, no real keys: every test injects ``input_fn`` / ``output_fn`` and
monkeypatches ``onboarding.CONFIG_PATH`` to a temp file.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gemia import onboarding


@pytest.fixture
def cfg_path(tmp_path, monkeypatch) -> Path:
    """Point onboarding at a throwaway config.json under tmp_path."""
    path = tmp_path / ".gemia" / "config.json"
    monkeypatch.setattr(onboarding, "CONFIG_PATH", path)
    return path


class _ScriptedInput:
    """A scripted ``input_fn`` that returns queued answers, raising if drained."""

    def __init__(self, answers: list[str]):
        self._answers = list(answers)
        self.calls: list[str] = []

    def __call__(self, prompt: str = "") -> str:
        self.calls.append(prompt)
        if not self._answers:
            raise AssertionError(f"input_fn drained; unexpected extra prompt: {prompt!r}")
        return self._answers.pop(0)


def _capture_output() -> tuple[list[str], "callable"]:
    lines: list[str] = []

    def out(msg: str = "") -> None:
        lines.append(str(msg))

    return lines, out


# ── needs_onboarding ───────────────────────────────────────────────────
def test_needs_onboarding_missing_file(cfg_path):
    assert not cfg_path.exists()
    assert onboarding.needs_onboarding() is True


def test_needs_onboarding_with_gemini_key(cfg_path):
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({"gemini_api_key": "g-test"}), encoding="utf-8")
    assert onboarding.needs_onboarding() is False


def test_needs_onboarding_empty_provider_value(cfg_path):
    # A present-but-blank provider key does NOT count as configured.
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({"gemini_api_key": "   "}), encoding="utf-8")
    assert onboarding.needs_onboarding() is True


# ── run_onboarding: provider choices ───────────────────────────────────
def test_run_onboarding_openrouter(cfg_path):
    # provider=3 (openrouter), key="or-test", skip optional openrouter model,
    # skip search (0), skip proxy (blank).
    inp = _ScriptedInput(["3", "or-test", "", "0", ""])
    lines, out = _capture_output()

    merged = onboarding.run_onboarding(input_fn=inp, output_fn=out)

    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert on_disk["openrouter_api_key"] == "or-test"
    assert on_disk["lumeri_v3_provider"] == "openrouter"
    assert merged["openrouter_api_key"] == "or-test"
    # search left unset -> DuckDuckGo
    assert "search_provider" not in on_disk
    assert "proxy" not in on_disk


def test_run_onboarding_vertex_block(cfg_path):
    # provider=1 (vertex), project="my-proj", skip search (0), skip proxy.
    inp = _ScriptedInput(["1", "my-proj", "0", ""])
    lines, out = _capture_output()

    onboarding.run_onboarding(input_fn=inp, output_fn=out)

    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert on_disk["vertex_project"] == "my-proj"
    assert on_disk["lumeri_v3_provider"] == "vertex"
    assert on_disk["lumeri_v3_model"] == "google/gemini-3.5-flash"
    assert on_disk["lumeri_v3_location"] == "global"
    assert on_disk["vertex_location"] == "us-central1"
    assert on_disk["vertex_video_model"] == "veo-3.1-generate-preview"
    assert on_disk["vertex_image_model"] == "gemini-3.1-flash-image-preview"
    assert on_disk["vertex_audio_model"] == "lyria-3-pro-preview"
    # Auth note printed somewhere.
    assert any("Vertex AI User" in line for line in lines)


def test_run_onboarding_gemini(cfg_path):
    inp = _ScriptedInput(["2", "g-secret", "0", ""])
    _, out = _capture_output()
    onboarding.run_onboarding(input_fn=inp, output_fn=out)
    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert on_disk["gemini_api_key"] == "g-secret"
    assert on_disk["lumeri_v3_provider"] == "gemini"


def test_run_onboarding_openai_with_optional_model(cfg_path):
    # provider=4 (openai), key, custom model, skip search, skip proxy.
    inp = _ScriptedInput(["4", "sk-test", "gpt-4o-mini", "0", ""])
    _, out = _capture_output()
    onboarding.run_onboarding(input_fn=inp, output_fn=out)
    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert on_disk["openai_api_key"] == "sk-test"
    assert on_disk["lumeri_v3_provider"] == "openai"
    assert on_disk["openai_model"] == "gpt-4o-mini"


def test_run_onboarding_claude(cfg_path):
    inp = _ScriptedInput(["5", "ant-secret", "0", ""])
    _, out = _capture_output()
    onboarding.run_onboarding(input_fn=inp, output_fn=out)
    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert on_disk["anthropic_api_key"] == "ant-secret"
    assert on_disk["lumeri_v3_provider"] == "claude"


# ── run_onboarding: optional search ────────────────────────────────────
def test_run_onboarding_search_tavily(cfg_path):
    # gemini provider + key; search=1 (tavily) + key; skip proxy.
    inp = _ScriptedInput(["2", "g-key", "1", "tav-key", ""])
    _, out = _capture_output()
    onboarding.run_onboarding(input_fn=inp, output_fn=out)
    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert on_disk["tavily_api_key"] == "tav-key"
    assert on_disk["search_provider"] == "tavily"


def test_run_onboarding_skip_search_leaves_unset(cfg_path):
    inp = _ScriptedInput(["2", "g-key", "0", ""])
    _, out = _capture_output()
    onboarding.run_onboarding(input_fn=inp, output_fn=out)
    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "search_provider" not in on_disk
    assert "tavily_api_key" not in on_disk


def test_run_onboarding_google_cse_needs_two_fields(cfg_path):
    # search=6 (google_cse) needs key + id.
    inp = _ScriptedInput(["2", "g-key", "6", "cse-key", "cse-id", ""])
    _, out = _capture_output()
    onboarding.run_onboarding(input_fn=inp, output_fn=out)
    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert on_disk["google_cse_key"] == "cse-key"
    assert on_disk["google_cse_id"] == "cse-id"
    assert on_disk["search_provider"] == "google_cse"


def test_run_onboarding_search_searxng(cfg_path):
    # gemini provider + key; search=7 (searxng, keyless self-hosted) + URL; skip proxy.
    inp = _ScriptedInput(["2", "g-key", "7", "http://127.0.0.1:8080", ""])
    _, out = _capture_output()
    onboarding.run_onboarding(input_fn=inp, output_fn=out)
    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert on_disk["searxng_url"] == "http://127.0.0.1:8080"
    assert on_disk["search_provider"] == "searxng"


# ── run_onboarding: optional proxy ─────────────────────────────────────
def test_run_onboarding_proxy_set(cfg_path):
    inp = _ScriptedInput(["2", "g-key", "0", "http://127.0.0.1:7890"])
    _, out = _capture_output()
    onboarding.run_onboarding(input_fn=inp, output_fn=out)
    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert on_disk["proxy"] == "http://127.0.0.1:7890"


# ── merge-preserve ─────────────────────────────────────────────────────
def test_merge_preserve_existing_unrelated_key(cfg_path):
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        json.dumps({"some_unrelated_key": "keep-me", "google_oauth_client_id": "abc"}),
        encoding="utf-8",
    )
    inp = _ScriptedInput(["3", "or-test", "", "0", ""])
    _, out = _capture_output()
    onboarding.run_onboarding(input_fn=inp, output_fn=out)
    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert on_disk["some_unrelated_key"] == "keep-me"
    assert on_disk["google_oauth_client_id"] == "abc"
    assert on_disk["openrouter_api_key"] == "or-test"


def test_merge_write_helper_preserves(cfg_path):
    onboarding.merge_write({"a": "1"})
    onboarding.merge_write({"b": "2"})
    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert on_disk == {"a": "1", "b": "2"}


# ── headless ───────────────────────────────────────────────────────────
def test_ensure_onboarded_headless_unconfigured_returns_false(cfg_path):
    lines, out = _capture_output()

    def fail_input(prompt: str = "") -> str:  # pragma: no cover - must NOT be called
        raise AssertionError("input must not be called in headless mode")

    # print_instructions() defaults to the builtin print; patch it (and input)
    # at the builtins level so instructions are captured and input is never used.
    import builtins

    orig_print = builtins.print
    builtins.print = out  # type: ignore[assignment]
    orig_input = builtins.input
    builtins.input = fail_input  # type: ignore[assignment]
    try:
        result = onboarding.ensure_onboarded(interactive=False)
    finally:
        builtins.print = orig_print  # type: ignore[assignment]
        builtins.input = orig_input  # type: ignore[assignment]

    assert result is False
    blob = "\n".join(lines)
    # Instructions must name the exact provider key names.
    for key in (
        "vertex_project",
        "gemini_api_key",
        "openrouter_api_key",
        "openai_api_key",
        "anthropic_api_key",
    ):
        assert key in blob


def test_ensure_onboarded_configured_returns_true_no_io(cfg_path):
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({"gemini_api_key": "g"}), encoding="utf-8")
    # Even with interactive=True, a configured install must not prompt.
    assert onboarding.ensure_onboarded(interactive=True) is True


def test_print_instructions_returns_text(cfg_path):
    lines, out = _capture_output()
    text = onboarding.print_instructions(output_fn=out)
    assert "python -m gemia setup" in text
    assert "search_provider" in text
    assert text in "\n".join(lines)


# ── secrets never echoed in full ───────────────────────────────────────
def test_secrets_masked_in_summary(cfg_path):
    secret = "or-supersecretkey-1234"
    inp = _ScriptedInput(["3", secret, "", "0", ""])
    lines, out = _capture_output()
    onboarding.run_onboarding(input_fn=inp, output_fn=out)
    summary = "\n".join(lines)
    # The full secret must never appear verbatim in the printed summary lines
    # that echo the saved config.
    summary_after_saved = summary.split("Configuration saved", 1)[-1]
    assert secret not in summary_after_saved
    # A masked form (tail kept) should be present.
    assert onboarding.mask_secret(secret) in summary_after_saved
    assert "1234" in summary_after_saved  # tail preserved
    # But still saved in full on disk.
    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert on_disk["openrouter_api_key"] == secret


def test_mask_secret_helper():
    assert onboarding.mask_secret("") == ""
    assert onboarding.mask_secret("ab") == "**"
    assert onboarding.mask_secret("abcdef") == "**cdef"
    full = "verylongsecretvalue"
    masked = onboarding.mask_secret(full)
    assert masked != full
    assert masked.endswith(full[-4:])


# ── chmod 600 on written config ────────────────────────────────────────
def test_config_written_chmod_600(cfg_path, monkeypatch):
    # Assert the write path requests 0o600. We capture chmod calls because some
    # filesystems (e.g. the external SSD used for TMPDIR here) don't enforce
    # POSIX mode bits, which would make a resulting-mode assertion flaky.
    chmod_calls: list[tuple[Path, int]] = []
    real_chmod = Path.chmod

    def spy_chmod(self, mode, *args, **kwargs):
        chmod_calls.append((self, mode))
        return real_chmod(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", spy_chmod)

    inp = _ScriptedInput(["2", "g-key", "0", ""])
    _, out = _capture_output()
    onboarding.run_onboarding(input_fn=inp, output_fn=out)

    assert any(p == cfg_path and mode == 0o600 for p, mode in chmod_calls), chmod_calls
