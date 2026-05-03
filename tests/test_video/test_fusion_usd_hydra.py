from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.fusion_usd_hydra import render_fusion_usd_hydra_toolset_manifest


def test_fusion_usd_hydra_toolset_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.fusion_usd_hydra.render_fusion_usd_hydra_toolset_manifest" in catalog_for_prompt("video")


def test_fusion_usd_hydra_toolset_writes_manifest(tmp_path: Path) -> None:
    clip = _make_video(tmp_path / "usd_source.mp4")
    manifest_path = Path(render_fusion_usd_hydra_toolset_manifest(
        [str(clip)],
        str(tmp_path / "manifest"),
        stage_id="Shot USD Stage",
        hydra_delegates=[{"id": "Storm", "quality": "draft"}, {"id": "Karma", "quality": "final"}],
        frame_range=(96, 12),
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "resolve21_fusion_usd_hydra_toolset_manifest"
    assert manifest["stage"]["stage_id"] == "shot_usd_stage"
    assert manifest["stage"]["frame_range"] == [12, 96]
    assert manifest["hydra_delegates"][1]["supports_motion_blur"] is True
    layer = manifest["usd_layers"][0]
    assert layer["prim_path"].startswith("/shot_usd_stage/shots/media_plane_00_")
    assert layer["usd_prim"]["variant_sets"]["resolution"] == "proxy"
    assert layer["fusion_loader"]["tool"] == "USDStageLoader"


def test_fusion_usd_hydra_toolset_validation_and_defaults(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_fusion_usd_hydra_toolset_manifest([], str(tmp_path / "out"))
    with pytest.raises(FileNotFoundError):
        render_fusion_usd_hydra_toolset_manifest([str(tmp_path / "missing.mp4")], str(tmp_path / "out"))
    clip = _make_video(tmp_path / "source.mp4")
    manifest_path = Path(render_fusion_usd_hydra_toolset_manifest(
        [str(clip)],
        str(tmp_path / "out"),
        hydra_delegates=[{"id": "Odd Delegate", "quality": "cinema"}],
        meters_per_unit=-3,
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["stage"]["meters_per_unit"] == 0.001
    assert manifest["hydra_delegates"][0]["delegate_id"] == "odd_delegate"
    assert manifest["hydra_delegates"][0]["quality"] == "draft"


def test_fusion_usd_hydra_toolset_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_usd")
    assert manifest["usd_layers"][0]["source_probe"]["duration"] > 0
    assert manifest["stage_references"][0]["asset_ref"]


def test_fusion_usd_hydra_toolset_reproduces_with_two_real_clips(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "pair_usd")
    assert manifest["stage"]["clip_count"] == 2
    prim_paths = {item["prim_path"] for item in manifest["stage_references"]}
    assert len(prim_paths) == 2
    assert len(manifest["fusion_tools"][1]["delegate_options"]) == 2


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(render_fusion_usd_hydra_toolset_manifest([str(path) for path in paths], str(output_dir)))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for source in manifest["usd_layers"]:
        assert source["source_probe"]["width"] > 0
        assert source["source_probe"]["height"] > 0
        assert source["asset_ref"]
    return manifest


def _make_video(path: Path) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=s=180x100:r=12:d=1.0",
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
