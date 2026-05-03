from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.audio_driven_fusion import render_audio_driven_fusion_animation


def test_audio_driven_fusion_animation_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.audio_driven_fusion.render_audio_driven_fusion_animation" in catalog_for_prompt("video")


def test_audio_driven_fusion_animation_writes_curves(tmp_path: Path) -> None:
    clip = _make_video(tmp_path / "audio_source.mp4")
    manifest_path = Path(render_audio_driven_fusion_animation(
        [str(clip)],
        str(tmp_path / "manifest"),
        sample_count=12,
        parameter_targets=[{"id": "Glow Gain", "node": "Glow", "minimum": 0.2, "maximum": 0.8, "curve": "ease_out"}],
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "resolve21_audio_driven_fusion_animation"
    assert manifest["animation"]["parameter_count"] == 1
    assert manifest["sources"][0]["audio_summary"]["has_audio"] is True
    curve = manifest["automation_sets"][0]["parameter_curves"][0]
    assert curve["parameter_id"] == "glow_gain"
    assert len(curve["keyframes"]) == 16
    assert any(frame["value"] > 0.2 for frame in curve["keyframes"])


def test_audio_driven_fusion_animation_validation_and_clamps(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_audio_driven_fusion_animation([], str(tmp_path / "out"))
    with pytest.raises(FileNotFoundError):
        render_audio_driven_fusion_animation([str(tmp_path / "missing.mp4")], str(tmp_path / "out"))
    clip = _make_video(tmp_path / "source.mp4")
    manifest_path = Path(render_audio_driven_fusion_animation([str(clip)], str(tmp_path / "out"), sample_count=2, smoothing=2.0))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["animation"]["sample_count"] == 16
    assert manifest["animation"]["smoothing"] == 0.95


def test_audio_driven_fusion_animation_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_audio")
    assert manifest["sources"][0]["audio_summary"]["nonzero_peak_count"] > 0
    assert manifest["automation_sets"][0]["beat_markers"]


def test_audio_driven_fusion_animation_reproduces_with_two_real_clips(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "pair_audio")
    assert manifest["animation"]["clip_count"] == 2
    assert len(manifest["automation_sets"]) == 2
    refs = {item["asset_ref"] for item in manifest["automation_sets"]}
    assert len(refs) == 2
    assert all(len(item["parameter_curves"][0]["keyframes"]) == 24 for item in manifest["automation_sets"])


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(render_audio_driven_fusion_animation([str(path) for path in paths], str(output_dir)))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        assert source["source_probe"]["duration"] > 0
        assert source["source_probe"]["has_audio"] is True
        assert source["asset_ref"]
    return manifest


def _make_video(path: Path) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=s=180x100:r=12:d=1.0",
            "-f", "lavfi", "-i", "sine=frequency=880:duration=1.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path
