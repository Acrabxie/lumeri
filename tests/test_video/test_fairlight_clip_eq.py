from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.fairlight_clip_eq import render_fairlight_6band_clip_eq


def test_fairlight_6band_clip_eq_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.fairlight_clip_eq.render_fairlight_6band_clip_eq" in catalog_for_prompt("video")


def test_fairlight_6band_clip_eq_writes_manifest(tmp_path: Path) -> None:
    clip = _make_video(tmp_path / "eq_source.mp4")
    bands = [{"id": f"Band {i}", "frequency_hz": 100 * i, "gain_db": i - 3, "q": 1.0, "kind": "bell"} for i in range(1, 7)]
    manifest_path = Path(render_fairlight_6band_clip_eq([str(clip)], str(tmp_path / "manifest"), eq_bands=bands, analysis_samples=12))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "resolve21_fairlight_6band_clip_eq"
    assert manifest["preset"]["band_count"] == 6
    assert manifest["preset"]["analysis_samples"] == 16
    assert len(manifest["clips"][0]["eq_bands"]) == 6
    assert manifest["clips"][0]["analysis"]["has_audio"] is True
    assert manifest["clips"][0]["eq_bands"][0]["band_id"] == "band_1"


def test_fairlight_6band_clip_eq_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_fairlight_6band_clip_eq([], str(tmp_path / "out"))
    with pytest.raises(ValueError, match="six bands"):
        render_fairlight_6band_clip_eq([str(_make_video(tmp_path / "source.mp4"))], str(tmp_path / "out"), eq_bands=[{"id": "one"}])
    with pytest.raises(FileNotFoundError):
        render_fairlight_6band_clip_eq([str(tmp_path / "missing.mp4")], str(tmp_path / "out"))


def test_fairlight_6band_clip_eq_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_eq")
    assert manifest["clips"][0]["analysis"]["peak_max"] > 0
    assert len(manifest["clips"][0]["eq_bands"]) == 6


def test_fairlight_6band_clip_eq_reproduces_with_two_real_clips(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "pair_eq")
    assert manifest["preset"]["clip_count"] == 2
    refs = {clip["asset_ref"] for clip in manifest["clips"]}
    assert len(refs) == 2
    assert all(clip["analysis"]["has_audio"] for clip in manifest["clips"])


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(render_fairlight_6band_clip_eq([str(path) for path in paths], str(output_dir)))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for clip in manifest["clips"]:
        assert clip["source_probe"]["duration"] > 0
        assert clip["source_probe"]["has_audio"] is True
        assert clip["asset_ref"]
    return manifest


def _make_video(path: Path) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=s=180x100:r=12:d=1.0",
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
