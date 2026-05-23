from __future__ import annotations
import json
import subprocess
from pathlib import Path
import pytest
from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.premiere_generative_extend import render_premiere_generative_extend_edit_handle_manifest

def test_premiere_generative_extend_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.premiere_generative_extend.render_premiere_generative_extend_edit_handle_manifest" in catalog_for_prompt("video")

def test_premiere_generative_extend_writes_video_and_audio_handles(tmp_path: Path) -> None:
    clip = _make_video(tmp_path / "reaction.mp4")
    manifest_path = Path(render_premiere_generative_extend_edit_handle_manifest(
        [str(clip)], str(tmp_path / "handles"),
        package_id="Reaction Extend",
        extension_requests=[
            {"id": "intro_frames", "side": "head", "media_type": "video", "duration_seconds": 3.0},
            {"id": "room_tone", "side": "tail", "media_type": "audio", "duration_seconds": 4.0},
        ],
        provider_constraints={"max_video_extension_seconds": 1.25, "requires_cloud_ai": True},
        transition_intent="hold expression before cut",
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "premiere_generative_extend_edit_handle_manifest"
    assert manifest["premiere_controls"]["source_media_modified"] is False
    assert manifest["provider_constraints"]["max_video_extension_seconds"] == 1.25
    plan = manifest["edit_handle_plans"][0]
    handles = {item["request_id"]: item for item in plan["requested_handles"]}
    assert handles["intro_frames"]["planned_duration_seconds"] == 1.25
    assert handles["intro_frames"]["generated_units"]["frames"] > 0
    assert handles["room_tone"]["eligible"] is True
    assert handles["room_tone"]["generated_units"]["sample_rate"] == 48000
    assert plan["source_range"]["has_audio"] is True

def test_premiere_generative_extend_rejects_bad_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_premiere_generative_extend_edit_handle_manifest([], str(tmp_path))
    with pytest.raises(FileNotFoundError):
        render_premiere_generative_extend_edit_handle_manifest([str(tmp_path / "missing.mp4")], str(tmp_path))
    folder = tmp_path / "folder"; folder.mkdir()
    with pytest.raises(OSError):
        render_premiere_generative_extend_edit_handle_manifest([str(folder)], str(tmp_path))
    audio = tmp_path / "tone.mp3"
    proc = subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5", "-c:a", "libmp3lame", str(audio)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr[-1000:]
    with pytest.raises(ValueError, match="visual media"):
        render_premiere_generative_extend_edit_handle_manifest([str(audio)], str(tmp_path))

def test_premiere_generative_extend_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_handles")
    assert manifest["package"]["clip_count"] == 1
    assert manifest["edit_handle_plans"][0]["requested_handles"]
    assert manifest["sources"][0]["source_probe"]["media_kind"] == "video"

def test_premiere_generative_extend_reproduces_with_two_real_clips(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "pair_handles")
    assert manifest["package"]["clip_count"] == 2
    assert len({source["asset_ref"] for source in manifest["sources"]}) == 2
    assert all(handle["provider_context_range"]["uploads_full_source"] is False for plan in manifest["edit_handle_plans"] for handle in plan["requested_handles"])

def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(render_premiere_generative_extend_edit_handle_manifest([str(path) for path in paths], str(output_dir)))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        assert Path(source["source_path"]).exists()
        assert source["cache_key"]
        assert source["source_probe"]["width"] > 0
    return manifest

def _make_video(path: Path) -> Path:
    proc = subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=640x360:r=24:d=1.2",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=1.2",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    ], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path
