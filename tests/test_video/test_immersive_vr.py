import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.immersive_vr import render_immersive_vr_delivery_manifest


def test_immersive_vr_delivery_manifest_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.immersive_vr.render_immersive_vr_delivery_manifest" in catalog_for_prompt("video")


def test_immersive_vr_delivery_manifest_writes_deliverables(tmp_path: Path) -> None:
    clip = _make_video(tmp_path / "vr.mp4", "purple", size="384x192")
    manifest_path = Path(render_immersive_vr_delivery_manifest(
        [str(clip)],
        str(tmp_path / "vr_manifest"),
        package_id="scene_vr",
        target_platforms=["Quest Review", "Web 360"],
        deliverables=[
            {"id": "mono", "layout": "mono_equirectangular", "stereo_mode": "mono"},
            {"id": "stereo", "layout": "stereo_side_by_side", "stereo_mode": "stereo", "spatial_audio": True},
        ],
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "resolve21_immersive_vr_delivery_manifest"
    assert manifest["package"]["package_id"] == "scene_vr"
    assert manifest["package"]["deliverable_count"] == 2
    assert manifest["sources"][0]["vr_readiness"]["looks_equirectangular"] is True
    assert manifest["deliverables"][1]["metadata"]["spatial_audio"] is True
    assert manifest["deliverables"][0]["clip_asset_refs"] == [manifest["sources"][0]["asset_ref"]]


def test_immersive_vr_delivery_manifest_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_immersive_vr_delivery_manifest([], str(tmp_path / "out"))
    with pytest.raises(FileNotFoundError):
        render_immersive_vr_delivery_manifest([str(tmp_path / "missing.mp4")], str(tmp_path / "out"))
    clip = _make_video(tmp_path / "flat.mp4", "orange", size="320x180")
    manifest_path = Path(render_immersive_vr_delivery_manifest([str(clip)], str(tmp_path / "out"), deliverables=[{"id": ""}]))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["package"]["deliverable_count"] == 1
    assert manifest["deliverables"][0]["deliverable_id"] == "mono_equirectangular"


def test_immersive_vr_delivery_manifest_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_vr")
    assert manifest["sources"][0]["source_probe"]["width"] > 0
    assert manifest["deliverables"][0]["review_key"]


def test_immersive_vr_delivery_manifest_reproduces_with_timeline_pair(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "pair_vr")
    assert manifest["package"]["clip_count"] == 2
    refs = {source["asset_ref"] for source in manifest["sources"]}
    for deliverable in manifest["deliverables"]:
        assert set(deliverable["clip_asset_refs"]) == refs


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(render_immersive_vr_delivery_manifest(
        [str(path) for path in paths],
        str(output_dir),
        package_id="real_immersive_delivery",
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        assert source["asset_ref"]
        assert source["source_probe"]["duration_seconds"] > 0
    return manifest


def _make_video(path: Path, color: str, *, size: str) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=15:d=0.8",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path
