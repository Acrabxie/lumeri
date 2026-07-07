"""narrate: script line → spoken voiceover audio asset (local TTS).

Requires the macOS ``say`` engine (present on the dev Mac). Asserts the tool
synthesizes real audio, registers it, and reports a positive measured duration
— the value the storyboard uses to pace a cut to the voiceover.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

import pytest

from gemia.tools import DISPATCHER
from gemia.tools._context import AssetRegistry, ToolContext

pytestmark = pytest.mark.skipif(
    not (shutil.which("say") or shutil.which("espeak")),
    reason="no local TTS backend (say/espeak) available",
)


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        session_id="v3-narrate",
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _u: None,
    )


def _call(verb: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return asyncio.run(DISPATCHER[verb](args, ctx))


def test_narrate_is_real_not_stub():
    assert "narrate" in DISPATCHER
    assert "stub" not in DISPATCHER["narrate"].__qualname__.lower()


def test_narrate_makes_audio_with_duration(tmp_path):
    ctx = _ctx(tmp_path)
    out = _call("narrate", {"text": "Every great story begins with a single frame."}, ctx)
    aid = out["asset_id"]
    rec = ctx.registry.get(aid)
    assert rec.kind == "audio" and rec.path.exists() and rec.path.stat().st_size > 0
    assert out["metadata"]["duration_sec"] > 0
    assert out["metadata"]["provider"] == "local_tts"
    assert out["metadata"]["word_count"] == 8


def test_narrate_rate_is_clamped(tmp_path):
    ctx = _ctx(tmp_path)
    out = _call("narrate", {"text": "Fast talk", "rate": 9999}, ctx)
    assert out["metadata"]["rate_wpm"] == 400  # clamped to the ceiling


def test_narrate_requires_text(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ValueError):
        _call("narrate", {"text": "   "}, ctx)
