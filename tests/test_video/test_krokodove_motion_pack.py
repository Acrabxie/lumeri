from __future__ import annotations

import json
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.krokodove_motion_pack import KROKODOVE_NODE_PRESETS, render_krokodove_motion_pack


def test_krokodove_motion_pack_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.krokodove_motion_pack.render_krokodove_motion_pack" in catalog_for_prompt("video")


def test_krokodove_motion_pack_writes_overlay_and_sidecar(tmp_path: Path) -> None:
    source = _make_video(tmp_path / "source.mp4", duration=0.5)
    output = tmp_path / "krokodove.mp4"
    result = render_krokodove_motion_pack(str(source), str(output), preset="scanline_caption", title="Review", max_seconds=0.5)
    metadata = json.loads(output.with_suffix(".krokodove_motion_pack.json").read_text(encoding="utf-8"))
    assert result == str(output)
    assert output.exists()
    assert metadata["effect"] == "resolve21_fusion_krokodove_motion_pack"
    assert metadata["preset"] == "scanline_caption"
    assert metadata["node_preset"] == KROKODOVE_NODE_PRESETS["scanline_caption"]
    assert metadata["rendered_frames"] > 0
    assert metadata["audio_copied"] is True


def test_krokodove_motion_pack_validation(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        render_krokodove_motion_pack(str(tmp_path / "missing.mp4"), str(tmp_path / "out.mp4"))
    source = _make_video(tmp_path / "source.mp4", duration=0.3)
    with pytest.raises(ValueError, match="Unknown Krokodove preset"):
        render_krokodove_motion_pack(str(source), str(tmp_path / "bad.mp4"), preset="nope")


def test_krokodove_motion_pack_real_local_reproductions(tmp_path: Path) -> None:
    real_inputs = [Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")]
    if not all(path.exists() for path in real_inputs):
        pytest.skip("No two local real input videos found for Krokodove testing")
    presets = ["orbit_grid", "radial_echo"]
    for index, source in enumerate(real_inputs):
        output = tmp_path / f"real_{index}.mp4"
        render_krokodove_motion_pack(str(source), str(output), preset=presets[index], title=f"Real {index}", max_seconds=0.8)
        metadata = json.loads(output.with_suffix(".krokodove_motion_pack.json").read_text(encoding="utf-8"))
        assert output.exists()
        assert metadata["audio_copied"] is True
        assert metadata["node_preset"]
        assert _probe_stream_count(output, "v") >= 1
        assert _probe_stream_count(output, "a") >= 1


def _make_video(path: Path, duration: float) -> Path:
    proc = subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=s=160x90:r=12:d={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=550:duration={duration}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    ], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return path


def _probe_stream_count(path: Path, selector: str) -> int:
    proc = subprocess.run(["ffprobe", "-v", "error", "-select_streams", selector, "-show_entries", "stream=index", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return len([line for line in proc.stdout.splitlines() if line.strip()])
