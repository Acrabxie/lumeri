"""Timeline v1 M5: OTIO adapter round-trip tests."""
from __future__ import annotations

import json
from typing import Any

import pytest

from gemia.project_model import empty_project, normalize_project
from lumerai.otio_adapter import (
    otio_json_to_project,
    otio_to_project,
    project_to_otio,
    project_to_otio_json,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _video_clip(
    clip_id: str,
    asset_id: str,
    name: str,
    *,
    start: float = 0.0,
    duration: float = 5.0,
    source_in: float = 0.0,
    track_id: str = "V1",
    effects: dict | None = None,
    transition_after: dict | None = None,
    keyframes: list | None = None,
    provenance: dict | None = None,
) -> dict[str, Any]:
    return {
        "id": clip_id,
        "asset_id": asset_id,
        "track_id": track_id,
        "name": name,
        "media_kind": "video",
        "start": start,
        "duration": duration,
        "source_in": source_in,
        "source_out": source_in + duration,
        "enabled": True,
        "effects": effects or {"rotation": 0, "mirrored": False, "muted": False, "audioDetached": False, "speed": 1},
        "transition_after": transition_after,
        "keyframes": keyframes or [],
        "provenance": provenance,
        "thumbnails": [],
        "waveform_peaks": [],
        "summary": None,
        "text_config": None,
    }


def _text_clip(
    clip_id: str,
    content: str,
    *,
    start: float = 0.0,
    duration: float = 3.0,
    track_id: str = "OV1",
) -> dict[str, Any]:
    return {
        "id": clip_id,
        "asset_id": "",
        "track_id": track_id,
        "name": content[:20],
        "media_kind": "text",
        "start": start,
        "duration": duration,
        "source_in": 0.0,
        "source_out": duration,
        "enabled": True,
        "effects": {"rotation": 0, "mirrored": False, "muted": False, "audioDetached": False, "speed": 1},
        "transition_after": None,
        "keyframes": [],
        "provenance": None,
        "thumbnails": [],
        "waveform_peaks": [],
        "summary": None,
        "text_config": {"content": content, "font_size": 72.0, "color": "#ffffff", "position": None, "align": "center"},
    }


def _make_project(
    title: str = "Test",
    *,
    clips: list | None = None,
    tracks: list | None = None,
    markers: list | None = None,
    fps: float = 30.0,
) -> dict[str, Any]:
    p = empty_project(title=title)
    p["timeline"]["fps"] = fps
    if clips is not None:
        p["timeline"]["clips"] = clips
    if tracks is not None:
        p["timeline"]["tracks"] = tracks
    if markers is not None:
        p["timeline"]["markers"] = markers
    # Build assets from clip asset_ids that reference file paths.
    assets = []
    for c in p["timeline"]["clips"]:
        aid = c.get("asset_id") or ""
        name = c.get("name") or "media.mp4"
        if aid and c.get("media_kind") != "text":
            assets.append({
                "id": aid,
                "asset_id": aid,
                "name": name,
                "media_kind": c.get("media_kind", "video"),
                "mime_type": "video/mp4",
                "source_path": f"/tmp/{name}",
                "preview_src": None,
                "duration": c.get("duration", 0.0),
                "metadata": {},
                "status": "ready",
            })
    p["assets"] = assets
    return normalize_project(p)


# ---------------------------------------------------------------------------
# Test 1: empty project round-trip
# ---------------------------------------------------------------------------

def test_empty_project_roundtrip():
    p = empty_project(title="Empty")
    otio_str = project_to_otio_json(p)
    p2 = otio_json_to_project(otio_str)
    assert p2["title"] == "Empty"
    assert p2["timeline"]["clips"] == []
    assert isinstance(p2["timeline"]["tracks"], list)
    assert p2["timeline"]["duration"] == 0.0


# ---------------------------------------------------------------------------
# Test 2: single video clip round-trip (id, start, duration, source_in preserved)
# ---------------------------------------------------------------------------

def test_single_video_clip_roundtrip():
    clip = _video_clip("c1", "asset_abc", "intro.mp4", start=0.0, duration=10.0, source_in=2.0)
    p = _make_project("Single", clips=[clip])
    p2 = otio_json_to_project(project_to_otio_json(p))

    assert len(p2["timeline"]["clips"]) == 1
    c2 = p2["timeline"]["clips"][0]
    assert c2["id"] == "c1"
    assert abs(c2["start"] - 0.0) < 1e-4
    assert abs(c2["duration"] - 10.0) < 1e-4
    assert abs(c2["source_in"] - 2.0) < 1e-4
    assert abs(c2["source_out"] - 12.0) < 1e-4
    assert c2["media_kind"] == "video"


# ---------------------------------------------------------------------------
# Test 3: multi-clip timeline preserves ordering and gaps
# ---------------------------------------------------------------------------

def test_multi_clip_ordering():
    clips = [
        _video_clip("c1", "a1", "clip1.mp4", start=0.0, duration=5.0),
        _video_clip("c2", "a2", "clip2.mp4", start=5.0, duration=3.0),
        _video_clip("c3", "a3", "clip3.mp4", start=10.0, duration=4.0),  # gap at 8-10s
    ]
    p = _make_project("Multi", clips=clips)
    p2 = otio_json_to_project(project_to_otio_json(p))

    r_clips = p2["timeline"]["clips"]
    assert len(r_clips) == 3
    ids = [c["id"] for c in r_clips]
    assert ids == ["c1", "c2", "c3"]
    # Start times must match.
    assert abs(r_clips[0]["start"] - 0.0) < 1e-4
    assert abs(r_clips[1]["start"] - 5.0) < 1e-4
    assert abs(r_clips[2]["start"] - 10.0) < 1e-4


# ---------------------------------------------------------------------------
# Test 4: overlay track (kind preserved) + text clip (text_config preserved)
# ---------------------------------------------------------------------------

def test_overlay_track_and_text_clip():
    tracks = [
        {"id": "V1", "kind": "video", "name": "Video 1", "index": 0, "locked": False, "muted": False},
        {"id": "OV1", "kind": "overlay", "name": "Overlay 1", "index": 1, "locked": False, "muted": False},
    ]
    video_clip = _video_clip("vc1", "av1", "bg.mp4", start=0.0, duration=10.0)
    txt_clip = _text_clip("tc1", "Hello OTIO", start=2.0, duration=3.0)
    p = _make_project("Overlay", clips=[video_clip, txt_clip], tracks=tracks)
    otio_tl = project_to_otio(p)

    # Check OTIO track kinds.
    otio_tracks = list(otio_tl.tracks)
    assert len(otio_tracks) == 2
    from opentimelineio import schema as os_
    assert otio_tracks[0].kind == os_.TrackKind.Video
    assert otio_tracks[1].kind == os_.TrackKind.Video  # overlay maps to Video
    assert otio_tracks[1].metadata["lumeri"]["track_kind"] == "overlay"

    # Round-trip back.
    p2 = otio_to_project(otio_tl)
    r_tracks = p2["timeline"]["tracks"]
    kinds = {t["id"]: t["kind"] for t in r_tracks}
    assert kinds.get("OV1") == "overlay"

    r_clips = p2["timeline"]["clips"]
    txt = next((c for c in r_clips if c["media_kind"] == "text"), None)
    assert txt is not None
    assert txt["text_config"]["content"] == "Hello OTIO"
    assert abs(txt["text_config"]["font_size"] - 72.0) < 0.1
    assert txt["text_config"]["color"] == "#ffffff"


# ---------------------------------------------------------------------------
# Test 5: rich fields preserved (effects, transition_after, keyframes, provenance)
# ---------------------------------------------------------------------------

def test_rich_fields_roundtrip():
    effects = {"rotation": 15, "mirrored": True, "muted": False, "audioDetached": False, "speed": 1.5}
    transition = {"kind": "dissolve", "duration": 0.5}
    keyframes = [{"time": 0.0, "opacity": 1.0}, {"time": 2.0, "opacity": 0.0}]
    provenance = {"source": "veo", "job_id": "j123"}
    clip = _video_clip(
        "rc1", "ar1", "rich.mp4",
        duration=5.0,
        effects=effects,
        transition_after=transition,
        keyframes=keyframes,
        provenance=provenance,
    )
    p = _make_project("Rich", clips=[clip])
    p2 = otio_json_to_project(project_to_otio_json(p))

    c2 = p2["timeline"]["clips"][0]
    assert c2["effects"]["rotation"] == 15
    assert c2["effects"]["mirrored"] is True
    assert abs(c2["effects"]["speed"] - 1.5) < 1e-6
    assert c2["transition_after"]["kind"] == "dissolve"
    assert len(c2["keyframes"]) == 2
    assert c2["provenance"]["source"] == "veo"


# ---------------------------------------------------------------------------
# Test 6: markers round-trip (time, label, color)
# ---------------------------------------------------------------------------

def test_markers_roundtrip():
    markers = [
        {"id": "m1", "time": 3.0, "label": "Chapter 1", "color": "#ff0000", "note": "start"},
        {"id": "m2", "time": 10.5, "label": "Cut", "color": "#00ff00", "note": None},
    ]
    p = _make_project("Markers", markers=markers)
    p2 = otio_json_to_project(project_to_otio_json(p))

    r_markers = p2["timeline"]["markers"]
    assert len(r_markers) == 2
    labels = {m["label"] for m in r_markers}
    assert "Chapter 1" in labels
    assert "Cut" in labels
    times = sorted(m["time"] for m in r_markers)
    assert abs(times[0] - 3.0) < 0.01
    assert abs(times[1] - 10.5) < 0.01


# ---------------------------------------------------------------------------
# Test 7: fps preserved through round-trip
# ---------------------------------------------------------------------------

def test_fps_preserved():
    p = _make_project("FPS24", fps=24.0)
    p2 = otio_json_to_project(project_to_otio_json(p))
    assert abs(p2["timeline"]["fps"] - 24.0) < 1e-6


# ---------------------------------------------------------------------------
# Test 8: multi-track (video + audio) clips assigned correctly
# ---------------------------------------------------------------------------

def test_multitrack_video_and_audio():
    tracks = [
        {"id": "V1", "kind": "video", "name": "Video 1", "index": 0, "locked": False, "muted": False},
        {"id": "A1", "kind": "audio", "name": "Audio 1", "index": 1, "locked": False, "muted": False},
    ]
    vid_clip = _video_clip("v1", "av1", "scene.mp4", start=0.0, duration=8.0, track_id="V1")
    aud_clip = {**_video_clip("a1", "aa1", "bg_music.mp3", start=0.0, duration=8.0, track_id="A1"), "media_kind": "audio"}
    p = _make_project("MultiTrack", clips=[vid_clip, aud_clip], tracks=tracks)
    p2 = otio_json_to_project(project_to_otio_json(p))

    by_kind = {}
    for c in p2["timeline"]["clips"]:
        by_kind[c["media_kind"]] = c
    assert "video" in by_kind
    # Audio clip round-trips with track_id A1.
    aud = next((c for c in p2["timeline"]["clips"] if c.get("track_id") == "A1"), None)
    assert aud is not None


# ---------------------------------------------------------------------------
# Test 9: read a minimal manually-constructed OTIO JSON (import path)
# ---------------------------------------------------------------------------

def test_import_minimal_otio_json():
    """Verify otio_json_to_project can handle a raw .otio JSON structure."""
    import opentimelineio as otio
    from opentimelineio import opentime, schema as os_

    rate = 30.0
    clip = os_.Clip(
        name="test_clip",
        media_reference=os_.ExternalReference(
            target_url="/media/test.mp4",
            available_range=opentime.TimeRange(
                start_time=opentime.RationalTime(0, rate),
                duration=opentime.RationalTime(90, rate),  # 3 s
            ),
        ),
        source_range=opentime.TimeRange(
            start_time=opentime.RationalTime(0, rate),
            duration=opentime.RationalTime(90, rate),
        ),
        metadata={"lumeri": {"clip_id": "import_c1", "asset_id": "import_a1", "media_kind": "video", "enabled": True}},
    )
    track = os_.Track(name="V1", kind=os_.TrackKind.Video)
    track.append(clip)
    tl = os_.Timeline(name="Imported")
    tl.tracks.append(track)

    otio_json = otio.adapters.write_to_string(tl, adapter_name="otio_json")
    p = otio_json_to_project(otio_json)

    assert p["title"] == "Imported"
    assert len(p["timeline"]["clips"]) == 1
    c = p["timeline"]["clips"][0]
    assert c["id"] == "import_c1"
    assert abs(c["duration"] - 3.0) < 0.01
    assert c["media_kind"] == "video"


# ---------------------------------------------------------------------------
# Test 10: project_id preserved through round-trip
# ---------------------------------------------------------------------------

def test_project_id_preserved():
    p = empty_project(title="IDTest")
    original_id = p["project_id"]
    p2 = otio_json_to_project(project_to_otio_json(p))
    assert p2["project_id"] == original_id


# ---------------------------------------------------------------------------
# Test 11 (M6): audio track + audio clip attributes round-trip
# ---------------------------------------------------------------------------

def test_audio_track_and_attributes_roundtrip():
    """Audio tracks map to TrackKind.Audio and back; gain_db/fade_in/fade_out/
    muted ride the effects map and survive via metadata.lumeri (M6-D)."""
    from opentimelineio import schema as os_

    tracks = [
        {"id": "V1", "kind": "video", "name": "Video 1", "index": 0, "locked": False, "muted": False},
        {"id": "A1", "kind": "audio", "name": "Audio 1", "index": 1, "locked": False, "muted": False},
    ]
    video_clip = _video_clip("vc1", "av1", "scene.mp4", start=0.0, duration=8.0)
    audio_clip = {
        **_video_clip("ac1", "aa1", "music.wav", start=2.0, duration=5.0, source_in=1.0, track_id="A1"),
        "media_kind": "audio",
        "effects": {"gain_db": -6.0, "fade_in": 0.5, "fade_out": 1.0, "muted": False},
    }
    p = _make_project("AudioRT", clips=[video_clip, audio_clip], tracks=tracks)

    # OTIO track kind for the audio track.
    otio_tracks = list(project_to_otio(p).tracks)
    assert otio_tracks[1].kind == os_.TrackKind.Audio

    # Full JSON round-trip.
    p2 = otio_json_to_project(project_to_otio_json(p))
    kinds = {t["id"]: t["kind"] for t in p2["timeline"]["tracks"]}
    assert kinds.get("A1") == "audio"

    aud = next(c for c in p2["timeline"]["clips"] if c.get("track_id") == "A1")
    assert aud["media_kind"] == "audio"
    assert abs(aud["start"] - 2.0) < 1e-4
    assert abs(aud["duration"] - 5.0) < 1e-4
    assert abs(aud["source_in"] - 1.0) < 1e-4
    assert abs(aud["source_out"] - 6.0) < 1e-4
    assert abs(aud["effects"]["gain_db"] - (-6.0)) < 1e-6
    assert abs(aud["effects"]["fade_in"] - 0.5) < 1e-6
    assert abs(aud["effects"]["fade_out"] - 1.0) < 1e-6
    assert aud["effects"]["muted"] is False


# ---------------------------------------------------------------------------
# Test 12 (M7): track-level duck_under survives the round-trip
# ---------------------------------------------------------------------------

def test_duck_under_roundtrip():
    """A music track's duck_under (sidechain trigger) survives project<->OTIO
    via track metadata.lumeri (M7-E)."""
    tracks = [
        {"id": "V1", "kind": "video", "name": "Video 1", "index": 0, "locked": False, "muted": False, "duck_under": None},
        {"id": "A1", "kind": "audio", "name": "Music", "index": 1, "locked": False, "muted": False, "duck_under": "A2"},
        {"id": "A2", "kind": "audio", "name": "Voice", "index": 2, "locked": False, "muted": False, "duck_under": None},
    ]
    video_clip = _video_clip("vc1", "av1", "scene.mp4", start=0.0, duration=8.0)
    music = {**_video_clip("m1", "am1", "music.wav", start=0.0, duration=8.0, track_id="A1"), "media_kind": "audio"}
    voice = {**_video_clip("vo1", "av2", "voice.wav", start=1.0, duration=3.0, track_id="A2"), "media_kind": "audio"}
    p = _make_project("Duck", clips=[video_clip, music, voice], tracks=tracks)

    p2 = otio_json_to_project(project_to_otio_json(p))

    by_id = {t["id"]: t for t in p2["timeline"]["tracks"]}
    assert by_id["A1"]["duck_under"] == "A2"
    assert by_id["A2"]["duck_under"] is None
    assert by_id["V1"]["duck_under"] is None
