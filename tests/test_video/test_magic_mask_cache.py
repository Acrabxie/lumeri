from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.magic_mask_cache import render_magic_mask_render_in_place_cache_manifest


def test_magic_mask_render_in_place_cache_manifest_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.magic_mask_cache.render_magic_mask_render_in_place_cache_manifest" in catalog_for_prompt("video")


def test_magic_mask_render_in_place_cache_manifest_writes_cache_entries(tmp_path: Path) -> None:
    clip = _make_video(tmp_path / "hero.mp4", size="384x216")
    manifest_path = Path(render_magic_mask_render_in_place_cache_manifest(
        [str(clip)],
        str(tmp_path / "cache"),
        package_id="Magic Mask Spot",
        cache_codec="dnxhr_444",
        cache_resolution="half",
        mask_tracks=[
            {"id": "Face A", "target_type": "person", "tracking_mode": "better", "handles_frames": 16},
            {"id": "Prop", "target_type": "object", "quality": "faster", "include_alpha": False},
        ],
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "resolve21_magic_mask_render_in_place_cache_manifest"
    assert manifest["package"]["package_id"] == "magic_mask_spot"
    assert manifest["package"]["cache_entry_count"] == 2
    assert manifest["cache_entries"][0]["render_in_place"]["cache_codec"] == "dnxhr_444"
    assert manifest["cache_entries"][0]["tracking_windows"][0]["estimated_frames"] > 0
    assert manifest["cache_entries"][1]["render_in_place"]["alpha_mode"] == "matte_only"


def test_magic_mask_render_in_place_cache_manifest_validation_and_clamps(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_magic_mask_render_in_place_cache_manifest([], str(tmp_path / "out"))
    with pytest.raises(FileNotFoundError):
        render_magic_mask_render_in_place_cache_manifest([str(tmp_path / "missing.mp4")], str(tmp_path / "out"))
    clip = _make_video(tmp_path / "default.mp4", size="320x180")
    manifest_path = Path(render_magic_mask_render_in_place_cache_manifest(
        [str(clip)],
        str(tmp_path / "out"),
        cache_codec="bad",
        cache_resolution="bad",
        mask_tracks=[{"id": "", "target_type": "bad", "tracking_mode": "bad", "handles_frames": 999}],
    ))
    entry = json.loads(manifest_path.read_text(encoding="utf-8"))["cache_entries"][0]
    assert entry["render_in_place"]["cache_codec"] == "prores_4444"
    assert entry["render_in_place"]["cache_resolution"] == "source"
    assert entry["render_in_place"]["handles_frames"] == 120
    assert entry["validation"]["render_in_place_ready"] is True


def test_magic_mask_render_in_place_cache_manifest_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_magic_mask")
    assert manifest["sources"][0]["source_probe"]["duration"] > 0
    assert manifest["cache_entries"][0]["clip_asset_refs"] == [manifest["sources"][0]["asset_ref"]]


def test_magic_mask_render_in_place_cache_manifest_reproduces_with_two_real_clips(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "pair_magic_mask")
    assert manifest["package"]["clip_count"] == 2
    refs = {source["asset_ref"] for source in manifest["sources"]}
    for entry in manifest["cache_entries"]:
        assert set(entry["clip_asset_refs"]) == refs
        assert entry["validation"]["source_count"] == 2
        assert entry["validation"]["total_tracked_seconds"] > 0


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(render_magic_mask_render_in_place_cache_manifest([str(path) for path in paths], str(output_dir)))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        assert source["source_probe"]["width"] > 0
        assert source["asset_ref"]
        assert source["mask_readiness"]["supports_temporal_tracking"] is True
    return manifest


def _make_video(path: Path, *, size: str) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=s={size}:r=12:d=1.0",
            "-f", "lavfi", "-i", "sine=frequency=550:duration=1.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path
