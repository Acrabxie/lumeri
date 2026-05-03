from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.picture_in_picture_resolvefx import render_picture_in_picture_resolvefx_layout


def test_picture_in_picture_resolvefx_layout_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.picture_in_picture_resolvefx.render_picture_in_picture_resolvefx_layout" in catalog_for_prompt("video")


def test_picture_in_picture_resolvefx_layout_writes_controls(tmp_path: Path) -> None:
    bg = _make_video(tmp_path / "background.mp4", size="640x360")
    inset = _make_video(tmp_path / "presenter.mp4", size="320x180")
    manifest_path = Path(render_picture_in_picture_resolvefx_layout(
        [str(bg), str(inset)],
        str(tmp_path / "layout"),
        package_id="Review Spot",
        layout_presets=[
            {"id": "Lower Third", "anchor": "bottom_left", "scale_percent": 32, "margin_percent": 5, "border_width_px": 6},
            {"id": "Center", "anchor": "center", "scale": 45, "drop_shadow": False},
        ],
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "resolve21_picture_in_picture_resolvefx_layout"
    assert manifest["package"]["package_id"] == "review_spot"
    assert manifest["package"]["layout_count"] == 2
    assert manifest["layouts"][0]["resolvefx_controls"]["anchor"] == "bottom_left"
    assert manifest["layouts"][0]["resolvefx_controls"]["border_width_px"] == 6
    assert manifest["layouts"][0]["timeline_controls"]["background_track"] == "V1"
    assert manifest["layouts"][0]["validation"]["durations_overlap"] is True


def test_picture_in_picture_resolvefx_layout_validation_and_clamps(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least two"):
        render_picture_in_picture_resolvefx_layout([], str(tmp_path / "out"))
    with pytest.raises(FileNotFoundError):
        render_picture_in_picture_resolvefx_layout([str(tmp_path / "missing.mp4"), str(tmp_path / "also_missing.mp4")], str(tmp_path / "out"))
    bg = _make_video(tmp_path / "bg.mp4", size="320x180")
    inset = _make_video(tmp_path / "inset.mp4", size="160x90")
    manifest_path = Path(render_picture_in_picture_resolvefx_layout(
        [str(bg), str(inset)],
        str(tmp_path / "out"),
        layout_presets=[{"id": "", "anchor": "bad", "scale_percent": 999, "margin_percent": -5, "border_width_px": 99}],
    ))
    controls = json.loads(manifest_path.read_text(encoding="utf-8"))["layouts"][0]["resolvefx_controls"]
    assert controls["anchor"] == "bottom_right"
    assert controls["size_percent"]["width"] == 70
    assert controls["border_width_px"] == 32


def test_picture_in_picture_resolvefx_layout_reproduces_with_demo_pair(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "demo_pair_pip")
    assert manifest["sources"][0]["role"] == "background"
    assert manifest["sources"][1]["role"] == "inset"
    assert manifest["layouts"][0]["background_asset_ref"] == manifest["sources"][0]["asset_ref"]


def test_picture_in_picture_resolvefx_layout_reproduces_with_reversed_real_pair(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/gemia_timeline_demo.mp4"), Path("inputs/demo.mp4")], tmp_path / "reverse_pair_pip")
    refs = {source["asset_ref"] for source in manifest["sources"]}
    for layout in manifest["layouts"]:
        assert layout["background_asset_ref"] in refs
        assert layout["inset_asset_ref"] in refs
        assert layout["timeline_controls"]["sync_duration_seconds"] > 0


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(render_picture_in_picture_resolvefx_layout([str(path) for path in paths], str(output_dir)))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        assert source["source_probe"]["width"] > 0
        assert source["asset_ref"]
        assert "aspect_ratio" in source["layout_readiness"]
    return manifest


def _make_video(path: Path, *, size: str) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=s={size}:r=12:d=1.0",
            "-f", "lavfi", "-i", "sine=frequency=520:duration=1.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path
