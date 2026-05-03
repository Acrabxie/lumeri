from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.panomap_ilpd import render_panomap_ilpd_stereo_retarget_manifest


def test_panomap_ilpd_stereo_retarget_manifest_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.panomap_ilpd.render_panomap_ilpd_stereo_retarget_manifest" in catalog_for_prompt("video")


def test_panomap_ilpd_stereo_retarget_manifest_writes_controls(tmp_path: Path) -> None:
    clip = _make_video(tmp_path / "pano.mp4", size="384x192")
    manifest_path = Path(render_panomap_ilpd_stereo_retarget_manifest(
        [str(clip)],
        str(tmp_path / "manifest"),
        package_id="Panomap Scene",
        projection="Equirectangular",
        stereo_layout="Side By Side",
        retarget_presets=[
            {"id": "Hero", "yaw": 12, "pitch": -5, "roll": 2, "ilpd_mm": 62, "convergence_distance_m": 1.5},
            {"id": "Wide", "yaw_degrees": -20, "field_of_view_degrees": 210, "parallax_budget_percent": 4.5},
        ],
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "resolve21_panomap_ilpd_stereo_retarget_manifest"
    assert manifest["package"]["package_id"] == "panomap_scene"
    assert manifest["package"]["retarget_count"] == 2
    assert manifest["sources"][0]["panomap_readiness"]["looks_equirectangular"] is True
    assert manifest["retargets"][0]["panomap_controls"]["yaw_degrees"] == 12
    assert manifest["retargets"][0]["ilpd_controls"]["interpupillary_distance_mm"] == 62
    assert manifest["retargets"][1]["validation"]["ilpd_within_comfort_range"] is True


def test_panomap_ilpd_stereo_retarget_manifest_validation_and_clamps(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_panomap_ilpd_stereo_retarget_manifest([], str(tmp_path / "out"))
    with pytest.raises(FileNotFoundError):
        render_panomap_ilpd_stereo_retarget_manifest([str(tmp_path / "missing.mp4")], str(tmp_path / "out"))
    clip = _make_video(tmp_path / "flat.mp4", size="320x180")
    manifest_path = Path(render_panomap_ilpd_stereo_retarget_manifest(
        [str(clip)],
        str(tmp_path / "out"),
        retarget_presets=[{"id": "", "yaw": 999, "pitch": -999, "roll": 999, "fov": 999, "ilpd_mm": 999}],
    ))
    retarget = json.loads(manifest_path.read_text(encoding="utf-8"))["retargets"][0]
    assert retarget["panomap_controls"]["yaw_degrees"] == 180
    assert retarget["panomap_controls"]["pitch_degrees"] == -90
    assert retarget["panomap_controls"]["roll_degrees"] == 45
    assert retarget["panomap_controls"]["field_of_view_degrees"] == 220
    assert retarget["ilpd_controls"]["interpupillary_distance_mm"] == 75


def test_panomap_ilpd_stereo_retarget_manifest_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_panomap")
    assert manifest["sources"][0]["source_probe"]["duration"] > 0
    assert manifest["retargets"][0]["clip_asset_refs"] == [manifest["sources"][0]["asset_ref"]]


def test_panomap_ilpd_stereo_retarget_manifest_reproduces_with_two_real_clips(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "pair_panomap")
    assert manifest["package"]["clip_count"] == 2
    refs = {source["asset_ref"] for source in manifest["sources"]}
    for retarget in manifest["retargets"]:
        assert set(retarget["clip_asset_refs"]) == refs
        assert retarget["validation"]["source_count"] == 2


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(render_panomap_ilpd_stereo_retarget_manifest([str(path) for path in paths], str(output_dir)))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        assert source["source_probe"]["width"] > 0
        assert source["asset_ref"]
        assert "aspect_ratio" in source["panomap_readiness"]
    return manifest


def _make_video(path: Path, *, size: str) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=s={size}:r=12:d=1.0",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path
