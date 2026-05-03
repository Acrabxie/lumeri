from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.social_delivery import render_vertical_social_resolution_delivery_manifest


def test_vertical_social_resolution_delivery_manifest_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.social_delivery.render_vertical_social_resolution_delivery_manifest" in catalog_for_prompt("video")


def test_vertical_social_resolution_delivery_manifest_writes_layout_jobs(tmp_path: Path) -> None:
    clip = _make_video(tmp_path / "wide.mp4", size="384x216")
    manifest_path = Path(render_vertical_social_resolution_delivery_manifest(
        [str(clip)],
        str(tmp_path / "social"),
        package_id="Social Delivery",
        safe_area_percent=0.82,
        reframing_mode="fit_pad",
        delivery_targets=[
            {"id": "Shorts", "label": "Shorts", "width": 720, "height": 1280, "platform": "youtube"},
            {"id": "Square", "width": 1080, "height": 1080},
        ],
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    first_job = manifest["render_jobs"][0]
    assert manifest["effect"] == "resolve21_vertical_social_resolution_delivery_manifest"
    assert manifest["package"]["package_id"] == "social_delivery"
    assert manifest["package"]["safe_area_percent"] == 0.82
    assert manifest["package"]["reframing_mode"] == "fit_pad"
    assert manifest["package"]["target_count"] == 2
    assert first_job["target"]["target_id"] == "shorts"
    assert first_job["source_layouts"][0]["layout_action"] in {"pad_top_bottom", "pad_left_right"}
    assert first_job["caption_and_ui_safe"]["subtitle_band"]["height"] > 0


def test_vertical_social_resolution_delivery_manifest_validation_and_clamps(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_vertical_social_resolution_delivery_manifest([], str(tmp_path / "out"))
    with pytest.raises(FileNotFoundError):
        render_vertical_social_resolution_delivery_manifest([str(tmp_path / "missing.mp4")], str(tmp_path / "out"))
    clip = _make_video(tmp_path / "clamp.mp4", size="320x180")
    manifest_path = Path(render_vertical_social_resolution_delivery_manifest(
        [str(clip)],
        str(tmp_path / "out"),
        safe_area_percent=2.0,
        reframing_mode="bad",
        delivery_targets=[{"id": "", "width": 10, "height": 99999, "platform_hint": "Bad Platform"}],
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    target = manifest["delivery_targets"][0]
    assert manifest["package"]["safe_area_percent"] == 1.0
    assert manifest["package"]["reframing_mode"] == "center_crop"
    assert target["target_id"] == "social_target_0"
    assert target["width"] == 240
    assert target["height"] == 4320
    assert target["safe_area_box"]["x"] == 0


def test_vertical_social_resolution_delivery_manifest_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_social")
    assert manifest["sources"][0]["source_probe"]["duration"] > 0
    assert manifest["render_jobs"][0]["source_layouts"][0]["asset_ref"] == manifest["sources"][0]["asset_ref"]


def test_vertical_social_resolution_delivery_manifest_reproduces_with_two_real_clips(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "pair_social")
    assert manifest["package"]["clip_count"] == 2
    refs = {source["asset_ref"] for source in manifest["sources"]}
    for job in manifest["render_jobs"]:
        assert {layout["asset_ref"] for layout in job["source_layouts"]} == refs
        assert job["validation"]["source_count"] == 2
        assert job["validation"]["manifest_ready"] is True


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(render_vertical_social_resolution_delivery_manifest([str(path) for path in paths], str(output_dir)))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        assert source["source_probe"]["width"] > 0
        assert source["asset_ref"]
    return manifest


def _make_video(path: Path, *, size: str) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=s={size}:r=12:d=1.0",
            "-f", "lavfi", "-i", "sine=frequency=770:duration=1.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path
