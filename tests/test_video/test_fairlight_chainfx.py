from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.fairlight_chainfx import render_fairlight_eq_level_match_chainfx


def test_fairlight_chainfx_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.fairlight_chainfx.render_fairlight_eq_level_match_chainfx" in catalog_for_prompt("video")


def test_fairlight_chainfx_writes_manifest(tmp_path: Path) -> None:
    clip = _make_video(tmp_path / "chain_source.mp4", frequency=330)
    manifest_path = Path(render_fairlight_eq_level_match_chainfx([str(clip)], str(tmp_path / "manifest"), target_peak=2.0))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "resolve21_fairlight_eq_level_match_chainfx"
    assert manifest["chain"]["reference_peak"] == 0.98
    assert len(manifest["chain_fx"]) == 3
    assert manifest["clips"][0]["analysis"]["has_audio"] is True
    assert manifest["clips"][0]["chain_instances"][0]["fx_id"] == "clip_eq"


def test_fairlight_chainfx_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_fairlight_eq_level_match_chainfx([], str(tmp_path / "out"))
    with pytest.raises(FileNotFoundError):
        render_fairlight_eq_level_match_chainfx([str(tmp_path / "missing.mp4")], str(tmp_path / "out"))


def test_fairlight_chainfx_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_chain")
    assert manifest["clips"][0]["level_match"]["mode"] == "clip_gain"
    assert manifest["match_report"]["matched_clip_count"] == 1


def test_fairlight_chainfx_reproduces_with_two_real_clips(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "pair_chain")
    assert manifest["chain"]["clip_count"] == 2
    refs = {clip["asset_ref"] for clip in manifest["clips"]}
    assert len(refs) == 2
    assert all(len(clip["chain_instances"]) == 3 for clip in manifest["clips"])


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(render_fairlight_eq_level_match_chainfx([str(path) for path in paths], str(output_dir)))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for clip in manifest["clips"]:
        assert clip["source_probe"]["duration"] > 0
        assert clip["source_probe"]["has_audio"] is True
        assert clip["analysis"]["peak_max"] > 0
    return manifest


def _make_video(path: Path, *, frequency: int) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=s=180x100:r=12:d=1.0",
            "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=1.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path
