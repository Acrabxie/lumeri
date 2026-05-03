from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.media_pool_tags import render_media_pool_rating_tagging_columns_manifest


def test_media_pool_rating_tagging_columns_manifest_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.media_pool_tags.render_media_pool_rating_tagging_columns_manifest" in catalog_for_prompt("video")


def test_media_pool_rating_tagging_columns_manifest_writes_columns(tmp_path: Path) -> None:
    clip = _make_video(tmp_path / "select.mp4", size="384x216")
    manifest_path = Path(render_media_pool_rating_tagging_columns_manifest(
        [str(clip)],
        str(tmp_path / "pool"),
        package_id="Take Selects",
        ratings={"select": 5},
        scene_label="Scene A",
        tagging_rules=[
            {"id": "wide", "column": "shot_type", "tag": "wide", "when": {"min_width": 300}},
            {"id": "sound", "column": "audio_state", "tag": "sync_sound", "when": {"has_audio": True}},
        ],
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    asset = manifest["assets"][0]
    assert manifest["effect"] == "resolve21_media_pool_rating_tagging_columns_manifest"
    assert manifest["package"]["package_id"] == "take_selects"
    assert asset["rating"] == 5
    assert asset["columns"]["clip_color"] == "green"
    assert asset["tags"]["shot_type"] == ["wide"]
    assert asset["take_selection"]["keep"] is True


def test_media_pool_rating_tagging_columns_manifest_validation_and_clamps(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_media_pool_rating_tagging_columns_manifest([], str(tmp_path / "out"))
    with pytest.raises(FileNotFoundError):
        render_media_pool_rating_tagging_columns_manifest([str(tmp_path / "missing.mp4")], str(tmp_path / "out"))
    clip = _make_video(tmp_path / "low.mp4", size="320x180")
    manifest_path = Path(render_media_pool_rating_tagging_columns_manifest(
        [str(clip)],
        str(tmp_path / "out"),
        ratings={"low": 99},
        default_rating=-1,
        tagging_rules=[{"tag": "", "when": {"min_width": "bad", "max_duration": "bad", "has_audio": "bad"}}],
    ))
    asset = json.loads(manifest_path.read_text(encoding="utf-8"))["assets"][0]
    assert asset["rating"] == 5
    assert asset["columns"]["flags"] == ["favorite"]
    assert "keywords" in asset["tags"]


def test_media_pool_rating_tagging_columns_manifest_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_pool")
    assert manifest["assets"][0]["source_probe"]["duration"] > 0
    assert manifest["assets"][0]["take_selection"]["keep"] is True


def test_media_pool_rating_tagging_columns_manifest_reproduces_with_two_real_clips(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "pair_pool")
    assert manifest["package"]["asset_count"] == 2
    refs = {asset["asset_ref"] for asset in manifest["assets"]}
    assert len(refs) == 2
    for asset in manifest["assets"]:
        assert asset["columns"]["scene"] == "scene_001"
        assert asset["tags"]


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(render_media_pool_rating_tagging_columns_manifest([str(path) for path in paths], str(output_dir)))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for asset in manifest["assets"]:
        assert asset["source_probe"]["width"] > 0
        assert asset["asset_ref"]
    return manifest


def _make_video(path: Path, *, size: str) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=s={size}:r=12:d=1.0",
            "-f", "lavfi", "-i", "sine=frequency=660:duration=1.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path
