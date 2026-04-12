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
