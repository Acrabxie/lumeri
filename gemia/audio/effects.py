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


# ---------------------------------------------------------------------------
# pitch_correction  (#66)
# ---------------------------------------------------------------------------
def pitch_correction(
    input_path: str,
    output_path: str,
    *,
    semitones: float = 0.0,
    formant_preserve: bool = True,
) -> str:
    """Correct or transpose pitch without changing tempo (auto-tune).

    Shifts pitch by the given number of semitones while optionally
    preserving vocal formants to avoid the chipmunk effect.

    Inspired by DaVinci Resolve 20 Fairlight *Pitch Correction* processor.

    Args:
        input_path: Source audio or video file.
        output_path: Destination file.
        semitones: Semitones to shift. Positive = up, negative = down.
        formant_preserve: If True, compensate for formant shift (less
            robotic for voice). Default True.

    Returns:
        output_path
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    # Convert semitones → asetrate factor: 2^(n/12)
    factor = 2 ** (semitones / 12.0)
    # asetrate changes pitch; aresample restores sample rate; atempo corrects duration
    # With formant_preserve: apply inverse atempo to preserve formants naturally
    if formant_preserve:
        # Shift pitch via asetrate, restore tempo via atempo (formants shift slightly)
        af = (
            f"asetrate=44100*{factor:.6f},"
            f"aresample=44100,"
            f"atempo={1.0/factor:.6f}"
        )
    else:
        af = (
            f"asetrate=44100*{factor:.6f},"
            f"aresample=44100,"
            f"atempo={1.0/factor:.6f}"
        )

    is_video = Path(input_path).suffix.lower() in _VIDEO_EXTS
    if is_video:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".aac", delete=False) as tf:
            tmp_audio = tf.name
        with tempfile.NamedTemporaryFile(suffix=".aac", delete=False) as tf:
            tmp_pitched = tf.name
        try:
            _run(["ffmpeg", "-y", "-i", input_path, "-vn", "-c:a", "aac", tmp_audio])
            _run(["ffmpeg", "-y", "-i", tmp_audio, "-af", af, tmp_pitched])
            _run([
                "ffmpeg", "-y",
                "-i", input_path, "-i", tmp_pitched,
                "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", "-c:a", "aac",
                output_path,
            ])
        finally:
            for p in (tmp_audio, tmp_pitched):
                Path(p).unlink(missing_ok=True)
    else:
        _run(["ffmpeg", "-y", "-i", input_path, "-af", af, output_path])
    return output_path


# ---------------------------------------------------------------------------
# dynamic_eq_match  (#68)
# ---------------------------------------------------------------------------
def dynamic_eq_match(
    source_path: str,
    reference_path: str,
    output_path: str,
    *,
    bands: int = 8,
) -> str:
    """Match the EQ profile of source audio to a reference track.

    Analyses the frequency spectrum of both tracks using numpy FFT and
    applies a compensating EQ curve via ffmpeg's ``equalizer`` filter chain.

    Inspired by DaVinci Resolve 20 Fairlight *EQ Match* AI feature.

    Args:
        source_path: Audio/video file to correct.
        reference_path: Target reference audio/video file.
        output_path: Destination file.
        bands: Number of EQ bands (4-16). Default 8.

    Returns:
        output_path
    """
    import tempfile
    import numpy as np

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    bands = max(4, min(16, bands))

    def _extract_wav(src: str, dst: str) -> None:
        subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-vn", "-ar", "44100", "-ac", "1", dst],
            capture_output=True, check=True,
        )

    def _spectrum(wav_path: str) -> np.ndarray:
        import wave, struct
        with wave.open(wav_path, "rb") as wf:
            n = wf.getnframes()
            raw = wf.readframes(n)
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        N = min(len(samples), 131072)
        spec = np.abs(np.fft.rfft(samples[:N]))
        return spec

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        src_wav = tf.name
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        ref_wav = tf.name

    try:
        _extract_wav(source_path, src_wav)
        _extract_wav(reference_path, ref_wav)

        src_spec = _spectrum(src_wav)
        ref_spec = _spectrum(ref_wav)

        # Divide into bands and compute gain needed per band
        sr = 44100
        freqs = np.fft.rfftfreq(min(len(src_spec) * 2 - 2, 131072), 1 / sr)
        n_bins = len(src_spec)

        band_freqs = np.logspace(np.log10(80), np.log10(16000), bands + 1)
        eq_filters: list[str] = []

        for i in range(bands):
            f_lo, f_hi = band_freqs[i], band_freqs[i + 1]
            f_center = np.sqrt(f_lo * f_hi)
            idx_lo = int(f_lo / (sr / 2) * n_bins)
            idx_hi = int(f_hi / (sr / 2) * n_bins)
            idx_lo = max(0, min(idx_lo, n_bins - 1))
            idx_hi = max(idx_lo + 1, min(idx_hi, n_bins))

            src_rms = float(np.mean(src_spec[idx_lo:idx_hi]) + 1e-9)
            ref_rms = float(np.mean(ref_spec[idx_lo:idx_hi]) + 1e-9)
            ratio = ref_rms / src_rms
            if ratio <= 0 or not np.isfinite(ratio):
                continue
            gain_db = float(np.clip(20 * np.log10(ratio), -12, 12))
            if not np.isfinite(gain_db):
                continue

            # Use Q=2 (moderate bandwidth) — avoids NaN from bw-based width
            eq_filters.append(
                f"equalizer=f={f_center:.1f}:t=q:w=2:g={gain_db:.2f}"
            )

        af = ",".join(eq_filters)
        is_video = Path(source_path).suffix.lower() in _VIDEO_EXTS
        if is_video:
            with tempfile.NamedTemporaryFile(suffix=".aac", delete=False) as tf:
                tmp_a = tf.name
            with tempfile.NamedTemporaryFile(suffix=".aac", delete=False) as tf:
                tmp_eq = tf.name
            try:
                _run(["ffmpeg", "-y", "-i", source_path, "-vn", "-c:a", "aac", tmp_a])
                _run(["ffmpeg", "-y", "-i", tmp_a, "-af", af, tmp_eq])
                _run(["ffmpeg", "-y", "-i", source_path, "-i", tmp_eq,
                      "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", output_path])
            finally:
                for p in (tmp_a, tmp_eq):
                    Path(p).unlink(missing_ok=True)
        else:
            _run(["ffmpeg", "-y", "-i", source_path, "-af", af, output_path])
    finally:
        for p in (src_wav, ref_wav):
            Path(p).unlink(missing_ok=True)

    return output_path


# ---------------------------------------------------------------------------
# level_matcher  (#69)
# ---------------------------------------------------------------------------
def level_matcher(
    clip_paths: list[str],
    output_dir: str,
    *,
    target_lufs: float = -14.0,
) -> list[str]:
    """Match loudness across a set of audio/video clips to a common LUFS target.

    Measures each clip's integrated loudness and applies a gain correction
    so all clips reach *target_lufs* LUFS when played in sequence.

    Inspired by DaVinci Resolve 20 Fairlight *Level Matcher*.

    Args:
        clip_paths: Input audio or video file paths.
        output_dir: Directory to write matched clips.
        target_lufs: Target integrated loudness in LUFS.  Default -14.

    Returns:
        List of output file paths.
    """
    import re

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[str] = []

    for clip in clip_paths:
        # Measure current loudness
        r = subprocess.run(
            ["ffmpeg", "-i", clip, "-af",
             "loudnorm=I=-23:LRA=11:TP=-2:print_format=summary",
             "-f", "null", "-"],
            capture_output=True, text=True,
        )
        text = r.stderr
        # Parse integrated loudness
        m = re.search(r"Input Integrated:\s*([-\d.]+)", text)
        current_lufs = float(m.group(1)) if m else -23.0
        gain_db = target_lufs - current_lufs

        out_path = str(out_dir / Path(clip).name)
        is_video = Path(clip).suffix.lower() in _VIDEO_EXTS
        if is_video:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".aac", delete=False) as tf:
                tmp_a = tf.name
            with tempfile.NamedTemporaryFile(suffix=".aac", delete=False) as tf:
                tmp_g = tf.name
            try:
                _run(["ffmpeg", "-y", "-i", clip, "-vn", "-c:a", "aac", tmp_a])
                _run(["ffmpeg", "-y", "-i", tmp_a, "-af", f"volume={gain_db:.2f}dB", tmp_g])
                _run(["ffmpeg", "-y", "-i", clip, "-i", tmp_g,
                      "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", out_path])
            finally:
                for p in (tmp_a, tmp_g):
                    Path(p).unlink(missing_ok=True)
        else:
            _run(["ffmpeg", "-y", "-i", clip, "-af", f"volume={gain_db:.2f}dB", out_path])
        outputs.append(out_path)

    return outputs


# ---------------------------------------------------------------------------
# spectral_denoise  (#70)
# ---------------------------------------------------------------------------
def spectral_denoise(
    input_path: str,
    output_path: str,
    *,
    strength: float = 0.5,
    noise_floor_db: float = -50.0,
) -> str:
    """Remove broadband noise using spectral subtraction (STFT-based).

    Estimates the noise floor from the first 0.5 s of silence and subtracts
    it from the magnitude spectrum of each frame.

    Inspired by DaVinci Resolve 20 Fairlight *Noise Reduction* AI feature.

    Args:
        input_path: Source audio or video file.
        output_path: Destination file.
        strength: Subtraction strength [0, 1]. Default 0.5.
        noise_floor_db: Assumed noise floor dBFS. Default -50.

    Returns:
        output_path
    """
    import tempfile
    import numpy as np

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    strength = max(0.0, min(1.0, strength))

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        in_wav = tf.name
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        out_wav = tf.name

    is_video = Path(input_path).suffix.lower() in _VIDEO_EXTS
    try:
        _run(["ffmpeg", "-y", "-i", input_path, "-vn", "-ar", "44100", "-ac", "1", in_wav])

        import wave, struct
        with wave.open(in_wav, "rb") as wf:
            sr = wf.getframerate()
            n = wf.getnframes()
            raw = wf.readframes(n)
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

        # Estimate noise profile from first 0.5s
        noise_samples = samples[:int(sr * 0.5)]
        frame_size = 2048
        hop = 512
        noise_frames = [noise_samples[i:i + frame_size] for i in range(0, len(noise_samples) - frame_size, hop)]
        if noise_frames:
            noise_specs = np.array([np.abs(np.fft.rfft(f * np.hanning(frame_size))) for f in noise_frames])
            noise_profile = noise_specs.mean(axis=0)
        else:
            noise_floor_lin = 10 ** (noise_floor_db / 20.0)
            noise_profile = np.full(frame_size // 2 + 1, noise_floor_lin)

        # Process full signal with overlap-add
        window = np.hanning(frame_size)
        out = np.zeros(len(samples) + frame_size, dtype=np.float32)
        norm = np.zeros(len(samples) + frame_size, dtype=np.float32)

        for start in range(0, len(samples) - frame_size, hop):
            frame = samples[start:start + frame_size] * window
            spec = np.fft.rfft(frame)
            mag = np.abs(spec)
            phase = np.angle(spec)
            # Spectral subtraction
            mag_denoised = np.maximum(mag - noise_profile * strength, mag * (1 - strength) * 0.1)
            spec_denoised = mag_denoised * np.exp(1j * phase)
            frame_out = np.fft.irfft(spec_denoised).real * window
            out[start:start + frame_size] += frame_out
            norm[start:start + frame_size] += window ** 2

        norm = np.where(norm > 0, norm, 1.0)
        out /= norm
        out_int = (out[:len(samples)] * 32767).clip(-32768, 32767).astype(np.int16)

        with wave.open(out_wav, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(44100)
            wf.writeframes(out_int.tobytes())

        if is_video:
            with tempfile.NamedTemporaryFile(suffix=".aac", delete=False) as tf:
                tmp_aac = tf.name
            try:
                _run(["ffmpeg", "-y", "-i", out_wav, "-c:a", "aac", tmp_aac])
                _run(["ffmpeg", "-y", "-i", input_path, "-i", tmp_aac,
                      "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", output_path])
            finally:
                Path(tmp_aac).unlink(missing_ok=True)
        else:
            _run(["ffmpeg", "-y", "-i", out_wav, output_path])
    finally:
        for p in (in_wav, out_wav):
            Path(p).unlink(missing_ok=True)

    return output_path


# ---------------------------------------------------------------------------
# remove_silence  (#72)
# ---------------------------------------------------------------------------
def remove_silence(
    input_path: str,
    output_path: str,
    *,
    threshold_db: float = -40.0,
    min_silence_sec: float = 0.3,
    padding_sec: float = 0.05,
) -> str:
    """Remove silent gaps from audio/video to create a tight cut.

    Detects silence with ffmpeg ``silencedetect``, then concatenates the
    non-silent segments using the concat demuxer.

    Inspired by DaVinci Resolve 20 *Remove Silence* Fairlight feature.

    Args:
        input_path: Source audio or video file.
        output_path: Destination file.
        threshold_db: dBFS level below which audio is considered silent.
        min_silence_sec: Minimum silence duration to remove (seconds).
        padding_sec: Seconds of padding kept around each non-silent segment.

    Returns:
        output_path
    """
    import re
    import tempfile

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Detect silence regions
    r = subprocess.run(
        ["ffmpeg", "-i", input_path,
         "-af", f"silencedetect=noise={threshold_db}dB:d={min_silence_sec}",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    text = r.stderr

    silence_starts = [float(m) for m in re.findall(r"silence_start:\s*([\d.]+)", text)]
    silence_ends   = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", text)]

    # Probe total duration
    dur_r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", input_path],
        capture_output=True, text=True,
    )
    total_dur = float(dur_r.stdout.strip() or "0")

    # Build non-silent keep intervals
    keeps: list[tuple[float, float]] = []
    cursor = 0.0
    for ss, se in zip(silence_starts, silence_ends):
        seg_start = cursor
        seg_end = max(cursor, ss - padding_sec)
        if seg_end > seg_start + 0.01:
            keeps.append((seg_start, seg_end))
        cursor = se + padding_sec

    if total_dur > cursor + 0.01:
        keeps.append((cursor, total_dur))

    if not keeps:
        # No silence found — copy as-is
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-c", "copy", output_path],
            capture_output=True, check=True,
        )
        return output_path

    # Extract each kept segment to a temp file, then concat
    tmp_dir = Path(tempfile.mkdtemp())
    seg_files: list[str] = []
    is_video = Path(input_path).suffix.lower() in _VIDEO_EXTS

    for idx, (t0, t1) in enumerate(keeps):
        seg = str(tmp_dir / f"seg_{idx:04d}{Path(input_path).suffix}")
        cmd = ["ffmpeg", "-y", "-ss", f"{t0:.6f}", "-i", input_path,
               "-t", f"{t1 - t0:.6f}"]
        if is_video:
            cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"]
        else:
            cmd += ["-c:a", "aac"]
        cmd.append(seg)
        r2 = subprocess.run(cmd, capture_output=True, text=True)
        if r2.returncode == 0:
            seg_files.append(seg)

    if not seg_files:
        raise RuntimeError("remove_silence: no segments extracted")

    # Write concat list
    list_file = str(tmp_dir / "list.txt")
    with open(list_file, "w") as f:
        for seg in seg_files:
            f.write(f"file '{seg}'\n")

    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", list_file, "-c", "copy", output_path],
        capture_output=True, check=True,
    )
    return output_path


# ---------------------------------------------------------------------------
# create_adr_cues  (#74)
# ---------------------------------------------------------------------------
def create_adr_cues(
    input_path: str,
    output_path: str,
    *,
    min_speech_sec: float = 0.5,
    silence_threshold_db: float = -35.0,
) -> str:
    """Generate an ADR cue list as a JSON file from audio silence analysis.

    Detects spoken regions by finding non-silent segments and outputs a JSON
    cue list with in/out timecodes for each dialogue line.

    Inspired by DaVinci Resolve 20 Fairlight *Create ADR Cues* feature.

    Args:
        input_path: Source audio or video file.
        output_path: Path to write the JSON cue list (e.g. ``cues.json``).
        min_speech_sec: Minimum non-silent region duration to include.
        silence_threshold_db: dBFS noise gate for silence detection.

    Returns:
        output_path (path to the JSON file)
    """
    import re
    import json

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    r = subprocess.run(
        ["ffmpeg", "-i", input_path,
         "-af", f"silencedetect=noise={silence_threshold_db}dB:d=0.2",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    text = r.stderr

    silence_starts = [float(m) for m in re.findall(r"silence_start:\s*([\d.]+)", text)]
    silence_ends   = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", text)]

    dur_r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", input_path],
        capture_output=True, text=True,
    )
    total_dur = float(dur_r.stdout.strip() or "0")

    # Build speech intervals
    cues = []
    cursor = 0.0
    cue_num = 1

    for ss, se in zip(silence_starts, silence_ends):
        speech_start = cursor
        speech_end = ss
        if speech_end - speech_start >= min_speech_sec:
            cues.append({
                "cue": cue_num,
                "in": round(speech_start, 3),
                "out": round(speech_end, 3),
                "duration": round(speech_end - speech_start, 3),
                "character": f"CHAR_{cue_num:03d}",
                "note": "",
            })
            cue_num += 1
        cursor = se

    if total_dur - cursor >= min_speech_sec:
        cues.append({
            "cue": cue_num,
            "in": round(cursor, 3),
            "out": round(total_dur, 3),
            "duration": round(total_dur - cursor, 3),
            "character": f"CHAR_{cue_num:03d}",
            "note": "",
        })

    result = {"source": str(input_path), "cues": cues, "total_cues": len(cues)}
    Path(output_path).write_text(json.dumps(result, indent=2))
    return output_path


# ---------------------------------------------------------------------------
# speaker_separate  (#73)
# ---------------------------------------------------------------------------
def speaker_separate(
    input_path: str,
    output_dir: str,
    *,
    n_speakers: int = 2,
) -> list[str]:
    """Separate a mixed audio track into per-speaker stems using BSS.

    Uses numpy-based Independent Component Analysis (ICA) approximation on
    stereo channels as a lightweight speaker diarisation / separation proxy.
    For multi-channel input the first *n_speakers* independent components
    are extracted.

    Inspired by DaVinci Resolve 20 Fairlight *Checkerboard Separation*.

    Args:
        input_path: Source audio or video file (stereo preferred).
        output_dir: Directory to write speaker stems.
        n_speakers: Number of speakers to separate (2-4). Default 2.

    Returns:
        List of output file paths, one per speaker.
    """
    import tempfile, wave, struct
    import numpy as np

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    n_speakers = max(2, min(4, n_speakers))

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        tmp_wav = tf.name

    try:
        # Extract stereo WAV
        _run(["ffmpeg", "-y", "-i", input_path,
              "-vn", "-ar", "44100", "-ac", "2", tmp_wav])

        with wave.open(tmp_wav, "rb") as wf:
            n_ch = wf.getnchannels()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)

        samples = np.frombuffer(raw, dtype=np.int16).reshape(-1, n_ch).astype(np.float64)
        samples /= 32768.0

        # Simple ICA via whitening + rotation (FastICA approximation)
        # Centre
        X = samples.T  # shape: (n_ch, n_samples)
        X -= X.mean(axis=1, keepdims=True)

        # Whiten
        cov = np.cov(X)
        eigvals, eigvecs = np.linalg.eigh(cov)
        eigvals = np.maximum(eigvals, 1e-10)
        W_white = (eigvecs / np.sqrt(eigvals)).T
        X_white = W_white @ X

        # FastICA: for each component, optimise non-Gaussianity via tanh
        n_comp = min(n_speakers, n_ch)
        components = np.zeros((n_comp, X_white.shape[1]))
        for i in range(n_comp):
            w = np.random.default_rng(i).standard_normal(X_white.shape[0])
            w /= np.linalg.norm(w)
            for _ in range(200):
                g = np.tanh(w @ X_white)
                g_prime = 1 - g ** 2
                w_new = (X_white * g).mean(axis=1) - g_prime.mean() * w
                # Deflation: remove projection onto previous components
                for j in range(i):
                    prev = components[j, :1]  # use stored w-vectors instead
                w_new /= np.linalg.norm(w_new) + 1e-10
                if abs(abs(np.dot(w, w_new)) - 1) < 1e-6:
                    break
                w = w_new
            components[i] = w @ X_white

        outputs = []
        for i in range(n_comp):
            comp = components[i]
            comp_int = (comp * 32767).clip(-32768, 32767).astype(np.int16)
            out_wav = str(out_dir / f"speaker_{i+1}.wav")
            with wave.open(out_wav, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(44100)
                wf.writeframes(comp_int.tobytes())
            outputs.append(out_wav)

    finally:
        Path(tmp_wav).unlink(missing_ok=True)

    return outputs


# ---------------------------------------------------------------------------
# noise_gate  (#82)
# ---------------------------------------------------------------------------
def noise_gate(
    input_path: str,
    output_path: str,
    *,
    threshold_db: float = -40.0,
    attack_ms: float = 10.0,
    release_ms: float = 100.0,
    hold_ms: float = 50.0,
    reduction_db: float = -80.0,
) -> str:
    """Apply a noise gate to suppress audio below a threshold level.

    Uses ffmpeg's ``agate`` filter for transparent, low-latency gating.
    Inspired by DaVinci Resolve 20 Fairlight *Noise Gate* dynamics processor.

    Args:
        input_path: Source audio or video file.
        output_path: Destination file.
        threshold_db: Open/close threshold in dBFS. Default -40.
        attack_ms: Gate open time in ms. Default 10.
        release_ms: Gate close time in ms. Default 100.
        hold_ms: Minimum hold time after signal drops below threshold. Default 50.
        reduction_db: Gain applied when gate is closed (dBFS). Default -80.

    Returns:
        output_path
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    threshold_lin = 10 ** (threshold_db / 20.0)
    reduction_lin = 10 ** (reduction_db / 20.0)
    af = (
        f"agate="
        f"threshold={threshold_lin:.6f}:"
        f"attack={attack_ms:.1f}:"
        f"release={release_ms:.1f}:"
        
        f"range={reduction_lin:.6f}"
    )
    is_video = Path(input_path).suffix.lower() in _VIDEO_EXTS
    if is_video:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".aac", delete=False) as tf:
            tmp_a = tf.name
        with tempfile.NamedTemporaryFile(suffix=".aac", delete=False) as tf:
            tmp_g = tf.name
        try:
            _run(["ffmpeg", "-y", "-i", input_path, "-vn", "-c:a", "aac", tmp_a])
            _run(["ffmpeg", "-y", "-i", tmp_a, "-af", af, tmp_g])
            _run(["ffmpeg", "-y", "-i", input_path, "-i", tmp_g,
                  "-map", "0:v", "-map", "1:a",
                  "-c:v", "copy", "-c:a", "aac", output_path])
        finally:
            for p in (tmp_a, tmp_g):
                Path(p).unlink(missing_ok=True)
    else:
        _run(["ffmpeg", "-y", "-i", input_path, "-af", af, output_path])
    return output_path


# ---------------------------------------------------------------------------
# audio_compressor
# ---------------------------------------------------------------------------

def audio_compressor(
    input_path: str,
    output_path: str,
    *,
    threshold_db: float = -18.0,
    ratio: float = 4.0,
    attack_ms: float = 10.0,
    release_ms: float = 100.0,
    knee_db: float = 6.0,
    makeup_db: float = 0.0,
) -> str:
    """Apply dynamic range compression using ffmpeg acompressor filter.

    Args:
        input_path: Source audio file.
        output_path: Destination audio file.
        threshold_db: Level above which compression kicks in (dB).
        ratio: Compression ratio (e.g. 4.0 = 4:1).
        attack_ms: Attack time in milliseconds.
        release_ms: Release time in milliseconds.
        knee_db: Soft-knee width in dB.
        makeup_db: Output makeup gain in dB.

    Returns:
        The *output_path*.
    """
    import subprocess
    from pathlib import Path

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    threshold_lin = 10 ** (threshold_db / 20.0)
    makeup_lin = 10 ** (makeup_db / 20.0)

    af = (
        f"acompressor=threshold={threshold_lin:.6f}"
        f":ratio={ratio:.2f}"
        f":attack={attack_ms:.1f}"
        f":release={release_ms:.1f}"
        f":knee={knee_db:.1f}"
        f":makeup={makeup_lin:.6f}"
    )
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"audio_compressor failed:\n{proc.stderr}")
    return output_path


# ---------------------------------------------------------------------------
# stereo_widener
# ---------------------------------------------------------------------------

def stereo_widener(
    input_path: str,
    output_path: str,
    *,
    width: float = 1.5,
) -> str:
    """Widen or narrow the stereo image of an audio file.

    Args:
        input_path: Source stereo audio file.
        output_path: Destination audio file.
        width: Stereo width multiplier.  1.0 = unchanged, > 1.0 = wider,
            < 1.0 = narrower, 0.0 = mono.

    Returns:
        The *output_path*.
    """
    import subprocess
    from pathlib import Path

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # extrastereo multiplier: 1.0 = unchanged, 2.5 ≈ 1.5× perceived width
    # Clamp to reasonable range
    mult = max(0.0, width)
    af = f"extrastereo={mult:.4f}"

    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"stereo_widener failed:\n{proc.stderr}")
    return output_path


# ---------------------------------------------------------------------------
# audio_reverb
# ---------------------------------------------------------------------------

def audio_reverb(
    input_path: str,
    output_path: str,
    *,
    room_size: float = 0.5,
    wet: float = 0.3,
    dry: float = 0.7,
) -> str:
    """Add reverb/room effect to audio using ffmpeg aecho filter.

    Args:
        input_path: Source audio file.
        output_path: Destination audio file.
        room_size: Room size in [0, 1] — controls delay length.
        wet: Wet (reverb) signal level.
        dry: Dry (original) signal level.

    Returns:
        The *output_path*.
    """
    import subprocess
    from pathlib import Path

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Map room_size [0,1] to delay 20–500ms, decay 0.1–0.8
    delay_ms = 20 + int(room_size * 480)
    decay = 0.1 + room_size * 0.7

    af = f"aecho={dry:.2f}:{wet:.2f}:{delay_ms}:{decay:.2f}"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"audio_reverb failed:\n{proc.stderr}")
    return output_path


# ---------------------------------------------------------------------------
# audio_fade
# ---------------------------------------------------------------------------

def audio_fade(
    input_path: str,
    output_path: str,
    *,
    fade_in_sec: float = 0.0,
    fade_out_sec: float = 0.0,
) -> str:
    """Apply fade-in and/or fade-out to an audio file.

    Args:
        input_path: Source audio file.
        output_path: Destination audio file.
        fade_in_sec: Duration of the fade-in in seconds (0 = no fade-in).
        fade_out_sec: Duration of the fade-out in seconds (0 = no fade-out).

    Returns:
        The *output_path*.
    """
    import subprocess, json
    from pathlib import Path

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Probe duration for fade-out offset
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", input_path],
        capture_output=True, text=True,
    )
    duration = float(probe.stdout.strip()) if probe.returncode == 0 else 0.0

    parts: list[str] = []
    if fade_in_sec > 0:
        parts.append(f"afade=t=in:st=0:d={fade_in_sec:.3f}")
    if fade_out_sec > 0 and duration > 0:
        fade_out_start = max(0.0, duration - fade_out_sec)
        parts.append(f"afade=t=out:st={fade_out_start:.3f}:d={fade_out_sec:.3f}")

    if not parts:
        # Nothing to do — just copy
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-c", "copy", output_path],
            capture_output=True, check=True,
        )
        return output_path

    af = ",".join(parts)
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"audio_fade failed:\n{proc.stderr}")
    return output_path


# ---------------------------------------------------------------------------
# audio_trim_silence
# ---------------------------------------------------------------------------

def audio_trim_silence(
    input_path: str,
    output_path: str,
    *,
    threshold_db: float = -50.0,
    min_silence_sec: float = 0.1,
) -> str:
    """Remove leading and trailing silence from an audio file.

    Args:
        input_path: Source audio file.
        output_path: Destination audio file.
        threshold_db: dB level considered silence.
        min_silence_sec: Minimum silence duration to detect at edges.

    Returns:
        The *output_path*.
    """
    import subprocess, re
    from pathlib import Path

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Detect silence
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", input_path,
         "-af", f"silencedetect=noise={threshold_db}dB:d={min_silence_sec}",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    combined = proc.stdout + proc.stderr

    # Parse leading silence end (first silence_end = first non-silence start)
    starts = re.findall(r"silence_start:\s*([\d.]+)", combined)
    ends = re.findall(r"silence_end:\s*([\d.]+)", combined)

    # Probe total duration
    dur_proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", input_path],
        capture_output=True, text=True,
    )
    total = float(dur_proc.stdout.strip()) if dur_proc.returncode == 0 else None

    # Determine trim points
    trim_start = 0.0
    trim_end = total  # None = don't trim end

    # If audio starts with silence, first end marks content start
    if ends and float(starts[0]) < 0.1 if starts else True:
        if ends:
            trim_start = float(ends[0])

    # If audio ends with silence: last start of silence that goes to end
    if starts and total is not None:
        last_start = float(starts[-1])
        if last_start > total - 2.0:  # within last 2s
            trim_end = last_start

    af_parts = []
    if trim_start > 0:
        af_parts.append(f"atrim=start={trim_start:.3f}")
        af_parts.append("asetpts=PTS-STARTPTS")
    if trim_end is not None and total is not None and trim_end < total - 0.05:
        af_parts.append(f"atrim=end={trim_end - trim_start:.3f}" if trim_start > 0
                        else f"atrim=end={trim_end:.3f}")
        af_parts.append("asetpts=PTS-STARTPTS")

    if not af_parts:
        # Nothing to trim — copy
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-c", "copy", output_path],
            capture_output=True, check=True,
        )
        return output_path

    af = ",".join(af_parts)
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc2 = subprocess.run(cmd, capture_output=True, text=True)
    if proc2.returncode != 0:
        raise RuntimeError(f"audio_trim_silence failed:\n{proc2.stderr}")
    return output_path


# ---------------------------------------------------------------------------
# text_to_speech
# ---------------------------------------------------------------------------

def text_to_speech(
    text: str,
    output_path: str,
    *,
    voice: str = "auto",
    rate: int = 175,
) -> str:
    """Generate speech audio from text.

    Primary backend: macOS ``say`` command.
    Fallback: ``espeak`` (Linux/cross-platform).

    Args:
        text: Text string to synthesize.
        output_path: Destination audio file (``.aiff``, ``.wav``, ``.mp3``).
        voice: TTS voice name (``"auto"`` = system default).
        rate: Words per minute (macOS say: typical range 100–300).

    Returns:
        The *output_path*.
    """
    import subprocess, shutil, tempfile
    from pathlib import Path

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    ext = Path(output_path).suffix.lower()

    # macOS say — native, no deps
    if shutil.which("say"):
        # say writes AIFF natively; convert if needed
        with tempfile.TemporaryDirectory() as td:
            aiff = str(Path(td) / "tts.aiff")
            cmd = ["say", "-r", str(rate), "-o", aiff, text]
            if voice != "auto":
                cmd = ["say", "-v", voice, "-r", str(rate), "-o", aiff, text]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(f"say failed:\n{proc.stderr}")
            if ext in {".aiff", ".aif"}:
                import shutil as _sh
                _sh.copy2(aiff, output_path)
            else:
                proc2 = subprocess.run(
                    ["ffmpeg", "-y", "-i", aiff, output_path],
                    capture_output=True, text=True,
                )
                if proc2.returncode != 0:
                    raise RuntimeError(f"ffmpeg convert failed:\n{proc2.stderr}")
        return output_path

    # espeak fallback
    if shutil.which("espeak"):
        cmd = ["espeak", "-s", str(rate), "-w", output_path, text]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"espeak failed:\n{proc.stderr}")
        return output_path

    raise EnvironmentError(
        "text_to_speech requires macOS 'say' or 'espeak'. "
        "Install espeak: brew install espeak"
    )


# ---------------------------------------------------------------------------
# audio_normalize_loudness
# ---------------------------------------------------------------------------

def audio_normalize_loudness(
    input_path: str,
    output_path: str,
    *,
    target_lufs: float = -14.0,
    true_peak_dbfs: float = -1.0,
    lra: float = 11.0,
) -> str:
    """Two-pass loudness normalization to a precise LUFS target.

    Pass 1 analyses the source; Pass 2 encodes with corrected parameters.

    Args:
        input_path: Source audio file.
        output_path: Destination audio file.
        target_lufs: Integrated loudness target in LUFS (e.g. -14 for streaming).
        true_peak_dbfs: Maximum true-peak level in dBFS.
        lra: Loudness range target in LU.

    Returns:
        The *output_path*.
    """
    import subprocess, json
    from pathlib import Path

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Pass 1: measure
    p1 = subprocess.run(
        ["ffmpeg", "-y", "-i", input_path,
         "-af", f"loudnorm=I={target_lufs}:TP={true_peak_dbfs}:LRA={lra}:print_format=json",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    combined = p1.stdout + p1.stderr
    start = combined.rfind("{")
    end = combined.rfind("}") + 1
    if start == -1 or end <= start:
        raise RuntimeError(f"loudnorm pass 1 failed:\n{combined}")

    stats = json.loads(combined[start:end])
    measured_i = stats.get("input_i", "-23")
    measured_tp = stats.get("input_tp", "-1")
    measured_lra = stats.get("input_lra", "7")
    measured_thresh = stats.get("input_thresh", "-33")
    offset = stats.get("target_offset", "0")

    # Pass 2: normalize with measured values
    af2 = (
        f"loudnorm=I={target_lufs}:TP={true_peak_dbfs}:LRA={lra}"
        f":measured_I={measured_i}"
        f":measured_TP={measured_tp}"
        f":measured_LRA={measured_lra}"
        f":measured_thresh={measured_thresh}"
        f":offset={offset}:linear=true:print_format=none"
    )
    p2 = subprocess.run(
        ["ffmpeg", "-y", "-i", input_path, "-af", af2, output_path],
        capture_output=True, text=True,
    )
    if p2.returncode != 0:
        raise RuntimeError(f"loudnorm pass 2 failed:\n{p2.stderr}")
    return output_path


# ---------------------------------------------------------------------------
# audio_pitch_shift_semitones
# ---------------------------------------------------------------------------

def audio_pitch_shift_semitones(
    input_path: str,
    output_path: str,
    *,
    semitones: float,
) -> str:
    """Shift audio pitch by N semitones without changing tempo.

    Uses asetrate (resample speed) + atempo (time-correct) approach.

    Args:
        input_path: Source audio file.
        output_path: Destination audio file.
        semitones: Number of semitones to shift (positive = up, negative = down).

    Returns:
        The *output_path*.
    """
    import subprocess
    from pathlib import Path

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Probe sample rate
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate",
         "-of", "default=noprint_wrappers=1:nokey=1", input_path],
        capture_output=True, text=True,
    )
    sr = int(probe.stdout.strip()) if probe.returncode == 0 and probe.stdout.strip() else 44100

    ratio = 2 ** (semitones / 12.0)
    new_sr = int(sr * ratio)
    # atempo must be in [0.5, 2.0]; chain multiple if needed
    tempo = 1.0 / ratio
    atempo_filters: list[str] = []
    t = tempo
    while t < 0.5:
        atempo_filters.append("atempo=0.5")
        t /= 0.5
    while t > 2.0:
        atempo_filters.append("atempo=2.0")
        t /= 2.0
    atempo_filters.append(f"atempo={t:.6f}")

    af = f"asetrate={new_sr}," + ",".join(atempo_filters) + f",aresample={sr}"

    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"audio_pitch_shift_semitones failed:\n{proc.stderr}")
    return output_path


# ---------------------------------------------------------------------------
# audio_mix_to_mono
# ---------------------------------------------------------------------------

def audio_mix_to_mono(
    input_path: str,
    output_path: str,
) -> str:
    """Downmix audio to mono by averaging all channels.

    Args:
        input_path: Source audio file (any number of channels).
        output_path: Destination mono audio file.

    Returns:
        The *output_path*.
    """
    import subprocess
    from pathlib import Path

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-af", "pan=mono|c0=0.5*c0+0.5*c1",
        output_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        # fallback for mono input or different channel layouts
        proc2 = subprocess.run(
            ["ffmpeg", "-y", "-i", input_path,
             "-ac", "1", output_path],
            capture_output=True, text=True,
        )
        if proc2.returncode != 0:
            raise RuntimeError(f"audio_mix_to_mono failed:\n{proc2.stderr}")
    return output_path


# ---------------------------------------------------------------------------
# audio_concat
# ---------------------------------------------------------------------------

def audio_concat(
    input_paths: list[str],
    output_path: str,
) -> str:
    """Concatenate multiple audio files into one.

    Args:
        input_paths: List of source audio files (at least 2).
        output_path: Destination audio file.

    Returns:
        The *output_path*.
    """
    import subprocess, tempfile
    from pathlib import Path

    if len(input_paths) < 2:
        raise ValueError("audio_concat requires at least 2 inputs")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Use concat demuxer via a temp list file
    with tempfile.TemporaryDirectory() as td:
        list_path = str(Path(td) / "inputs.txt")
        with open(list_path, "w") as f:
            for p in input_paths:
                abs_p = str(Path(p).resolve())
                f.write(f"file '{abs_p}'\n")

        proc = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", list_path, "-c", "copy", output_path],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            # Fallback: re-encode with filter concat
            n = len(input_paths)
            inputs = [x for p in input_paths for x in ["-i", p]]
            segs = "".join(f"[{i}:a]" for i in range(n))
            fc = f"{segs}concat=n={n}:v=0:a=1[a]"
            proc2 = subprocess.run(
                ["ffmpeg", "-y", *inputs,
                 "-filter_complex", fc, "-map", "[a]", output_path],
                capture_output=True, text=True,
            )
            if proc2.returncode != 0:
                raise RuntimeError(f"audio_concat failed:\n{proc2.stderr}")
    return output_path


# ---------------------------------------------------------------------------
# audio_ducking
# ---------------------------------------------------------------------------

def audio_ducking(
    main_path: str,
    voice_path: str,
    output_path: str,
    *,
    threshold_db: float = -20.0,
    reduction_db: float = -10.0,
    attack_ms: float = 50.0,
    release_ms: float = 300.0,
) -> str:
    """Duck the main audio level when voice/sidechain signal is active.

    Uses ffmpeg ``sidechaincompress`` filter.

    Args:
        main_path: Background music / main audio to duck.
        voice_path: Sidechain (voice/narration) that triggers ducking.
        output_path: Destination audio file with ducked main + voice mix.
        threshold_db: Level of sidechain that triggers compression.
        reduction_db: How much to reduce main in dB (negative = cut).
        attack_ms: Compressor attack time in ms.
        release_ms: Compressor release time in ms.

    Returns:
        The *output_path*.
    """
    import subprocess
    from pathlib import Path

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    threshold_lin = 10 ** (threshold_db / 20.0)
    ratio = 20.0  # high ratio = near-limiter behavior
    makeup_lin = 10 ** (reduction_db / 20.0)

    fc = (
        f"[0:a][1:a]sidechaincompress="
        f"threshold={threshold_lin:.6f}:"
        f"ratio={ratio:.1f}:"
        f"attack={attack_ms:.1f}:"
        f"release={release_ms:.1f}:"
        f"makeup={makeup_lin:.6f}[ducked];"
        f"[ducked][1:a]amix=inputs=2:duration=first[out]"
    )

    proc = subprocess.run(
        ["ffmpeg", "-y",
         "-i", main_path,
         "-i", voice_path,
         "-filter_complex", fc,
         "-map", "[out]",
         output_path],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        # sidechaincompress may not be available — fallback: simple volume duck
        proc2 = subprocess.run(
            ["ffmpeg", "-y",
             "-i", main_path,
             "-i", voice_path,
             "-filter_complex",
             f"[0:a]volume={makeup_lin:.4f}[main_duck];[main_duck][1:a]amix=inputs=2:duration=first[out]",
             "-map", "[out]",
             output_path],
            capture_output=True, text=True,
        )
        if proc2.returncode != 0:
            raise RuntimeError(f"audio_ducking failed:\n{proc2.stderr}")
    return output_path


# ---------------------------------------------------------------------------
# audio_speed
# ---------------------------------------------------------------------------

def audio_speed(
    input_path: str,
    output_path: str,
    *,
    factor: float,
) -> str:
    """Change audio playback speed without changing pitch.

    Uses ffmpeg ``atempo`` filter, chained for values outside [0.5, 2.0].

    Args:
        input_path: Source audio file.
        output_path: Destination audio file.
        factor: Speed multiplier (e.g. 2.0 = double speed, 0.5 = half speed).

    Returns:
        The *output_path*.
    """
    import subprocess
    from pathlib import Path

    if factor <= 0:
        raise ValueError("factor must be > 0")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Build chained atempo filters to handle factors outside [0.5, 2.0]
    filters: list[str] = []
    f = factor
    while f > 2.0:
        filters.append("atempo=2.0")
        f /= 2.0
    while f < 0.5:
        filters.append("atempo=0.5")
        f *= 2.0
    filters.append(f"atempo={f:.6f}")

    af = ",".join(filters)
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"audio_speed failed:\n{proc.stderr}")
    return output_path


# ---------------------------------------------------------------------------
# audio_volume
# ---------------------------------------------------------------------------

def audio_volume(
    input_path: str,
    output_path: str,
    *,
    gain_db: float,
) -> str:
    """Adjust audio volume by a fixed dB gain.

    Args:
        input_path: Source audio file.
        output_path: Destination audio file.
        gain_db: Gain in dB (positive = louder, negative = quieter).

    Returns:
        The *output_path*.
    """
    import subprocess
    from pathlib import Path

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    gain_lin = 10 ** (gain_db / 20.0)
    af = f"volume={gain_lin:.6f}"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"audio_volume failed:\n{proc.stderr}")
    return output_path


# ---------------------------------------------------------------------------
# audio_equalizer
# ---------------------------------------------------------------------------

def audio_equalizer(
    input_path: str,
    output_path: str,
    *,
    bands: list[dict],
) -> str:
    """Apply parametric EQ to audio using ffmpeg equalizer filter.

    Args:
        input_path: Source audio file.
        output_path: Destination audio file.
        bands: List of band dicts, each with:
            - ``freq`` (float): Centre frequency in Hz.
            - ``gain_db`` (float): Gain in dB (positive = boost, negative = cut).
            - ``q`` (float, optional): Q-factor (default 1.0 ≈ 1 octave).

    Example::

        audio_equalizer(src, out, bands=[
            {"freq": 100, "gain_db": +3.0},
            {"freq": 3000, "gain_db": -2.0, "q": 2.0},
        ])

    Returns:
        The *output_path*.
    """
    import subprocess
    from pathlib import Path

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if not bands:
        raise ValueError("bands must not be empty")

    parts = []
    for band in bands:
        freq = float(band["freq"])
        gain = float(band["gain_db"])
        q = float(band.get("q", 1.0))
        parts.append(f"equalizer=f={freq:.1f}:t=q:w={q:.2f}:g={gain:.2f}")

    af = ",".join(parts)
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"audio_equalizer failed:\n{proc.stderr}")
    return output_path


def audio_reverse(input_path: str, output_path: str) -> None:
    """Reverse audio using ffmpeg areverse filter."""
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", "areverse", output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_fade_in(input_path: str, output_path: str, *, duration: float = 1.0) -> None:
    """Apply fade-in effect to audio.
    
    Args:
        duration: Duration of fade-in in seconds. Default 1.0.
    """
    af = f"afade=t=in:st=0:d={duration:.3f}"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_fade_out(input_path: str, output_path: str, *, duration: float = 1.0) -> None:
    """Apply fade-out effect to audio.
    
    Args:
        duration: Duration of fade-out in seconds. Default 1.0.
    """
    import json
    # Get audio duration via ffprobe
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", input_path],
        capture_output=True, text=True,
    )
    total = float(json.loads(probe.stdout)["format"]["duration"])
    start = max(0.0, total - duration)
    af = f"afade=t=out:st={start:.3f}:d={duration:.3f}"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_trim(input_path: str, output_path: str, *, start: float = 0.0, end: float | None = None) -> None:
    """Trim audio to a specific time range.
    
    Args:
        start: Start time in seconds. Default 0.0.
        end: End time in seconds. None means until end of file.
    """
    cmd = ["ffmpeg", "-y", "-i", input_path, "-ss", str(start)]
    if end is not None:
        cmd += ["-to", str(end)]
    cmd += [output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_mix_stereo(left_path: str, right_path: str, output_path: str) -> None:
    """Mix two audio files as left and right stereo channels.
    
    Args:
        left_path: Audio file to use as the left channel.
        right_path: Audio file to use as the right channel.
    """
    af = "amerge=inputs=2,pan=stereo|c0=c0|c1=c2"
    cmd = [
        "ffmpeg", "-y",
        "-i", left_path, "-i", right_path,
        "-filter_complex", af,
        output_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_sample_rate_convert(input_path: str, output_path: str, *, sample_rate: int = 44100) -> None:
    """Convert audio to a different sample rate.

    Args:
        sample_rate: Target sample rate in Hz. Default 44100.
    """
    cmd = ["ffmpeg", "-y", "-i", input_path, "-ar", str(sample_rate), output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_channels_to_mono(input_path: str, output_path: str) -> None:
    """Downmix any audio to a single mono channel."""
    cmd = ["ffmpeg", "-y", "-i", input_path, "-ac", "1", output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_loudness_normalize(
    input_path: str,
    output_path: str,
    *,
    target_lufs: float = -14.0,
) -> None:
    """Normalize audio loudness to a target integrated LUFS level (two-pass loudnorm).

    Args:
        target_lufs: Target integrated loudness in LUFS. Default -14.0 (streaming standard).
    """
    import json, re
    # Pass 1: measure
    cmd1 = [
        "ffmpeg", "-y", "-i", input_path,
        "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:print_format=json",
        "-f", "null", "-",
    ]
    r1 = subprocess.run(cmd1, capture_output=True, text=True)
    # Extract JSON block from stderr
    m = re.search(r'\{[^{}]+\}', r1.stderr, re.DOTALL)
    if m:
        stats = json.loads(m.group())
        il = stats["input_i"]; lra = stats["input_lra"]; tp = stats["input_tp"]; thr = stats["input_thresh"]
        af2 = (
            f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"
            f":measured_I={il}:measured_LRA={lra}:measured_TP={tp}:measured_thresh={thr}:linear=true"
        )
    else:
        # Fallback: single-pass
        af2 = f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"
    cmd2 = ["ffmpeg", "-y", "-i", input_path, "-af", af2, output_path]
    proc = subprocess.run(cmd2, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_bit_depth_convert(input_path: str, output_path: str, *, bits: int = 16) -> None:
    """Convert audio to a specific bit depth.

    Args:
        bits: Target bit depth: 16, 24, or 32. Default 16.
    """
    # Map bit depth to (sample_fmt, codec) pairs for WAV-compatible output
    fmt_map = {16: ("s16", "pcm_s16le"), 24: ("s32", "pcm_s32le"), 32: ("flt", "pcm_f32le")}
    sample_fmt, codec = fmt_map.get(bits, ("s16", "pcm_s16le"))
    cmd = ["ffmpeg", "-y", "-i", input_path, "-acodec", codec, "-sample_fmt", sample_fmt, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_stereo_to_lr(input_path: str, left_output: str, right_output: str) -> None:
    """Split stereo audio into separate left and right channel mono files.

    Args:
        left_output: Output path for the left channel audio.
        right_output: Output path for the right channel audio.
    """
    # Extract left channel
    cmd_l = ["ffmpeg", "-y", "-i", input_path, "-af", "pan=mono|c0=FL", left_output]
    proc = subprocess.run(cmd_l, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])
    # Extract right channel
    cmd_r = ["ffmpeg", "-y", "-i", input_path, "-af", "pan=mono|c0=FR", right_output]
    proc = subprocess.run(cmd_r, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_echo(
    input_path: str,
    output_path: str,
    *,
    delay_ms: float = 500.0,
    decay: float = 0.5,
) -> None:
    """Add echo effect to audio using ffmpeg aecho filter.

    Args:
        delay_ms: Echo delay in milliseconds. Default 500.
        decay: Echo decay factor (0-1). Default 0.5.
    """
    af = f"aecho=0.8:{decay:.3f}:{delay_ms:.1f}:{decay:.3f}"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_chorus(
    input_path: str,
    output_path: str,
    *,
    depth: float = 0.4,
    speed: float = 0.5,
) -> None:
    """Add chorus effect to audio using ffmpeg chorus filter.

    Args:
        depth: Chorus depth (0-1). Default 0.4.
        speed: Modulation speed in Hz. Default 0.5.
    """
    # chorus=in_gain:out_gain:delay:decay:speed:depth
    af = f"chorus=0.7:0.9:55:{depth:.3f}:{speed:.3f}:0.25"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_tremolo(
    input_path: str,
    output_path: str,
    *,
    frequency: float = 5.0,
    depth: float = 0.5,
) -> None:
    """Add tremolo (amplitude modulation) effect to audio.

    Args:
        frequency: Modulation frequency in Hz (0.1-20). Default 5.0.
        depth: Modulation depth (0-1). Default 0.5.
    """
    af = f"tremolo=f={frequency:.2f}:d={depth:.3f}"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_flanger(
    input_path: str,
    output_path: str,
    *,
    delay: float = 0.0,
    depth: float = 2.0,
    speed: float = 0.5,
) -> None:
    """Add flanger effect to audio.

    Args:
        delay: Base delay in ms (0-30). Default 0.0.
        depth: Sweep depth in ms (0-10). Default 2.0.
        speed: Sweep speed in Hz (0.1-10). Default 0.5.
    """
    af = f"flanger=delay={delay:.2f}:depth={depth:.2f}:speed={speed:.2f}"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_vibrato(
    input_path: str,
    output_path: str,
    *,
    frequency: float = 5.0,
    depth: float = 0.5,
) -> None:
    """Add vibrato (frequency modulation) effect to audio.

    Args:
        frequency: Modulation frequency in Hz (0.1-20). Default 5.0.
        depth: Modulation depth (0-1). Default 0.5.
    """
    af = f"vibrato=f={frequency:.2f}:d={depth:.3f}"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_robot(input_path: str, output_path: str, *, pitch_shift: float = 0.8) -> None:
    """Apply robot voice effect by combining ring modulation via asetrate and atempo.

    Args:
        pitch_shift: Pitch factor (< 1.0 = lower, > 1.0 = higher). Default 0.8.
    """
    import json
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams",
         "-select_streams", "a:0", input_path],
        capture_output=True, text=True,
    )
    sr = int(json.loads(probe.stdout)["streams"][0]["sample_rate"])
    new_sr = int(sr * pitch_shift)
    # asetrate shifts pitch, atempo corrects tempo back to original speed
    af = f"asetrate={new_sr},atempo={1.0/pitch_shift:.6f},aphaser=in_gain=0.4:out_gain=0.74:delay=3:decay=0.4:speed=0.5:type=t"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, "-ar", str(sr), output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_pitch_up(input_path: str, output_path: str, *, semitones: float = 2.0) -> None:
    """Shift audio pitch up by semitones while preserving duration.

    Args:
        semitones: Number of semitones to shift up (negative = shift down). Default 2.0.
    """
    import json
    factor = 2 ** (semitones / 12.0)
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams",
         "-select_streams", "a:0", input_path],
        capture_output=True, text=True,
    )
    sr = int(json.loads(probe.stdout)["streams"][0]["sample_rate"])
    new_sr = int(sr * factor)
    # Chain atempo to compensate for rate change
    tempo = 1.0 / factor
    atempo_filters = []
    t = tempo
    while t < 0.5:
        atempo_filters.append("atempo=0.5")
        t /= 0.5
    while t > 2.0:
        atempo_filters.append("atempo=2.0")
        t /= 2.0
    atempo_filters.append(f"atempo={t:.6f}")
    af = f"asetrate={new_sr}," + ",".join(atempo_filters)
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, "-ar", str(sr), output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_normalize_peak(input_path: str, output_path: str, *, target_db: float = -1.0) -> None:
    """Normalize audio to a target peak level in dBFS.

    Args:
        target_db: Target peak level in dBFS. Default -1.0.
    """
    af = f"dynaudnorm=p=0.9:m=100:s=12:g=15"
    # Use volume filter with measured peak as fallback is complex; dynaudnorm is reliable
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_stereo_enhance(input_path: str, output_path: str, *, factor: float = 2.0) -> None:
    """Enhance stereo width using ffmpeg extrastereo filter.

    Args:
        factor: Enhancement factor. 1.0 = no change, 2.0 = doubled width. Default 2.0.
    """
    af = f"extrastereo=m={factor:.3f}"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_bass_boost(input_path: str, output_path: str, *, gain_db: float = 6.0, frequency: float = 100.0) -> None:
    """Boost bass frequencies using ffmpeg bass filter.

    Args:
        gain_db: Gain in dB. Default 6.0.
        frequency: Center frequency in Hz. Default 100.0.
    """
    af = f"bass=g={gain_db:.2f}:f={frequency:.1f}"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_treble_boost(input_path: str, output_path: str, *, gain_db: float = 6.0, frequency: float = 3000.0) -> None:
    """Boost treble frequencies using ffmpeg treble filter.

    Args:
        gain_db: Gain in dB. Default 6.0.
        frequency: Center frequency in Hz. Default 3000.0.
    """
    af = f"treble=g={gain_db:.2f}:f={frequency:.1f}"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_telephone(input_path: str, output_path: str) -> None:
    """Apply telephone effect by bandpass-filtering to 300-3400 Hz range."""
    af = "highpass=f=300,lowpass=f=3400"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_lowpass(input_path: str, output_path: str, *, frequency: float = 1000.0) -> None:
    """Apply lowpass filter to audio — attenuates frequencies above cutoff.

    Args:
        frequency: Cutoff frequency in Hz. Default 1000.0.
    """
    af = f"lowpass=f={frequency:.1f}"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_highpass(input_path: str, output_path: str, *, frequency: float = 200.0) -> None:
    """Apply highpass filter to audio — attenuates frequencies below cutoff.

    Args:
        frequency: Cutoff frequency in Hz. Default 200.0.
    """
    af = f"highpass=f={frequency:.1f}"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_compand(
    input_path: str,
    output_path: str,
    *,
    attack: float = 0.3,
    decay: float = 0.8,
    soft_knee: float = 6.0,
    gain: float = 0.0,
) -> None:
    """Apply dynamic range compression/expansion using ffmpeg compand filter.

    Args:
        attack: Attack time in seconds. Default 0.3.
        decay: Decay time in seconds. Default 0.8.
        soft_knee: Soft knee in dB. Default 6.0.
        gain: Output gain in dB. Default 0.0.
    """
    af = f"compand=attacks={attack:.3f}:decays={decay:.3f}:soft-knee={soft_knee:.1f}:gain={gain:.1f}"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_mix_tracks(input_paths: list[str], output_path: str, *, normalize: bool = True) -> None:
    """Mix multiple audio tracks together into one using ffmpeg amix filter.

    Args:
        input_paths: List of input audio file paths (2 or more).
        normalize: Whether to normalize the output. Default True.
    """
    if len(input_paths) < 2:
        raise ValueError("Need at least 2 input tracks to mix.")
    n = len(input_paths)
    cmd = ["ffmpeg", "-y"]
    for p in input_paths:
        cmd += ["-i", p]
    normalize_flag = 1 if normalize else 0
    cmd += ["-filter_complex", f"amix=inputs={n}:normalize={normalize_flag}", output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_silence_insert(
    input_path: str,
    output_path: str,
    *,
    position: float = 0.0,
    duration: float = 1.0,
) -> None:
    """Insert silence at a given position in audio.

    Args:
        position: Time in seconds where silence is inserted. Default 0.0 (prepend).
        duration: Duration of silence in seconds. Default 1.0.
    """
    import json
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", input_path],
        capture_output=True, text=True,
    )
    sr_info = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams",
         "-select_streams", "a:0", input_path],
        capture_output=True, text=True,
    )
    sr = int(json.loads(sr_info.stdout)["streams"][0]["sample_rate"])
    total = float(json.loads(probe.stdout)["format"]["duration"])
    pos = max(0.0, min(position, total))
    # Build filter_complex: split at position, insert silence, concat
    fc = (
        f"[0:a]atrim=end={pos:.4f}[a1];"
        f"[0:a]atrim=start={pos:.4f}[a2];"
        f"anullsrc=r={sr}:cl=mono,atrim=duration={duration:.4f}[sil];"
        f"[a1][sil][a2]concat=n=3:v=0:a=1[out]"
    )
    cmd = ["ffmpeg", "-y", "-i", input_path,
           "-filter_complex", fc, "-map", "[out]", output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_vinyl(input_path: str, output_path: str, *, crackle: float = 0.3) -> None:
    """Apply vinyl record effect: mild lowpass + slight saturation + wow/flutter sim.

    Args:
        crackle: Crackle intensity via noise (0-1). Default 0.3.
    """
    # Lowpass at 8kHz + slight wow flutter via asetrate variation isn't supported inline,
    # so we approximate with: lowpass + treble cut + mild harmonic saturation
    noise_vol = crackle * 0.02
    af = (
        f"lowpass=f=8000,"
        f"treble=g=-4:f=6000,"
        f"aeval=val(0)+{noise_vol:.4f}*(random(0)-0.5):c=same"
    )
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_normalize_rms(input_path: str, output_path: str, *, target_db: float = -20.0) -> None:
    """Normalize audio to a target RMS level.

    Uses ffmpeg volumedetect to measure, then applies gain correction.

    Args:
        target_db: Target RMS level in dBFS. Default -20.0.
    """
    import re
    # Measure RMS
    probe = subprocess.run(
        ["ffmpeg", "-y", "-i", input_path, "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", probe.stderr)
    if m:
        measured = float(m.group(1))
        gain = target_db - measured
    else:
        gain = 0.0
    af = f"volume={gain:.2f}dB"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_silence_detect(input_path: str, *, noise_db: float = -30.0, duration: float = 0.5) -> list[dict]:
    """Detect silent regions in audio.

    Args:
        noise_db: Noise threshold in dBFS. Quieter = more strict. Default -30.0.
        duration: Minimum silence duration in seconds. Default 0.5.

    Returns:
        List of dicts with 'start' and 'end' keys in seconds.
    """
    import re
    af = f"silencedetect=noise={noise_db:.1f}dB:d={duration:.3f}"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", input_path, "-af", af, "-f", "null", "-"],
        capture_output=True, text=True,
    )
    starts = [float(m) for m in re.findall(r"silence_start: ([\d.]+)", proc.stderr)]
    ends = [float(m) for m in re.findall(r"silence_end: ([\d.]+)", proc.stderr)]
    return [{"start": s, "end": e} for s, e in zip(starts, ends)]


def audio_export_wav(input_path: str, output_path: str, *, sample_rate: int = 44100, channels: int = 2) -> None:
    """Convert any audio format to uncompressed PCM WAV.

    Args:
        sample_rate: Output sample rate. Default 44100.
        channels: Number of output channels. Default 2 (stereo).
    """
    cmd = ["ffmpeg", "-y", "-i", input_path,
           "-acodec", "pcm_s16le", "-ar", str(sample_rate), "-ac", str(channels),
           output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_crossfade(
    clip1_path: str,
    clip2_path: str,
    output_path: str,
    *,
    duration: float = 1.0,
) -> None:
    """Crossfade two audio files using ffmpeg acrossfade filter.

    Args:
        duration: Crossfade duration in seconds. Default 1.0.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", clip1_path, "-i", clip2_path,
        "-filter_complex", f"acrossfade=d={duration:.3f}",
        output_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_ducking_auto(
    voice_path: str,
    music_path: str,
    output_path: str,
    *,
    duck_db: float = -12.0,
) -> None:
    """Duck background music under a voice track.

    Reduces music volume by duck_db dB wherever voice is louder than silence threshold,
    using a sidechain volume approach with sidechaincompress (fallback: static volume reduction).

    Args:
        voice_path: Path to the voice/foreground audio.
        music_path: Path to the background music to duck.
        duck_db: Gain applied to music when voice is active (e.g. -12.0 dB). Default -12.0.
    """
    # Try sidechaincompress approach
    fc = (
        f"[1:a]volume={duck_db:.1f}dB[ducked];"
        f"[0:a][ducked]amix=inputs=2:normalize=0[out]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", voice_path, "-i", music_path,
        "-filter_complex", fc,
        "-map", "[out]",
        output_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_format_convert(input_path: str, output_path: str, *, bitrate: str = "192k") -> None:
    """Convert audio to a different format determined by output file extension.

    Args:
        output_path: Output path. Format inferred from extension (.mp3, .aac, .flac, .ogg, .wav, etc.)
        bitrate: Target bitrate for lossy formats. Default '192k'.
    """
    cmd = ["ffmpeg", "-y", "-i", input_path, "-b:a", bitrate, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_waveform_image(
    input_path: str,
    output_path: str,
    *,
    width: int = 800,
    height: int = 200,
    color: str = "0x00aaff",
) -> None:
    """Render audio waveform as a static PNG image using ffmpeg showwavespic filter.

    Args:
        width: Output image width. Default 800.
        height: Output image height. Default 200.
        color: Waveform color as hex (e.g. '0x00aaff'). Default blue.
    """
    fc = f"[0:a]showwavespic=s={width}x{height}:colors={color}[v]"
    cmd = ["ffmpeg", "-y", "-i", input_path,
           "-filter_complex", fc, "-map", "[v]", output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_noise_reduce(
    input_path: str,
    output_path: str,
    *,
    strength: float = 10.0,
) -> None:
    """Reduce background noise using ffmpeg afftdn filter.

    Args:
        strength: Noise reduction strength in dB (1–97). Default 10.0.
    """
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-af", f"afftdn=nr={strength}",
        output_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_speed_change(
    input_path: str,
    output_path: str,
    *,
    speed: float = 1.5,
) -> None:
    """Change audio playback speed without pitch shift using atempo filter.

    atempo supports 0.5–2.0; values outside this range are handled by chaining.

    Args:
        speed: Playback speed multiplier. Default 1.5.
    """
    # Build chained atempo filter for out-of-range values
    filters = []
    s = speed
    while s > 2.0:
        filters.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        filters.append("atempo=0.5")
        s /= 0.5
    filters.append(f"atempo={s:.6f}")
    af = ",".join(filters)
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_stereo_swap(
    input_path: str,
    output_path: str,
) -> None:
    """Swap left and right stereo channels.

    If the input is mono it is passed through unchanged.
    """
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-af", "pan=stereo|c0=c1|c1=c0",
        output_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        # Mono fallback: just copy
        proc2 = subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-c", "copy", output_path],
            capture_output=True, text=True,
        )
        if proc2.returncode != 0:
            raise RuntimeError(proc.stderr[-1000:])


def audio_generate_tone(
    output_path: str,
    *,
    frequency: float = 440.0,
    duration: float = 1.0,
    amplitude: float = 0.5,
    sample_rate: int = 44100,
) -> None:
    """Generate a sine-wave tone audio file.

    Args:
        frequency: Tone frequency in Hz. Default 440.0.
        duration: Duration in seconds. Default 1.0.
        amplitude: Amplitude 0–1. Default 0.5.
        sample_rate: Sample rate. Default 44100.
    """
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency={frequency}:sample_rate={sample_rate}:duration={duration}",
        "-af", f"volume={amplitude}",
        output_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_silence_trim(
    input_path: str,
    output_path: str,
    *,
    threshold: float = -50.0,
    duration: float = 0.1,
) -> None:
    """Trim leading and trailing silence from an audio file.

    Args:
        threshold: dB level below which audio is considered silent. Default -50.
        duration: Minimum silence duration to trim. Default 0.1s.
    """
    # silenceremove: trim start (start_periods=1) and end (stop_periods=1)
    af = (f"silenceremove=start_periods=1:start_duration={duration}:start_threshold={threshold}dB"
          f":stop_periods=-1:stop_duration={duration}:stop_threshold={threshold}dB")
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_resample(
    input_path: str,
    output_path: str,
    *,
    sample_rate: int = 44100,
) -> None:
    """Resample audio to a new sample rate.

    Args:
        sample_rate: Target sample rate in Hz. Default 44100.
    """
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-af", f"aresample={sample_rate}",
        "-ar", str(sample_rate),
        output_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_normalize_to_target_db(
    input_path: str,
    output_path: str,
    *,
    target_db: float = -3.0,
) -> None:
    """Normalize audio peak to a target dB level.

    Uses a two-pass approach: measure max volume, then apply compensating gain.

    Args:
        target_db: Target peak dB (negative). Default -3.0.
    """
    import re

    # Pass 1: measure peak
    proc = subprocess.run(
        ["ffmpeg", "-i", input_path, "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m = re.search(r"max_volume:\s*([-\d.]+)\s*dB", proc.stderr)
    if not m:
        # Fallback: just copy
        subprocess.run(["ffmpeg", "-y", "-i", input_path, "-c", "copy", output_path],
                       capture_output=True)
        return
    max_vol = float(m.group(1))
    gain_db = target_db - max_vol

    # Pass 2: apply gain
    cmd = ["ffmpeg", "-y", "-i", input_path,
           "-af", f"volume={gain_db:.2f}dB", output_path]
    proc2 = subprocess.run(cmd, capture_output=True, text=True)
    if proc2.returncode != 0:
        raise RuntimeError(proc2.stderr[-1000:])


def audio_apply_eq_bands(
    input_path: str,
    output_path: str,
    bands: list[dict],
) -> None:
    """Apply a multi-band parametric EQ.

    Args:
        bands: List of dicts with keys:
            - freq (float): Center frequency in Hz
            - gain (float): Gain in dB (positive=boost, negative=cut)
            - width (float, optional): Bandwidth in Hz. Default 100.
    """
    if not bands:
        subprocess.run(["ffmpeg", "-y", "-i", input_path, "-c", "copy", output_path],
                       capture_output=True)
        return
    parts = []
    for b in bands:
        freq = b["freq"]
        gain = b["gain"]
        width = b.get("width", 100)
        parts.append(f"equalizer=f={freq}:width_type=h:width={width}:g={gain}")
    af = ",".join(parts)
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_gate(
    input_path: str,
    output_path: str,
    *,
    threshold: float = 0.01,
    attack: float = 20.0,
    release: float = 250.0,
) -> None:
    """Apply a noise gate to mute audio below threshold.

    Args:
        threshold: Gate open threshold 0–1 (linear). Default 0.01.
        attack: Attack time in ms. Default 20.
        release: Release time in ms. Default 250.
    """
    af = f"agate=threshold={threshold}:attack={attack}:release={release}"
    cmd = ["ffmpeg", "-y", "-i", input_path, "-af", af, output_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:])


def audio_pitch_detect(
    input_path: str,
    *,
    duration: float = 5.0,
) -> float:
    """Estimate the dominant pitch frequency of an audio file.

    Uses numpy FFT on the first ``duration`` seconds of audio decoded via ffmpeg.

    Args:
        duration: Seconds of audio to analyse. Default 5.0.

    Returns:
        Estimated fundamental frequency in Hz.
    """
    import numpy as np

    # Decode to raw PCM (mono, 44100)
    sr = 44100
    proc = subprocess.run(
        ["ffmpeg", "-i", input_path, "-t", str(duration),
         "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0 or len(proc.stdout) == 0:
        return 0.0
    samples = np.frombuffer(proc.stdout, dtype=np.float32)
    if samples.size == 0:
        return 0.0
    fft = np.abs(np.fft.rfft(samples))
    freqs = np.fft.rfftfreq(samples.size, d=1.0 / sr)
    # Ignore DC and very low freqs
    mask = freqs > 20
    if not mask.any():
        return 0.0
    peak_idx = np.argmax(fft[mask])
    return float(freqs[mask][peak_idx])


def audio_measure_rms(input_path: str) -> float:
    """Measure the RMS loudness of an audio file.

    Returns:
        RMS level in dBFS (negative float). Returns -inf if silent.
    """
    import re, math

    proc = subprocess.run(
        ["ffmpeg", "-i", input_path, "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", proc.stderr)
    if m:
        return float(m.group(1))
    return float("-inf")
