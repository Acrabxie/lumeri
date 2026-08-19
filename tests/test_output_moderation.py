"""Output-side screening — the half Waffo asks for that a prompt filter misses.

Layer B of the moderation gate: ``tests/test_moderation.py`` covers the prompt
side, this covers the product. The cases that matter are the ones where a
mistake shows refused media to a viewer or, just as bad, refuses a war
documentary — plus the one that decides what happens when the detector itself is
down, because "screening quietly turned itself off" is precisely what an account
review looks for.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from gemia.errors import ContentPolicyError
from gemia.moderation import guard_tool_output
from gemia.moderation.backends import GeminiOutputBackend, _parse_verdict, resolve_backend
from gemia.moderation.output import (
    OutputModerationUnavailable,
    OutputVerdict,
    check_output,
    platform_funded,
)
from gemia.moderation.policy import Category


@dataclass
class _Record:
    asset_id: str
    kind: str
    path: Path


class _Registry:
    def __init__(self, record: _Record | None = None) -> None:
        self._records = {record.asset_id: record} if record else {}

    def contains(self, asset_id: str) -> bool:
        return asset_id in self._records

    def get(self, asset_id: str) -> _Record:
        return self._records[asset_id]


class _Ctx:
    def __init__(self, registry: _Registry, extra: dict | None = None) -> None:
        self.registry = registry
        self.extra = extra or {}


class _Backend:
    """A detector with a scripted answer."""

    name = "fake"

    def __init__(self, verdict: OutputVerdict | Exception) -> None:
        self._verdict = verdict
        self.calls: list[tuple[str, Path]] = []

    async def inspect(self, kind: str, path: Path) -> OutputVerdict:
        self.calls.append((kind, path))
        if isinstance(self._verdict, Exception):
            raise self._verdict
        return self._verdict


@pytest.fixture
def asset(tmp_path: Path) -> tuple[_Ctx, Path]:
    media = tmp_path / "out.png"
    media.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    ctx = _Ctx(_Registry(_Record("img_1", "image", media)))
    return ctx, media


@pytest.fixture(autouse=True)
def _ledger_to_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test's ledger writes out of the repository."""
    monkeypatch.setenv("LUMERI_MODERATION_LEDGER", str(tmp_path / "ledger.jsonl"))


def _ledger_lines(tmp_path: Path) -> list[dict]:
    path = tmp_path / "ledger.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _run(coro):
    return asyncio.run(coro)


# --- the refusal path -------------------------------------------------------

def test_refused_output_raises_and_removes_the_file(asset, tmp_path):
    ctx, media = asset
    backend = _Backend(
        OutputVerdict(allowed=False, category=Category.SEXUAL, reason="no", detector="fake")
    )
    monkey = pytest.MonkeyPatch()
    monkey.setattr("gemia.moderation.resolve_backend", lambda: backend)
    try:
        with pytest.raises(ContentPolicyError) as excinfo:
            _run(guard_tool_output("generate_image", {"asset_id": "img_1"}, ctx))
    finally:
        monkey.undo()

    assert excinfo.value.category == "sexual"
    # The refused media must not survive behind a withheld id.
    assert not media.exists()
    entries = _ledger_lines(tmp_path)
    assert [e["stage"] for e in entries] == ["output"]
    assert entries[0]["category"] == "sexual"
    assert entries[0]["asset_id"] == "img_1"


def test_child_safety_refusal_records_no_fingerprint(asset, tmp_path):
    ctx, _ = asset
    backend = _Backend(
        OutputVerdict(allowed=False, category=Category.MINORS, reason="no", detector="fake")
    )
    monkey = pytest.MonkeyPatch()
    monkey.setattr("gemia.moderation.resolve_backend", lambda: backend)
    try:
        with pytest.raises(ContentPolicyError):
            _run(guard_tool_output("generate_image", {"asset_id": "img_1"}, ctx))
    finally:
        monkey.undo()

    entry = _ledger_lines(tmp_path)[0]
    assert entry["category"] == "minors"
    # Every other category gets a hash so a repeat can be recognised; building a
    # durable index keyed on this one is not this platform's job.
    assert "asset_sha256" not in entry


def test_allowed_output_passes_through_untouched(asset, tmp_path):
    ctx, media = asset
    backend = _Backend(OutputVerdict(allowed=True, detector="fake"))
    monkey = pytest.MonkeyPatch()
    monkey.setattr("gemia.moderation.resolve_backend", lambda: backend)
    try:
        _run(guard_tool_output("generate_image", {"asset_id": "img_1"}, ctx))
    finally:
        monkey.undo()

    assert media.exists()
    assert _ledger_lines(tmp_path) == []


# --- what happens when the detector cannot answer ---------------------------

def test_unavailable_detector_is_recorded_when_screening_is_not_required(asset, tmp_path):
    ctx, media = asset
    monkey = pytest.MonkeyPatch()
    monkey.setattr("gemia.moderation.resolve_backend", lambda: None)
    monkey.delenv("GEMIA_OUTPUT_MODERATION_REQUIRED", raising=False)
    try:
        _run(guard_tool_output("generate_image", {"asset_id": "img_1"}, ctx))
    finally:
        monkey.undo()

    assert media.exists()
    entry = _ledger_lines(tmp_path)[0]
    # The gap is written down rather than assumed away.
    assert entry["unscreened"] is True
    assert entry["asset_id"] == "img_1"


def test_unavailable_detector_refuses_when_screening_is_required(asset, tmp_path):
    ctx, media = asset
    monkey = pytest.MonkeyPatch()
    monkey.setattr("gemia.moderation.resolve_backend", lambda: None)
    monkey.setenv("GEMIA_OUTPUT_MODERATION_REQUIRED", "1")
    try:
        with pytest.raises(ContentPolicyError):
            _run(guard_tool_output("generate_image", {"asset_id": "img_1"}, ctx))
    finally:
        monkey.undo()

    # Fail closed: unscreened media does not reach a viewer. The file is left
    # alone — nothing judged it, so there is nothing to enforce against.
    assert media.exists()


def test_detector_that_raises_is_not_read_as_permission(asset):
    ctx, _ = asset
    backend = _Backend(OutputModerationUnavailable("provider down"))
    monkey = pytest.MonkeyPatch()
    monkey.setattr("gemia.moderation.resolve_backend", lambda: backend)
    monkey.setenv("GEMIA_OUTPUT_MODERATION_REQUIRED", "1")
    try:
        with pytest.raises(ContentPolicyError):
            _run(guard_tool_output("generate_image", {"asset_id": "img_1"}, ctx))
    finally:
        monkey.undo()


# --- scope ------------------------------------------------------------------

def test_non_generative_tools_are_not_screened(asset):
    ctx, _ = asset
    backend = _Backend(OutputVerdict(allowed=False, category=Category.SEXUAL, detector="fake"))
    monkey = pytest.MonkeyPatch()
    monkey.setattr("gemia.moderation.resolve_backend", lambda: backend)
    try:
        # A trim is not a generation; screening it would mean re-judging media
        # the platform already cleared once.
        _run(guard_tool_output("timeline_trim_clip", {"asset_id": "img_1"}, ctx))
    finally:
        monkey.undo()
    assert backend.calls == []


def test_user_funded_generation_is_not_screened(tmp_path):
    media = tmp_path / "byok.png"
    media.write_bytes(b"\x89PNG\r\n\x1a\n")
    ctx = _Ctx(_Registry(_Record("img_1", "image", media)), extra={"funding": "user"})
    backend = _Backend(OutputVerdict(allowed=False, category=Category.SEXUAL, detector="fake"))
    monkey = pytest.MonkeyPatch()
    monkey.setattr("gemia.moderation.resolve_backend", lambda: backend)
    try:
        _run(guard_tool_output("generate_image", {"asset_id": "img_1"}, ctx))
    finally:
        monkey.undo()

    # Money that never passed through the merchant of record; the prompt-side
    # guard still ran, and it runs whatever model was swapped in.
    assert backend.calls == []
    assert media.exists()


def test_platform_funded_defaults_to_true():
    assert platform_funded(None) is True
    assert platform_funded(_Ctx(_Registry())) is True
    assert platform_funded(_Ctx(_Registry(), extra={"funding": "user"})) is False


def test_audio_is_not_visually_screened(tmp_path):
    track = tmp_path / "vo.wav"
    track.write_bytes(b"RIFF")
    verdict = _run(check_output("audio", track, backend=None))
    # No detector configured, yet no refusal: narration is screened as text on
    # the prompt side, where the words cannot be misheard into a false positive.
    assert verdict.allowed


# --- parsing the detector's answer ------------------------------------------

@pytest.mark.parametrize(
    "payload",
    [
        "not json at all",
        "{",
        '{"verdict": "maybe"}',
        '{"confidence": 0.9}',
    ],
)
def test_unclear_detector_answers_fail_closed(payload):
    with pytest.raises(OutputModerationUnavailable):
        _parse_verdict(payload, detector="fake")


def test_parses_a_fenced_allow():
    verdict = _parse_verdict('```json\n{"verdict": "allow"}\n```', detector="fake")
    assert verdict.allowed


def test_csam_answer_maps_onto_the_published_child_safety_category():
    verdict = _parse_verdict(
        '{"verdict": "block", "category": "csam", "confidence": 0.98}', detector="fake"
    )
    assert verdict.allowed is False
    # The AUP publishes six categories; the detector's finer split folds into them.
    assert verdict.category is Category.MINORS
    assert verdict.scores == {"minors": 0.98}


def test_refusal_text_does_not_echo_what_the_detector_saw():
    verdict = _parse_verdict(
        '{"verdict": "block", "category": "sexual", "note": "explicit act between X and Y"}',
        detector="fake",
    )
    assert "explicit act" not in verdict.reason
    assert verdict.reason


# --- backend selection ------------------------------------------------------

def test_unset_backend_yields_none(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GEMIA_OUTPUT_MODERATION_BACKEND", raising=False)
    assert resolve_backend() is None


def test_unknown_backend_name_fails_closed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GEMIA_OUTPUT_MODERATION_BACKEND", "gemnini")
    # A typo in deployment config must not read as "screening off".
    with pytest.raises(OutputModerationUnavailable):
        resolve_backend()


def test_gemini_backend_without_a_key_is_unavailable(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GEMIA_OUTPUT_MODERATION_BACKEND", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    media = tmp_path / "a.png"
    media.write_bytes(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(OutputModerationUnavailable):
        _run(GeminiOutputBackend(api_key="").inspect("image", media))


def test_missing_generated_file_is_unavailable_not_allowed(tmp_path):
    backend = _Backend(OutputVerdict(allowed=True, detector="fake"))
    with pytest.raises(OutputModerationUnavailable):
        _run(check_output("image", tmp_path / "gone.png", backend=backend))


# --- the wiring itself ------------------------------------------------------
#
# Everything above calls guard_tool_output directly, which proves the policy but
# not that anything invokes it. These two close that gap: the dispatcher wrapper
# must run the output guard, and every DISPATCHER entry must be that wrapper.
# Without them, deleting one line from `_screened` leaves the whole suite green.

def test_dispatch_wrapper_screens_the_product(tmp_path, monkeypatch):
    import gemia.tools as tools_pkg

    media = tmp_path / "img_1.png"
    media.write_bytes(b"\x89PNG\r\n\x1a\n")
    ctx = _Ctx(_Registry(_Record("img_1", "image", media)))
    backend = _Backend(
        OutputVerdict(allowed=False, category=Category.SEXUAL, reason="refused", detector="fake")
    )
    monkeypatch.setattr("gemia.moderation.resolve_backend", lambda: backend)

    async def fake_generate(args, ctx):
        return {"asset_id": "img_1", "summary": "an image"}

    wrapped = tools_pkg._screened("generate_image", fake_generate)
    with pytest.raises(ContentPolicyError):
        _run(wrapped({"prompt": "a landscape"}, ctx))

    assert backend.calls == [("image", media)]
    assert not media.exists()


def test_every_generative_verb_reaches_the_output_guard():
    """The four screened verbs must be wired through the screening wrapper.

    Paired with ``test_library_verb_manifest``: that one proves every DISPATCHER
    entry is a ``_screened`` wrapper, this one proves the wrapper carries the
    output guard. Together they mean no generative verb can return media that
    was never judged.
    """
    import inspect as _inspect

    import gemia.tools as tools_pkg
    from gemia.moderation import SCREENED_TOOL_ARGS

    source = _inspect.getsource(tools_pkg._screened)
    assert "guard_tool_output" in source, (
        "the dispatch wrapper no longer screens generated media — output-side "
        "enforcement is gone while every other test stays green"
    )
    for verb in SCREENED_TOOL_ARGS:
        assert verb in tools_pkg.DISPATCHER, f"{verb} is screened but not dispatchable"
