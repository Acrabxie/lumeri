"""Audio analysis: silence_detect, beat_detect, music_extend, stem_separate."""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# beat_detect
# ---------------------------------------------------------------------------

def beat_detect(audio_path: str) -> list[float]:
    """Return a sorted list of beat timestamps (in seconds) for the given audio file.

    Primary backend: librosa.  Falls back to an ffmpeg-based rough estimate
    if librosa is not installed.
    """
    try:
        import librosa  # noqa: PLC0415
    except ImportError:
        return _beat_detect_ffmpeg(audio_path)

    y, sr = librosa.load(audio_path)
    _tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    timestamps: list[float] = librosa.frames_to_time(beat_frames, sr=sr).tolist()
    return sorted(timestamps)


def _beat_detect_ffmpeg(audio_path: str) -> list[float]:
    """Rough beat detection via ffmpeg ametadata / astats filter."""
    cmd = [
        "ffmpeg", "-i", audio_path,
        "-af", "ametadata=mode=print:file=-:key=lavfi.astats.Overall.RMS_level",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    timestamps: list[float] = []
    for line in result.stderr.splitlines():
        if line.startswith("pts_time:"):
            try:
                t = float(line.split(":")[1].split()[0])
                timestamps.append(t)
            except (IndexError, ValueError):
                pass
    if not timestamps:
        return []
    beats: list[float] = [timestamps[0]]
    for t in timestamps[1:]:
        if t - beats[-1] >= 0.4:
            beats.append(t)
    return sorted(beats)


# ---------------------------------------------------------------------------
# music_extend
# ---------------------------------------------------------------------------

def music_extend(
    audio_path: str,
    output_path: str,
    *,
    target_duration: float,
) -> str:
    """Extend (or trim) audio to *target_duration* seconds.

    If the source is already long enough it is trimmed to target_duration.
    Otherwise the source is looped and crossfaded at loop points.

    Returns *output_path*.
    """
    actual_duration = _probe_duration(audio_path)

    if actual_duration >= target_duration:
        _ffmpeg_run([
            "ffmpeg", "-y",
            "-i", audio_path,
            "-t", str(target_duration),
            "-c", "copy",
            output_path,
        ])
        return output_path

    reps = math.ceil(target_duration / actual_duration)
    crossfade_sec = 0.5

    with tempfile.NamedTemporaryFile(suffix=Path(audio_path).suffix, delete=False) as tmp:
        looped_path = tmp.name

    try:
        # Step 1: create looped version
        _ffmpeg_run([
            "ffmpeg", "-y",
            "-stream_loop", str(reps - 1),
            "-i", audio_path,
            "-t", str(target_duration + crossfade_sec),
            "-c", "copy",
            looped_path,
        ])

        # Step 2: apply acrossfade at the first loop point
        loop_point = actual_duration
        fade_start = max(0.0, loop_point - crossfade_sec / 2)

        filter_complex = (
            f"[0:a]atrim=0:{fade_start + crossfade_sec},asetpts=PTS-STARTPTS[a1];"
            f"[0:a]atrim={fade_start}:{target_duration + crossfade_sec},asetpts=PTS-STARTPTS[a2];"
            f"[a1][a2]acrossfade=d={crossfade_sec}[out]"
        )

        _ffmpeg_run([
            "ffmpeg", "-y",
            "-i", looped_path,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-t", str(target_duration),
            output_path,
        ])
    finally:
        try:
            os.unlink(looped_path)
        except OSError:
            pass

    return output_path


def _probe_duration(audio_path: str) -> float:
    """Return the duration of an audio file in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        audio_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    info = json.loads(result.stdout)
    return float(info["format"]["duration"])


def _ffmpeg_run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


# ---------------------------------------------------------------------------
# stem_separate
# ---------------------------------------------------------------------------

def stem_separate(audio_path: str, output_dir: str) -> dict[str, str]:
    """Separate audio into vocal, drums, bass, and other stems.

    Primary backend: demucs.  Falls back to ffmpeg frequency-band filters
    if demucs is not installed.

    Returns a dict mapping stem name → file path.
    """
    os.makedirs(output_dir, exist_ok=True)

    try:
        return _stem_separate_demucs(audio_path, output_dir)
    except ImportError:
        return _stem_separate_ffmpeg(audio_path, output_dir)


def _stem_separate_demucs(audio_path: str, output_dir: str) -> dict[str, str]:
    try:
        import torch  # noqa: PLC0415
        from demucs.pretrained import get_model  # noqa: PLC0415
        from demucs.apply import apply_model  # noqa: PLC0415
        import torchaudio  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError("pip install demucs") from exc

    model = get_model("htdemucs")
    model.eval()

    wav, sr = torchaudio.load(audio_path)
    if sr != model.samplerate:
        wav = torchaudio.functional.resample(wav, sr, model.samplerate)
    if wav.shape[0] == 1:
        wav = wav.repeat(2, 1)

    wav = wav.unsqueeze(0)  # (1, C, T)
    with torch.no_grad():
        sources = apply_model(model, wav, device="cpu")[0]  # (stems, C, T)

    stem_names = model.sources  # e.g. ['drums', 'bass', 'other', 'vocals']
    paths: dict[str, str] = {}
    for idx, name in enumerate(stem_names):
        out_path = os.path.join(output_dir, f"{name}.wav")
        torchaudio.save(out_path, sources[idx], model.samplerate)
        paths[name] = out_path

    for key in ("vocals", "drums", "bass", "other"):
        if key not in paths:
            paths[key] = paths.get(stem_names[0], "")

    return paths


def _stem_separate_ffmpeg(audio_path: str, output_dir: str) -> dict[str, str]:
    """Rough stem separation using ffmpeg frequency-band and channel filters."""
    stem_filters = {
        "bass":   "lowpass=f=200",
        "drums":  "bandpass=f=200:width_type=h:w=2000",
        "other":  "highpass=f=2000",
        "vocals": "pan=stereo|c0=c0-c1|c1=c1-c0",
    }
    paths: dict[str, str] = {}
    for name, af in stem_filters.items():
        out_path = os.path.join(output_dir, f"{name}.wav")
        _ffmpeg_run([
            "ffmpeg", "-y",
            "-i", audio_path,
            "-af", af,
            out_path,
        ])
        paths[name] = out_path
    return paths


# ---------------------------------------------------------------------------
# silence_detect (original)
# ---------------------------------------------------------------------------

def silence_detect(
    audio_path: str,
    *,
    threshold_db: float = -40.0,
    min_silence_sec: float = 0.5,
) -> list[dict]:
    """Detect silent segments in an audio or video file.

    Args:
        audio_path: Input audio/video file path.
        threshold_db: Silence threshold in dB (e.g. -40.0).
        min_silence_sec: Minimum duration in seconds for a segment to count as silence.

    Returns:
        List of dicts with keys ``start``, ``end``, ``duration`` (all floats, seconds).
    """
    cmd = [
        "ffmpeg", "-i", audio_path,
        "-af", f"silencedetect=noise={threshold_db}dB:d={min_silence_sec}",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    combined = proc.stdout + proc.stderr

    starts: list[float] = []
    ends: list[float] = []

    for line in combined.splitlines():
        m_start = re.search(r"silence_start:\s*([0-9.]+)", line)
        if m_start:
            starts.append(float(m_start.group(1)))
        m_end = re.search(r"silence_end:\s*([0-9.]+)", line)
        if m_end:
            ends.append(float(m_end.group(1)))

    result: list[dict] = []
    for start, end in zip(starts, ends):
        result.append({"start": start, "end": end, "duration": end - start})

    return result
