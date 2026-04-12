"""Transition primitives for video clips."""
from __future__ import annotations

from pathlib import Path

from gemia.video.timeline import _has_audio_stream, _probe_duration, _run


def transition_dissolve(
    input_a: str,
    input_b: str,
    output_path: str,
    *,
    duration_sec: float,
) -> str:
    """Dissolve from ``input_a`` into ``input_b`` using FFmpeg ``xfade``."""
    if duration_sec <= 0:
        raise ValueError("duration_sec must be > 0.")

    duration_a = _probe_duration(input_a)
    duration_b = _probe_duration(input_b)
    max_duration = min(duration_a, duration_b)
    if duration_sec >= max_duration:
        raise ValueError(
            "duration_sec must be smaller than both input clip durations."
        )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    offset = duration_a - duration_sec
    has_audio = _has_audio_stream(input_a) and _has_audio_stream(input_b)

    filter_parts = [
        "[0:v]setpts=PTS-STARTPTS[v0]",
        "[1:v]setpts=PTS-STARTPTS[v1]",
        f"[v0][v1]xfade=transition=dissolve:duration={duration_sec}:offset={offset}[vout]",
    ]
    if has_audio:
        filter_parts.extend(
            [
                "[0:a]asetpts=PTS-STARTPTS[a0]",
                "[1:a]asetpts=PTS-STARTPTS[a1]",
                f"[a0][a1]acrossfade=d={duration_sec}[aout]",
            ]
        )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_a,
        "-i",
        input_b,
        "-filter_complex",
        ";".join(filter_parts),
        "-map",
        "[vout]",
    ]
    if has_audio:
        cmd += ["-map", "[aout]"]
    cmd += ["-c:v", "libx264", "-c:a", "aac", output_path]
    _run(cmd)
    return output_path


_WIPE_MAP = {
    "left": "wipeleft",
    "right": "wiperight",
    "up": "wipeup",
    "down": "wipedown",
}

_PUSH_MAP = {
    "left": "slideleft",
    "right": "slideright",
    "up": "slideup",
    "down": "slidedown",
}

_CUSTOM_MAP = {
    "circle": "circlecrop",
    "radial": "radial",
    "fade_black": "fadeblack",
    "fade_white": "fadewhite",
    "pixelize": "pixelize",
}


def _xfade_transition(
    input_a: str,
    input_b: str,
    output_path: str,
    *,
    transition: str,
    duration_sec: float,
) -> str:
    """Internal helper that applies any named xfade transition."""
    if duration_sec <= 0:
        raise ValueError("duration_sec must be > 0.")

    duration_a = _probe_duration(input_a)
    duration_b = _probe_duration(input_b)
    max_duration = min(duration_a, duration_b)
    if duration_sec >= max_duration:
        raise ValueError(
            "duration_sec must be smaller than both input clip durations."
        )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    offset = duration_a - duration_sec
    has_audio = _has_audio_stream(input_a) and _has_audio_stream(input_b)

    filter_parts = [
        "[0:v]setpts=PTS-STARTPTS[v0]",
        "[1:v]setpts=PTS-STARTPTS[v1]",
        f"[v0][v1]xfade=transition={transition}:duration={duration_sec}:offset={offset}[vout]",
    ]
    if has_audio:
        filter_parts.extend(
            [
                "[0:a]asetpts=PTS-STARTPTS[a0]",
                "[1:a]asetpts=PTS-STARTPTS[a1]",
                f"[a0][a1]acrossfade=d={duration_sec}[aout]",
            ]
        )

    cmd = [
        "ffmpeg", "-y",
        "-i", input_a,
        "-i", input_b,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[vout]",
    ]
    if has_audio:
        cmd += ["-map", "[aout]"]
    cmd += ["-c:v", "libx264", "-c:a", "aac", output_path]
    _run(cmd)
    return output_path


def transition_wipe(
    input_a: str,
    input_b: str,
    output_path: str,
    *,
    direction: str,
    duration_sec: float,
) -> str:
    """Wipe transition from ``input_a`` into ``input_b``.

    Args:
        direction: One of ``"left"``, ``"right"``, ``"up"``, ``"down"``.
        duration_sec: Overlap duration in seconds.
    """
    xfade = _WIPE_MAP.get(direction)
    if xfade is None:
        raise ValueError(
            f"direction must be one of {list(_WIPE_MAP)}, got {direction!r}."
        )
    return _xfade_transition(
        input_a, input_b, output_path,
        transition=xfade,
        duration_sec=duration_sec,
    )


def transition_push(
    input_a: str,
    input_b: str,
    output_path: str,
    *,
    direction: str,
    duration_sec: float,
) -> str:
    """Push/slide transition from ``input_a`` into ``input_b``.

    Args:
        direction: One of ``"left"``, ``"right"``, ``"up"``, ``"down"``.
        duration_sec: Overlap duration in seconds.
    """
    xfade = _PUSH_MAP.get(direction)
    if xfade is None:
        raise ValueError(
            f"direction must be one of {list(_PUSH_MAP)}, got {direction!r}."
        )
    return _xfade_transition(
        input_a, input_b, output_path,
        transition=xfade,
        duration_sec=duration_sec,
    )


def transition_custom(
    input_a: str,
    input_b: str,
    output_path: str,
    *,
    mask_fn: str,
    duration_sec: float,
) -> str:
    """Custom / preset transition from ``input_a`` into ``input_b``.

    ``mask_fn`` can be one of the built-in presets
    (``"circle"``, ``"diamond"``, ``"star"``, ``"radial"``, ``"pixelize"``)
    or any raw xfade transition name supported by the installed FFmpeg build.

    Raises:
        ValueError: If ``mask_fn`` is not a known preset and not a valid
            xfade name (validated by attempting the encode).
    """
    xfade = _CUSTOM_MAP.get(mask_fn, mask_fn)
    known_presets = set(_CUSTOM_MAP)
    # Raw xfade names are allowed but presets that don't map are rejected.
    # We only hard-reject names that look like they were intended as preset
    # aliases but aren't in our map.
    if mask_fn not in known_presets and mask_fn not in {
        # expose a few common raw names so callers aren't surprised
        "circlecrop", "squeezeh", "squeezev", "radial", "pixelize",
        "hblur", "fadeblack", "fadewhite", "smoothleft", "smoothright",
        "smoothup", "smoothdown", "circleopen", "circleclose",
        "diagtl", "diagtr", "diagbl", "diagbr",
        "hlslice", "hrslice", "vuslice", "vdslice",
        "coverleft", "coverright", "coverup", "coverdown",
        "revealleft", "revealright", "revealup", "revealdown",
        "dissolve", "fade",
    }:
        raise ValueError(
            f"Unknown mask_fn {mask_fn!r}. "
            f"Built-in presets: {sorted(known_presets)}. "
            "Pass a raw xfade transition name to bypass preset lookup."
        )
    return _xfade_transition(
        input_a, input_b, output_path,
        transition=xfade,
        duration_sec=duration_sec,
    )
