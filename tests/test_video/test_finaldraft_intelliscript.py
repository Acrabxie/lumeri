from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.finaldraft_intelliscript import render_finaldraft_intelliscript_ingest_manifest


SCRIPT = """INT. EDIT BAY - DAY
The editor finds the opening reaction and marks it for the first beat.

EXT. ROOFTOP - SUNSET
The second source becomes the reveal shot with city light.
"""


def test_finaldraft_intelliscript_ingest_manifest_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.finaldraft_intelliscript.render_finaldraft_intelliscript_ingest_manifest" in catalog_for_prompt("video")


def test_finaldraft_intelliscript_ingest_manifest_writes_assignments(tmp_path: Path) -> None:
    first = _make_video(tmp_path / "edit_bay_opening.mp4", size="320x180")
    second = _make_video(tmp_path / "rooftop_reveal.mp4", size="320x180")
    manifest_path = Path(render_finaldraft_intelliscript_ingest_manifest(
        [str(first), str(second)],
        str(tmp_path / "manifest"),
        script_text=SCRIPT,
        package_id="Final Draft Spot",
        reel_name="Episode 7",
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "resolve21_finaldraft_intelliscript_ingest_manifest"
    assert manifest["package"]["package_id"] == "final_draft_spot"
    assert manifest["package"]["scene_count"] == 2
    assert manifest["assignments"][0]["intelliscript_controls"]["location_type"] == "INT"
    assert manifest["assignments"][1]["timeline_intent"]["marker_color"] == "green"
    assert manifest["assignments"][0]["clip_asset_ref"] == manifest["sources"][0]["asset_ref"]


def test_finaldraft_intelliscript_ingest_manifest_validation_and_script_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_finaldraft_intelliscript_ingest_manifest([], str(tmp_path / "out"))
    with pytest.raises(FileNotFoundError):
        render_finaldraft_intelliscript_ingest_manifest([str(tmp_path / "missing.mp4")], str(tmp_path / "out"))
    clip = _make_video(tmp_path / "fallback_scene.mp4", size="320x180")
    script = tmp_path / "script.fdx.txt"
    script.write_text("A beat without a formal heading.", encoding="utf-8")
    manifest_path = Path(render_finaldraft_intelliscript_ingest_manifest([str(clip)], str(tmp_path / "out"), script_path=str(script)))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["script"]["source_path"] == str(script.resolve())
    assert manifest["assignments"][0]["scene_heading"] == "SCENE 1"


def test_finaldraft_intelliscript_ingest_manifest_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_script")
    assert manifest["sources"][0]["source_probe"]["duration"] > 0
    assert manifest["assignments"][0]["clip_asset_ref"] == manifest["sources"][0]["asset_ref"]


def test_finaldraft_intelliscript_ingest_manifest_reproduces_with_two_real_clips(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "pair_script")
    assert manifest["package"]["clip_count"] == 2
    refs = {source["asset_ref"] for source in manifest["sources"]}
    for assignment in manifest["assignments"]:
        assert assignment["clip_asset_ref"] in refs
        assert assignment["validation"]["clip_has_video"] is True


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(render_finaldraft_intelliscript_ingest_manifest([str(path) for path in paths], str(output_dir), script_text=SCRIPT))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        assert source["source_probe"]["width"] > 0
        assert source["asset_ref"]
        assert source["ingest_readiness"]["timeline_ready"] is True
    return manifest


def _make_video(path: Path, *, size: str) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=s={size}:r=12:d=1.0",
            "-f", "lavfi", "-i", "sine=frequency=610:duration=1.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path
