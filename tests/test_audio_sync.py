"""DAY3 av-sync: deterministic tests for align_audio + detect_beats.

Synthetic audio only (numpy + soundfile); no network, no external media.

Note on beat tempo: librosa's tempo estimator is tuned for real musical
spectral content and does NOT return an accurate BPM on bare synthetic tone /
click fixtures (it collapses to a few grid values regardless of true spacing).
So these tests assert that the beat pipeline *runs and yields beats*, plus that
onset detection and waveform alignment are numerically accurate — the parts that
are deterministic on synthetic audio. Accurate BPM on real music is exercised in
the loop's manual real-audio verification, not here.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import soundfile as sf

from gemia.audio.analysis import align_offset, beat_info, detect_onsets, suggest_cut_points
from gemia.tools import DISPATCHER
from gemia.tools._context import AssetRegistry, ToolContext

SR = 22050


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        session_id="audio_sync",
        output_dir=tmp_path,
        registry=AssetRegistry(),
        emit_progress=lambda _u: None,
    )


def _run(name: str, args: dict, ctx: ToolContext) -> dict:
    return asyncio.run(DISPATCHER[name](args, ctx))


def _click_track(path: Path, *, bpm: int = 120, dur: float = 8.0,
                 sr: int = SR, noise: float = 0.0, seed: int = 3) -> Path:
    """A metronome: a short 1 kHz sine burst on every beat, optional noise bed."""
    n = int(dur * sr)
    y = np.zeros(n, dtype="float32")
    step = 60.0 / bpm
    burst_len = int(0.04 * sr)
    t = np.arange(burst_len) / sr
    env = np.hanning(burst_len).astype("float32")
    burst = (0.9 * np.sin(2 * np.pi * 1000 * t) * env).astype("float32")
    k = 0
    while True:
        start = int(round(k * step * sr))
        if start + burst_len >= n:
            break
        y[start:start + burst_len] += burst
        k += 1
    if noise > 0:
        rng = np.random.default_rng(seed)
        y = y + (noise * rng.standard_normal(n)).astype("float32")
    sf.write(str(path), y, sr)
    return path


def _noise(path: Path, *, dur: float = 2.0, sr: int = SR, seed: int = 7) -> Path:
    rng = np.random.default_rng(seed)
    y = (0.5 * rng.standard_normal(int(dur * sr))).astype("float32")
    sf.write(str(path), y, sr)
    return path


def _prepend_silence(src: Path, dst: Path, *, lead: float, sr: int = SR) -> Path:
    y, _sr = sf.read(str(src))
    pad = np.zeros(int(round(lead * sr)), dtype=y.dtype)
    sf.write(str(dst), np.concatenate([pad, y]), sr)
    return dst


# --------------------------- onset (accurate) -----------------------------

def test_detect_onsets_counts_clicks(tmp_path):
    click = _click_track(tmp_path / "click.wav", bpm=120, dur=8.0)  # clean
    onsets = detect_onsets(str(click))
    assert 12 <= len(onsets) <= 18, len(onsets)


# --------------------------- beat pipeline --------------------------------

def test_beat_info_produces_beats(tmp_path):
    # Bare synthetic clicks make librosa's beat tracker degenerate (0 beats);
    # a light noise bed lets it engage. We assert the pipeline yields beats,
    # not an exact BPM (see module docstring).
    rhythm = _click_track(tmp_path / "rhythm.wav", bpm=120, dur=8.0, noise=0.004)
    info = beat_info(str(rhythm))
    assert info["tempo_bpm"] > 0.0, info
    assert len(info["beats"]) >= 6, info
    # cut points derive from beats and thin with `every`
    cuts = suggest_cut_points(str(rhythm), source="beat", every=2)
    assert 0 < len(cuts) <= len(info["beats"])


def test_detect_beats_tool_end_to_end(tmp_path):
    ctx = _ctx(tmp_path)
    rhythm = _click_track(tmp_path / "r.wav", bpm=120, dur=8.0, noise=0.004)
    aid = ctx.registry.add_external(rhythm).asset_id
    out = _run("detect_beats", {"asset_id": aid, "include_onsets": True, "cut_every": 2}, ctx)
    assert out["asset_id"] == aid
    assert out["tempo_bpm"] > 0.0, out
    assert out["beat_count"] >= 6, out
    assert isinstance(out["onsets"], list) and len(out["onsets"]) > 0
    assert 0 < len(out["cut_points"]) <= out["beat_count"]
    assert "BPM" in out["summary"]


def test_detect_beats_missing_asset(tmp_path):
    ctx = _ctx(tmp_path)
    out = _run("detect_beats", {"asset_id": "nope_001"}, ctx)
    assert out["beat_count"] == 0
    assert "not found" in out["summary"]


# --------------------------- alignment (accurate) -------------------------

def test_align_offset_recovers_known_delay(tmp_path):
    ref = _noise(tmp_path / "ref.wav", dur=2.0)
    delayed = _prepend_silence(ref, tmp_path / "delayed.wav", lead=0.5)
    res = align_offset(str(ref), str(delayed), sr=SR)
    # 'delayed' lags 'ref' by ~0.5s -> positive offset by our sign convention
    assert 0.47 <= res["offset_sec"] <= 0.53, res
    assert res["confidence"] > 0.5, res
    assert res["method"] == "waveform-xcorr"


def test_align_offset_sign_symmetry(tmp_path):
    ref = _noise(tmp_path / "ref.wav", dur=2.0)
    delayed = _prepend_silence(ref, tmp_path / "delayed.wav", lead=0.5)
    # swap roles: arg1 is the later clip, arg2 the earlier -> negative offset
    res = align_offset(str(delayed), str(ref), sr=SR)
    assert -0.53 <= res["offset_sec"] <= -0.47, res


def test_align_audio_tool_end_to_end(tmp_path):
    ctx = _ctx(tmp_path)
    ref = _noise(tmp_path / "ref.wav", dur=2.0)
    delayed = _prepend_silence(ref, tmp_path / "delayed.wav", lead=0.5)
    ref_id = ctx.registry.add_external(ref).asset_id
    oth_id = ctx.registry.add_external(delayed).asset_id
    out = _run("align_audio", {"reference_asset_id": ref_id, "asset_ids": [oth_id]}, ctx)
    assert out["reference"] == ref_id
    assert len(out["alignments"]) == 1
    a = out["alignments"][0]
    assert a["asset_id"] == oth_id
    assert 0.47 <= a["offset_sec"] <= 0.53, a
    assert a["confidence"] > 0.5
    assert "lags" in a["suggestion"]


def test_align_audio_missing_reference(tmp_path):
    ctx = _ctx(tmp_path)
    out = _run("align_audio", {"reference_asset_id": "nope_001", "asset_ids": ["x"]}, ctx)
    assert out["alignments"] == []
    assert "not found" in out["summary"]
