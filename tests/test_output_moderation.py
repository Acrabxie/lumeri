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

import email.message
import urllib.error

import pytest

from gemia.moderation import backends

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


def test_gemini_backend_without_credentials_is_unavailable(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Neither transport configured means unavailable — never a silent pass.

    ``_vertex_target`` is stubbed rather than left to the environment: on a
    developer machine the Vertex route *is* configured, and without the stub this
    test would reach the real API — billing a unit test and passing for the wrong
    reason (the fixture's 8-byte PNG being rejected upstream).
    """
    media = tmp_path / "a.png"
    media.write_bytes(b"\x89PNG\r\n\x1a\n")
    backend = GeminiOutputBackend(api_key="")
    monkeypatch.setattr(backend, "_vertex_target", lambda: None)
    with pytest.raises(OutputModerationUnavailable):
        _run(backend.inspect("image", media))


def test_gemini_backend_prefers_vertex_when_configured(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """With both routes available the Vertex one is used.

    This platform holds no static Gemini key; a detector that reached for one
    first would be unavailable in production, which under required screening
    means refusing every generation instead of screening it.
    """
    media = tmp_path / "a.png"
    media.write_bytes(b"\x89PNG\r\n\x1a\n")
    backend = GeminiOutputBackend(api_key="a-key")
    monkeypatch.setattr(backend, "_vertex_target", lambda: ("https://example.invalid", "m", ""))
    used: list[str] = []
    monkeypatch.setattr(backend, "_post_vertex", lambda *a: used.append("vertex") or '{"verdict": "allow"}')
    monkeypatch.setattr(backend, "_post_api_key", lambda *a: used.append("apikey") or '{"verdict": "allow"}')
    verdict = _run(backend.inspect("image", media))
    assert used == ["vertex"]
    assert verdict.allowed


def _clear_vertex_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("VERTEX_PROJECT", "VERTEX_LOCATION", "LUMERI_V3_LOCATION", "OPENROUTER_PROXY"):
        monkeypatch.delenv(name, raising=False)


def test_vertex_project_falls_back_to_the_provider_profile(monkeypatch: pytest.MonkeyPatch):
    """A credentialed machine must not report itself as having no detector.

    This deployment stores its Vertex project under ``brain_provider_profiles``
    rather than at the top level. Reading only top-level keys made
    ``resolve_backend`` return a backend that could never reach a transport, so
    /health said "no detector configured" on a machine that was fully set up.
    """
    _clear_vertex_env(monkeypatch)
    monkeypatch.setattr("gemia.gemini_client._read_config_key", lambda field: "")
    monkeypatch.setattr(
        "gemia.gemini_client._read_config_value",
        lambda field: {"vertex": {"vertex_project": "p-from-profile", "vertex_location": "global"}}
        if field == "brain_provider_profiles"
        else None,
    )
    target = GeminiOutputBackend()._vertex_target()
    assert target is not None
    assert "projects/p-from-profile/" in target[0]


def test_top_level_vertex_project_wins_over_the_profile(monkeypatch: pytest.MonkeyPatch):
    """The profile is the fallback, not an override.

    An operator who sets the key explicitly must not be silently overruled by a
    profile left behind from another provider.
    """
    _clear_vertex_env(monkeypatch)
    monkeypatch.setattr(
        "gemia.gemini_client._read_config_key",
        lambda field: "p-explicit" if field == "vertex_project" else "",
    )
    monkeypatch.setattr(
        "gemia.gemini_client._read_config_value",
        lambda field: {"vertex": {"vertex_project": "p-from-profile"}}
        if field == "brain_provider_profiles"
        else None,
    )
    target = GeminiOutputBackend()._vertex_target()
    assert target is not None
    assert "projects/p-explicit/" in target[0]


def test_vertex_target_is_none_when_no_level_holds_a_project(monkeypatch: pytest.MonkeyPatch):
    """Absent everywhere still has to read as unconfigured, never as a pass."""
    _clear_vertex_env(monkeypatch)
    monkeypatch.setattr("gemia.gemini_client._read_config_key", lambda field: "")
    monkeypatch.setattr("gemia.gemini_client._read_config_value", lambda field: None)
    assert GeminiOutputBackend()._vertex_target() is None


class _FakeOpener:
    """An opener that replays a scripted sequence of outcomes."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    def open(self, request, timeout=None):  # noqa: ARG002 - mirrors urllib
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        class _Resp:
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *a):
                return False
            def read(self_inner):
                return outcome
        return _Resp()


def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://x.invalid", code, "boom", headers, None)


def test_a_rate_limit_is_retried_and_can_still_succeed(monkeypatch: pytest.MonkeyPatch):
    """429 is the common failure on a fixed quota, and it is usually transient.

    Without this the first rate limit ended the screening, which under required
    mode refuses a generation the user already paid for.
    """
    monkeypatch.setattr(backends.time, "sleep", lambda _: None)
    opener = _FakeOpener([_http_error(429), b'{"ok": 1}'])
    assert backends._post_json(opener, object()) == {"ok": 1}
    assert opener.calls == 2


def test_a_bad_request_is_never_retried(monkeypatch: pytest.MonkeyPatch):
    """Repeating a malformed request only spends the quota the next one needs."""
    monkeypatch.setattr(backends.time, "sleep", lambda _: None)
    opener = _FakeOpener([_http_error(400)])
    with pytest.raises(OutputModerationUnavailable):
        backends._post_json(opener, object())
    assert opener.calls == 1


def test_attempts_are_capped_and_exhaustion_reads_as_unavailable(monkeypatch: pytest.MonkeyPatch):
    """A detector that never answered must not be mistaken for one that allowed."""
    monkeypatch.setattr(backends.time, "sleep", lambda _: None)
    opener = _FakeOpener([_http_error(429) for _ in range(backends._MAX_ATTEMPTS)])
    with pytest.raises(OutputModerationUnavailable) as caught:
        backends._post_json(opener, object())
    assert opener.calls == backends._MAX_ATTEMPTS
    assert "attempt" in str(caught.value)


def test_server_retry_after_wins_over_the_local_backoff(monkeypatch: pytest.MonkeyPatch):
    """Only the server knows when its capacity returns."""
    slept: list[float] = []
    monkeypatch.setattr(backends.time, "sleep", lambda d: slept.append(d))
    opener = _FakeOpener([_http_error(429, retry_after="4"), b'{"ok": 1}'])
    backends._post_json(opener, object())
    assert slept == [4.0]


def test_retry_after_is_capped_so_one_header_cannot_stall_a_generation(monkeypatch: pytest.MonkeyPatch):
    slept: list[float] = []
    monkeypatch.setattr(backends.time, "sleep", lambda d: slept.append(d))
    opener = _FakeOpener([_http_error(429, retry_after="9999"), b'{"ok": 1}'])
    backends._post_json(opener, object())
    assert slept == [backends._RETRY_AFTER_CAP_SECONDS]


def test_backoff_grows_and_carries_jitter():
    """Jitter keeps simultaneous failures from resynchronising into a new burst.

    Range alone does not prove jitter: a fixed 0.5 and 1.0 sit inside the
    jittered bands too. Repeated draws are what distinguish a spread from a
    constant, and a constant is what turns many screenings rejected in the same
    second into one synchronised second wave against the same quota.
    """
    first = [backends._retry_delay(0, None) for _ in range(20)]
    second = [backends._retry_delay(1, None) for _ in range(20)]
    assert all(0.5 <= d <= 0.75 for d in first)
    assert all(1.0 <= d <= 1.5 for d in second)
    assert max(first) < min(second), "backoff must grow between attempts"
    assert len(set(first)) > 1, "no jitter: simultaneous retries would resynchronise"
    assert len(set(second)) > 1, "no jitter on the second attempt either"


def test_a_non_json_body_is_not_retried(monkeypatch: pytest.MonkeyPatch):
    """A body that is not JSON will not become JSON on a second ask."""
    monkeypatch.setattr(backends.time, "sleep", lambda _: None)
    opener = _FakeOpener([b"<html>gateway</html>"])
    with pytest.raises(OutputModerationUnavailable):
        backends._post_json(opener, object())
    assert opener.calls == 1


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
