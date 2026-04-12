"""gemia.audio.effects — Voice conversion, automatic mixing, and voice isolation."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv"}


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg error: {proc.stderr[-800:]}")


def _probe_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


# ---------------------------------------------------------------------------
# voice_convert
# ---------------------------------------------------------------------------
_VOICE_FILTERS: dict[str, str] = {
    "deep": "asetrate=44100*0.8,aresample=44100,atempo=1.25",
    "high": "asetrate=44100*1.2,aresample=44100,atempo=0.83",
    "robot": "aphaser=in_gain=0.4:out_gain=0.74:delay=3:decay=0.4:speed=0.5,flanger",
    "neutral": "acopy",
    "whisper": "lowpass=f=3000,volume=0.6,aecho=0.5:0.5:60:0.3",
}


def voice_convert(
    input_path: str,
    output_path: str,
    *,
    target_voice: str = "neutral",
) -> str:
    """Convert voice characteristics using ffmpeg audio filters.

    Args:
        input_path: Source audio or video file.
        output_path: Destination file.
        target_voice: One of ``"deep"``, ``"high"``, ``"robot"``,
                      ``"neutral"``, ``"whisper"``.

    Returns:
        output_path
    """
    af = _VOICE_FILTERS.get(target_voice)
    if af is None:
        raise ValueError(f"Unknown voice preset '{target_voice}'. "
                         f"Choose from: {list(_VOICE_FILTERS)}")

    is_video = Path(input_path).suffix.lower() in _VIDEO_EXTS

    if is_video:
        with tempfile.NamedTemporaryFile(suffix=".aac", delete=False) as tf:
            tmp_audio = tf.name
        with tempfile.NamedTemporaryFile(suffix=".aac", delete=False) as tf:
            tmp_converted = tf.name
        try:
            # Extract audio
            _run(["ffmpeg", "-y", "-i", input_path,
                  "-vn", "-c:a", "aac", tmp_audio])
            # Process audio
            _run(["ffmpeg", "-y", "-i", tmp_audio,
                  "-af", af, tmp_converted])
            # Remux
            _run(["ffmpeg", "-y",
                  "-i", input_path,
                  "-i", tmp_converted,
                  "-map", "0:v", "-map", "1:a",
                  "-c:v", "copy", "-c:a", "aac",
                  output_path])
        finally:
            for p in (tmp_audio, tmp_converted):
                Path(p).unlink(missing_ok=True)
    else:
        _run(["ffmpeg", "-y", "-i", input_path, "-af", af, output_path])

    return output_path


# ---------------------------------------------------------------------------
# voice_isolate  (#57)
# ---------------------------------------------------------------------------
def voice_isolate(
    input_path: str,
    output_path: str,
    *,
    low_hz: float = 80.0,
    high_hz: float = 8000.0,
    gate_db: float = -40.0,
) -> str:
    """Isolate voice/dialogue from background noise using bandpass + gate.

    Mirrors DaVinci Resolve 19 Fairlight *Voice Isolation* AI feature with
    an ffmpeg approximation: bandpass filter to speech frequencies, noise
    gate to suppress bleed, and dynamic compression to level dialogue.

    Args:
        input_path: Source audio or video file.
        output_path: Destination file.
        low_hz: Low-frequency cutoff in Hz. Default 80 (removes rumble).
        high_hz: High-frequency cutoff in Hz. Default 8000 (retains speech).
        gate_db: Noise gate threshold in dBFS.  Default -40.

    Returns:
        output_path
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    gate_level = 10 ** (gate_db / 20.0)
    # highpass → lowpass → agate → loudnorm
    af = (
        f"highpass=f={low_hz:.1f},"
        f"lowpass=f={high_hz:.1f},"
        f"agate=threshold={gate_level:.6f}:ratio=10:attack=10:release=100,"
        f"loudnorm=I=-16:LRA=11:TP=-1.5"
    )
    is_video = Path(input_path).suffix.lower() in _VIDEO_EXTS
    if is_video:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".aac", delete=False) as tf:
            tmp_audio = tf.name
        with tempfile.NamedTemporaryFile(suffix=".aac", delete=False) as tf:
            tmp_isolated = tf.name
        try:
            _run(["ffmpeg", "-y", "-i", input_path, "-vn", "-c:a", "aac", tmp_audio])
            _run(["ffmpeg", "-y", "-i", tmp_audio, "-af", af, tmp_isolated])
            _run([
                "ffmpeg", "-y",
                "-i", input_path, "-i", tmp_isolated,
                "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", "-c:a", "aac",
                output_path,
            ])
        finally:
            for p in (tmp_audio, tmp_isolated):
                Path(p).unlink(missing_ok=True)
    else:
        _run(["ffmpeg", "-y", "-i", input_path, "-af", af, output_path])
    return output_path


# ---------------------------------------------------------------------------
# auto_mix
# ---------------------------------------------------------------------------
def auto_mix(track_list: list[str], output_path: str) -> str:
    """Automatically mix multiple audio tracks with level normalisation.

    Analyses each track's RMS level, normalises to –14 LUFS, then combines
    with ``amix``.

    Args:
        track_list: List of audio file paths to mix.
        output_path: Destination audio file.

    Returns:
        output_path
    """
    if not track_list:
        raise ValueError("track_list is empty")

    # Measure mean volume for each track
    gains: list[float] = []
    for track in track_list:
        r = subprocess.run(
            ["ffmpeg", "-i", track, "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True,
        )
        mean_vol = -14.0  # default target LUFS
        for line in r.stderr.splitlines():
            if "mean_volume:" in line:
                mean_vol = float(line.split("mean_volume:")[-1].strip().split()[0])
                break
        # gain to reach -14 dB target
        gains.append(-14.0 - mean_vol)

    # Build filter graph: normalize each track then amix
    n = len(track_list)
    inputs = []
    for i, (track, gain) in enumerate(zip(track_list, gains)):
        inputs += ["-i", track]

    filter_parts = [f"[{i}:a]volume={g:.2f}dB[a{i}]" for i, g in enumerate(gains)]
    mix_inputs = "".join(f"[a{i}]" for i in range(n))
    filter_parts.append(f"{mix_inputs}amix=inputs={n}:duration=longest:normalize=1[out]")
    filtergraph = ";".join(filter_parts)

    _run([
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filtergraph,
        "-map", "[out]",
        output_path,
    ])
    return output_path


# ---------------------------------------------------------------------------
# ducker  — Fairlight-style sidechain ducking
# ---------------------------------------------------------------------------
def ducker(
    bed_path: str,
    dialogue_path: str,
    output_path: str,
    *,
    reduction_db: float = 12.0,
    attack_ms: float = 50.0,
    release_ms: float = 200.0,
    threshold: float = -30.0,
) -> str:
    """Auto-duck a music/bed track whenever dialogue is present.

    Mirrors DaVinci Resolve 19's Fairlight *Ducker Track FX*: the bed level
    is automatically reduced by *reduction_db* dB whenever the dialogue track
    exceeds *threshold* dBFS, with smooth attack/release envelopes.

    Args:
        bed_path: Path to background music / bed audio file.
        dialogue_path: Path to dialogue audio file (used as sidechain).
        output_path: Destination mixed audio file.
        reduction_db: How many dB to lower the bed when dialogue is active.
            Default 12.
        attack_ms: Gain reduction attack time in ms.  Default 50.
        release_ms: Gain recovery release time in ms.  Default 200.
        threshold: dBFS level above which dialogue triggers ducking.
            Default -30.

    Returns:
        output_path
    """
    attack_s = attack_ms / 1000.0
    release_s = release_ms / 1000.0
    # ffmpeg sidechaincompress: ducking via sidechain signal
    # input 0 = bed, input 1 = dialogue (sidechain)
    filtergraph = (
        f"[0:a][1:a]sidechaincompress="
        f"threshold={10 ** (threshold / 20.0):.6f}"
        f":ratio=20"
        f":attack={attack_ms:.1f}"
        f":release={release_ms:.1f}"
        f":makeup={reduction_db:.1f}"
        f":level_sc=1[ducked];"
        f"[ducked][1:a]amix=inputs=2:duration=longest:normalize=0[out]"
    )
    _run([
        "ffmpeg", "-y",
        "-i", bed_path,
        "-i", dialogue_path,
        "-filter_complex", filtergraph,
        "-map", "[out]",
        output_path,
    ])
    return output_path
