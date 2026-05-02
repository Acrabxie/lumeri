from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.audio.fairlight_folder_tracks import render_fairlight_folder_tracks_manifest
from gemia.registry import catalog_for_prompt, clear_catalog_cache


def test_fairlight_folder_tracks_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.audio.fairlight_folder_tracks.render_fairlight_folder_tracks_manifest" in catalog_for_prompt("audio")


def test_fairlight_folder_tracks_groups_assets(tmp_path: Path) -> None:
    dialogue = _make_audio(tmp_path / "dialogue_host.wav", 1.0)
    music = _make_audio(tmp_path / "music_bed.wav", 1.2)
    manifest_path = Path(render_fairlight_folder_tracks_manifest([
        {"path": str(dialogue), "role": "dialogue", "label": "Host dialogue"},
        {"path": str(music), "role": "music", "label": "Main music bed"},
    ], str(tmp_path / "fairlight.json"), timeline_id="unit"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    folders = {folder["id"]: folder for folder in manifest["folders"]}
    assert manifest["effect"] == "resolve21_fairlight_folder_tracks"
    assert manifest["track_count"] == 2
    assert folders["dialogue"]["audio_track_count"] == 1
    assert folders["music"]["audio_track_count"] == 1
    assert not manifest["diagnostics"]


def test_fairlight_folder_tracks_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="track_assets cannot be empty"):
        render_fairlight_folder_tracks_manifest([], str(tmp_path / "empty.json"))
    with pytest.raises(ValueError, match="missing a path"):
        render_fairlight_folder_tracks_manifest([{"role": "dialogue"}], str(tmp_path / "bad.json"))
    manifest = json.loads(Path(render_fairlight_folder_tracks_manifest([str(tmp_path / "missing.wav")], str(tmp_path / "missing.json"))).read_text())
    assert manifest["diagnostics"][0]["code"] == "track_missing"


def test_fairlight_folder_tracks_real_local_reproductions(tmp_path: Path) -> None:
    real_inputs = [Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")]
    if not all(path.exists() for path in real_inputs):
        pytest.skip("No two local real input videos found for Fairlight folder-track testing")
    manifest_path = Path(render_fairlight_folder_tracks_manifest([
        {"path": str(real_inputs[0]), "role": "dialogue", "label": "demo dialogue reference"},
        {"path": str(real_inputs[1]), "role": "music", "label": "timeline music bed"},
    ], str(tmp_path / "real_fairlight.json"), timeline_id="real-two-clip"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    folders = {folder["id"]: folder for folder in manifest["folders"]}
    assert folders["dialogue"]["audio_track_count"] == 1
    assert folders["music"]["audio_track_count"] == 1
    assert all(track["has_audio"] for track in manifest["tracks"])
    assert all(track["duration_seconds"] > 0 for track in manifest["tracks"])


def _make_audio(path: Path, duration: float) -> Path:
    proc = subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-c:a", "pcm_s16le", str(path),
    ], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return path
