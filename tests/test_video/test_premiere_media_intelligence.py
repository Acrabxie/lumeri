from __future__ import annotations
import json
import subprocess
from pathlib import Path
import pytest
from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.premiere_media_intelligence import render_premiere_media_intelligence_visual_marker_search_manifest

def test_premiere_media_intelligence_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.premiere_media_intelligence.render_premiere_media_intelligence_visual_marker_search_manifest" in catalog_for_prompt("video")

def test_premiere_media_intelligence_writes_facets_and_marker_ranges(tmp_path: Path) -> None:
    clip = _make_video(tmp_path / "wide_select.mp4")
    manifest_path = Path(render_premiere_media_intelligence_visual_marker_search_manifest(
        [str(clip)], str(tmp_path / "search"),
        package_id="Marker Search",
        search_queries=["wide goal", "sync audio", "select marker"],
        markers=[{"name": "Select Marker", "time_seconds": 0.4, "comment": "goal reaction"}],
        transcripts=["goal reaction with sync audio"],
        metadata_facets={"project": "demo"},
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "premiere_media_intelligence_visual_marker_search_manifest"
    assert manifest["privacy"]["uploads_source_media"] is False
    asset = manifest["assets"][0]
    assert asset["local_index"]["visual"]["orientation"] == "horizontal"
    assert asset["local_index"]["metadata"]["project"] == "demo"
    assert asset["local_index"]["markers"][0]["time_seconds"] == 0.4
    assert {hit["query"] for hit in manifest["search_results"]} >= {"wide goal", "sync audio", "select marker"}
    assert any("marker" in hit["matched_facets"] for hit in manifest["search_results"])

def test_premiere_media_intelligence_rejects_bad_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_premiere_media_intelligence_visual_marker_search_manifest([], str(tmp_path))
    with pytest.raises(FileNotFoundError):
        render_premiere_media_intelligence_visual_marker_search_manifest([str(tmp_path / "missing.mp4")], str(tmp_path))
    folder = tmp_path / "folder"; folder.mkdir()
    with pytest.raises(OSError):
        render_premiere_media_intelligence_visual_marker_search_manifest([str(folder)], str(tmp_path))
    audio = tmp_path / "tone.mp3"
    proc = subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5", "-c:a", "libmp3lame", str(audio)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr[-1000:]
    with pytest.raises(ValueError, match="visual media"):
        render_premiere_media_intelligence_visual_marker_search_manifest([str(audio)], str(tmp_path))

def test_premiere_media_intelligence_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_search")
    assert manifest["package"]["asset_count"] == 1
    assert manifest["search_results"]
    assert manifest["assets"][0]["source_probe"]["media_kind"] == "video"

def test_premiere_media_intelligence_reproduces_with_two_real_clips(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "pair_search")
    assert manifest["package"]["asset_count"] == 2
    assert len({asset["asset_ref"] for asset in manifest["assets"]}) == 2
    assert all(result["end_seconds"] > result["start_seconds"] for result in manifest["search_results"])

def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(render_premiere_media_intelligence_visual_marker_search_manifest([str(path) for path in paths], str(output_dir)))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for asset in manifest["assets"]:
        assert Path(asset["source_path"]).exists()
        assert asset["cache_key"]
        assert asset["local_index"]["visual"]["resolution"]
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
