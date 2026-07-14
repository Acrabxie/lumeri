"""Phase 1 transition rendering gates (docs/timeline-canonical-plan.md §5-§6).

Real ffmpeg end-to-end: color-coded clips through ProjectStore + export_project,
asserting INVARIANT T1 (the base video's total duration equals the timeline
duration — segment surgery must never shrink or grow the output the way a
joint-xfade concat would), that a dissolve actually blends pixels inside the
overlap window, the no-handle degrade-to-cut path, the fade filter path, and
A/V sync (pass-3 adelay positioning must be unaffected by pass-1 surgery).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from gemia.project_export import export_project
from gemia.project_store import ProjectHandle


_FPS = 30.0
_FRAME = 1.0 / _FPS
_TOL = _FRAME + 0.005  # ±1 frame (+ container rounding)


# ── media + project helpers ──────────────────────────────────────────────────


def _gen_color_video(tmp_path: Path, name: str, color: str, duration: float) -> Path:
    out = tmp_path / name
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            # 16:9 so the export's scale+pad fills the 1920x1080 canvas —
            # pillarbox bars would skew the mean-RGB probes below.
            "-i", f"color=c={color}:s=192x108:r=30:d={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(out),
        ],
        capture_output=True,
        check=True,
    )
    return out


def _gen_tone(tmp_path: Path, name: str, duration: float) -> Path:
    out = tmp_path / name
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"sine=frequency=440:duration={duration}",
            str(out),
        ],
        capture_output=True,
        check=True,
    )
    return out


def _project(tmp_path: Path, name: str) -> ProjectHandle:
    return ProjectHandle.open(tmp_path / "projects", name, session_id=name)


def _seed_two_clips(
    handle: ProjectHandle,
    a_path: Path,
    b_path: Path,
    *,
    b_source_in: float = 1.0,
    transition: str | None = None,
    duration_sec: float = 0.5,
) -> None:
    """A = 2 s at [0, 2), B = 2 s at [2, 4) trimmed to ``b_source_in``."""
    handle.apply_ops(
        [
            {"op": "upsert_asset", "asset": {
                "id": "asset_a", "asset_id": "asset_a", "name": "a.mp4",
                "media_kind": "video", "source_path": str(a_path), "duration": 2.0}},
            {"op": "insert_clip", "track_id": "V1", "at": {"time": 0.0},
             "data": {"clip": {
                 "id": "cA", "asset_id": "asset_a", "media_kind": "video",
                 "duration": 2.0, "source_in": 0.0, "source_out": 2.0}}},
            {"op": "upsert_asset", "asset": {
                "id": "asset_b", "asset_id": "asset_b", "name": "b.mp4",
                "media_kind": "video", "source_path": str(b_path), "duration": 3.0}},
            {"op": "insert_clip", "track_id": "V1", "at": {"time": 2.0},
             "data": {"clip": {
                 "id": "cB", "asset_id": "asset_b", "media_kind": "video",
                 "duration": 2.0, "source_in": b_source_in,
                 "source_out": b_source_in + 2.0}}},
        ],
        label="seed-two-clips",
    )
    if transition:
        handle.apply_ops(
            [{"op": "add_transition", "clip_id": "cA", "kind": transition,
              "duration_sec": duration_sec}],
            label="seed-transition",
        )


def _export(handle: ProjectHandle, tmp_path: Path, label: str) -> dict[str, Any]:
    return export_project(
        handle.store, handle.project_id,
        output_root=tmp_path / "out", quality="draft", label=label,
    )


# ── probe helpers ────────────────────────────────────────────────────────────


def _ffprobe(path: str | Path) -> dict[str, Any]:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(proc.stdout)


def _stream_duration(probe: dict[str, Any], codec_type: str) -> float:
    for stream in probe.get("streams") or []:
        if stream.get("codec_type") == codec_type:
            return float(stream.get("duration") or 0.0)
    return 0.0


def _format_duration(probe: dict[str, Any]) -> float:
    return float((probe.get("format") or {}).get("duration") or 0.0)


def _frame_rgb_mean(path: str | Path, t: float) -> tuple[float, float, float]:
    """Mean RGB of the frame at ``t`` (downscaled 16x16, raw rgb24)."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-ss", f"{t:.6f}", "-i", str(path),
         "-frames:v", "1", "-vf", "scale=16:16",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    )
    data = proc.stdout
    assert data, f"no frame decoded at t={t}"
    n = len(data) // 3
    return (
        sum(data[0::3]) / n,
        sum(data[1::3]) / n,
        sum(data[2::3]) / n,
    )


def _dist(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return max(abs(x - y) for x, y in zip(a, b))


# ── gate 1+2: T1 duration + the dissolve actually blends ─────────────────────


def test_t1_duration_and_dissolve_blend_e2e(tmp_path: Path) -> None:
    """INVARIANT T1: fade/dissolve exports match the no-transition export's
    duration ± 1 frame; a probe frame inside the dissolve overlap window
    differs from both the pure-A and pure-B frames (mid-blend assertion);
    a fade darkens the frames flanking the cut."""
    red = _gen_color_video(tmp_path, "red.mp4", "red", 2.0)
    blue = _gen_color_video(tmp_path, "blue.mp4", "blue", 3.0)

    plain = _project(tmp_path, "t1plain")
    _seed_two_clips(plain, red, blue, b_source_in=1.0, transition=None)
    fade = _project(tmp_path, "t1fade")
    _seed_two_clips(fade, red, blue, b_source_in=1.0, transition="fade")
    dissolve = _project(tmp_path, "t1dissolve")
    _seed_two_clips(dissolve, red, blue, b_source_in=1.0, transition="dissolve")

    m_plain = _export(plain, tmp_path, "plain")
    m_fade = _export(fade, tmp_path, "fade")
    m_dissolve = _export(dissolve, tmp_path, "dissolve")

    d_plain = _format_duration(_ffprobe(m_plain["export_path"]))
    d_fade = _format_duration(_ffprobe(m_fade["export_path"]))
    d_dissolve = _format_duration(_ffprobe(m_dissolve["export_path"]))

    # Baseline sanity: 2 s + 2 s timeline.
    assert abs(d_plain - 4.0) <= 0.1
    # T1: transitions must not change the export duration (± 1 frame).
    assert abs(d_fade - d_plain) <= _TOL
    assert abs(d_dissolve - d_plain) <= _TOL

    # Manifest honesty: both rendered, dissolve at full d_eff = 0.5
    # (d=0.5 <= B.source_in=1.0), nothing dropped.
    assert m_fade["transitions_rendered"] == 1
    assert m_fade["transitions"][0]["status"] == "rendered"
    assert m_dissolve["transitions_rendered"] == 1
    assert m_dissolve["transitions"][0]["status"] == "rendered"
    assert m_dissolve["transitions"][0]["effective_duration_sec"] == 0.5
    assert m_dissolve["dropped_fields"] == []
    assert m_plain["transitions_rendered"] == 0
    assert m_plain["dropped_fields"] == []

    # Reference colors from the no-transition export.
    ref_a = _frame_rgb_mean(m_plain["export_path"], 1.0)    # pure A (red)
    ref_b = _frame_rgb_mean(m_plain["export_path"], 2.5)    # pure B (blue)
    assert ref_a[0] > 180 and ref_a[2] < 80    # red-ish
    assert ref_b[2] > 180 and ref_b[0] < 80    # blue-ish

    # Mid-blend probe: cut=2.0, d_eff=0.5 → window [1.5, 2.0); probe its middle.
    blend = _frame_rgb_mean(m_dissolve["export_path"], 1.75)
    assert _dist(blend, ref_a) > 60, f"dissolve frame {blend} too close to pure A {ref_a}"
    assert _dist(blend, ref_b) > 60, f"dissolve frame {blend} too close to pure B {ref_b}"
    # And it sits between the two (red falling, blue rising).
    assert ref_b[0] < blend[0] < ref_a[0]
    assert ref_a[2] < blend[2] < ref_b[2]

    # Fade-through-black: frames flanking the cut are much darker than either
    # source color (fade=out on A's tail, fade=in on B's head; d/2 = 0.25).
    dark_out = _frame_rgb_mean(m_fade["export_path"], 1.97)
    dark_in = _frame_rgb_mean(m_fade["export_path"], 2.03)
    assert max(dark_out) < 90, f"A tail not faded out: {dark_out}"
    assert max(dark_in) < 90, f"B head not faded in: {dark_in}"


# ── gate 3: dissolve without handle degrades to a hard cut ───────────────────


def test_dissolve_without_handle_degrades_to_cut(tmp_path: Path) -> None:
    """B.source_in = 0 → no pre-handle media → d_eff = 0 < 2/fps → hard cut,
    duration preserved, manifest carries reason "no_handle" (never an error)."""
    red = _gen_color_video(tmp_path, "red.mp4", "red", 2.0)
    blue = _gen_color_video(tmp_path, "blue.mp4", "blue", 3.0)
    handle = _project(tmp_path, "nohandle")
    _seed_two_clips(handle, red, blue, b_source_in=0.0, transition="dissolve")

    manifest = _export(handle, tmp_path, "nohandle")

    probe = _ffprobe(manifest["export_path"])
    assert abs(_format_duration(probe) - 4.0) <= 0.1
    assert any(s.get("codec_type") == "video" for s in probe["streams"])

    assert manifest["transitions_rendered"] == 0
    record = manifest["transitions"][0]
    assert record["clip_id"] == "cA"
    assert record["status"] == "degraded"
    assert record["reason"] == "no_handle"
    assert {"clip_id": "cA", "field": "transition_after", "reason": "no_handle"} \
        in manifest["dropped_fields"]

    # Hard cut: just before the cut is still pure A (no blend happened).
    frame = _frame_rgb_mean(manifest["export_path"], 1.75)
    assert frame[0] > 180 and frame[2] < 80, f"expected pure A at 1.75, got {frame}"


# ── gate 4: A/V sync — pass-3 audio positioning unaffected by surgery ────────


def test_av_sync_dissolve_leaves_audio_positioning_unchanged(tmp_path: Path) -> None:
    """A project with an audio clip + one dissolve: audio stream duration and
    total duration match the no-transition export (adelay math intact)."""
    red = _gen_color_video(tmp_path, "red.mp4", "red", 2.0)
    blue = _gen_color_video(tmp_path, "blue.mp4", "blue", 3.0)
    tone = _gen_tone(tmp_path, "tone.wav", 3.0)

    def _seed_audio(handle: ProjectHandle) -> None:
        handle.apply_ops(
            [
                {"op": "upsert_asset", "asset": {
                    "id": "asset_t", "asset_id": "asset_t", "name": "tone.wav",
                    "media_kind": "audio", "source_path": str(tone), "duration": 3.0}},
                {"op": "insert_clip", "track_id": "A1", "at": {"time": 0.5},
                 "data": {"clip": {
                     "id": "cT", "asset_id": "asset_t", "media_kind": "audio",
                     "duration": 3.0, "source_in": 0.0, "source_out": 3.0}}},
            ],
            label="seed-audio",
        )

    plain = _project(tmp_path, "avplain")
    _seed_two_clips(plain, red, blue, b_source_in=1.0, transition=None)
    _seed_audio(plain)
    dissolve = _project(tmp_path, "avdissolve")
    _seed_two_clips(dissolve, red, blue, b_source_in=1.0, transition="dissolve")
    _seed_audio(dissolve)

    m_plain = _export(plain, tmp_path, "avplain")
    m_dissolve = _export(dissolve, tmp_path, "avdissolve")
    assert m_plain["has_audio"] and m_dissolve["has_audio"]
    assert m_dissolve["transitions_rendered"] == 1

    p_plain = _ffprobe(m_plain["export_path"])
    p_dissolve = _ffprobe(m_dissolve["export_path"])

    # T1 on the video stream itself, not just the container.
    v_plain = _stream_duration(p_plain, "video")
    v_dissolve = _stream_duration(p_dissolve, "video")
    assert abs(v_dissolve - v_plain) <= _TOL
    # Audio mix output identical in length → adelay positioning unchanged.
    a_plain = _stream_duration(p_plain, "audio")
    a_dissolve = _stream_duration(p_dissolve, "audio")
    assert a_plain > 3.0  # 0.5 s adelay + 3 s tone
    assert abs(a_dissolve - a_plain) <= 0.05
    assert abs(_format_duration(p_dissolve) - _format_duration(p_plain)) <= _TOL


# ── runtime re-check: stale adjacency degrades, never breaks T1 ──────────────


def test_stale_transition_after_gap_degrades_not_adjacent(tmp_path: Path) -> None:
    """The export-side re-check is defense in depth: move/delete now clear
    stale transitions at write time (plan §5.2 step 4, Phase 2), but a gap
    opened via set_clip_time (or multi-track interposition) still reaches the
    renderer, which degrades to a hard cut with "not_adjacent" and keeps T1."""
    red = _gen_color_video(tmp_path, "red.mp4", "red", 2.0)
    blue = _gen_color_video(tmp_path, "blue.mp4", "blue", 3.0)
    handle = _project(tmp_path, "stale")
    _seed_two_clips(handle, red, blue, b_source_in=1.0, transition="dissolve")
    # Open a 1 s gap after the transition was stored. set_clip_time is outside
    # the write-time cleanup (only move/delete re-validate), so the stored
    # transition stays stale on cA — exactly the state the runtime guard owns.
    handle.apply_ops(
        [{"op": "set_clip_time", "clip_id": "cB", "start": 3.0}], label="open-gap",
    )
    (clip_a,) = [c for c in handle.load()["timeline"]["clips"] if c["id"] == "cA"]
    assert isinstance(clip_a.get("transition_after"), dict), "premise: still stale"

    manifest = _export(handle, tmp_path, "stale")

    assert manifest["transitions_rendered"] == 0
    record = manifest["transitions"][0]
    assert record["status"] == "degraded"
    assert record["reason"] == "not_adjacent"
    assert {"clip_id": "cA", "field": "transition_after", "reason": "not_adjacent"} \
        in manifest["dropped_fields"]
    # Timeline now ends at 5.0 (gap rendered black); duration still exact.
    assert abs(_format_duration(_ffprobe(manifest["export_path"])) - 5.0) <= 0.1
