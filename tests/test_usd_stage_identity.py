from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.asset_identity import parse_entity_reference
from gemia.usd_stage_identity import build_openusd_stage_identity, write_openusd_stage_identity_package
from gemia.video.fusion_usd_hydra import render_fusion_usd_hydra_toolset_manifest


def test_build_openusd_stage_identity_from_manifest() -> None:
    identity = build_openusd_stage_identity(_manifest())
    assert identity["backend"] == "openusd_optional_local"
    assert identity["stage"]["stage_id"] == "shot_stage"
    assert identity["stage"]["stage_identifier"].startswith("gemia:usd_stage:shot_stage:")
    assert identity["stage"]["frame_range"] == [12, 96]
    assert identity["layer_stack"][0]["role"] == "root"
    assert len(identity["prim_identities"]) == 2
    parsed = parse_entity_reference(identity["prim_identities"][0]["entity_reference"])
    assert parsed.account_id == "usd_stage"
    assert parsed.asset_id == "plate_a"


def test_write_openusd_stage_identity_package(tmp_path: Path) -> None:
    manifest_path = tmp_path / "fusion_usd_hydra_toolset_manifest.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    identity_path = Path(write_openusd_stage_identity_package(str(manifest_path), str(tmp_path / "identity"), package_id="Unit Package"))
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    usda_path = Path(identity["files"]["usda_stage"])
    assert usda_path.exists()
    usda = usda_path.read_text(encoding="utf-8")
    assert "#usda 1.0" in usda
    assert "gemia:stageIdentifier" in usda
    assert "gemia:assetRef" in usda
    assert identity["stage"]["package_id"] == "unit_package"


def test_openusd_stage_identity_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="stage object"):
        build_openusd_stage_identity({"usd_layers": []})
    with pytest.raises(ValueError, match="at least one"):
        build_openusd_stage_identity({"stage": {"stage_id": "empty"}, "usd_layers": []})
    manifest_path = tmp_path / "missing.json"
    with pytest.raises(FileNotFoundError):
        write_openusd_stage_identity_package(str(manifest_path), str(tmp_path / "out"))


def test_openusd_stage_identity_reproduces_with_two_real_videos(tmp_path: Path) -> None:
    real_inputs = [Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")]
    if not all(path.exists() for path in real_inputs):
        pytest.skip("No two local real input videos found for OpenUSD stage identity testing")
    manifest_path = Path(render_fusion_usd_hydra_toolset_manifest([str(path) for path in real_inputs], str(tmp_path / "usd")))
    identity_path = Path(write_openusd_stage_identity_package(str(manifest_path), str(tmp_path / "identity"), package_id="real_stage"))
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    assert identity["stage"]["clip_count"] == 2
    assert len({prim["asset_ref"] for prim in identity["prim_identities"]}) == 2
    assert Path(identity["files"]["usda_stage"]).exists()


def _manifest() -> dict:
    return {
        "stage": {
            "stage_id": "Shot Stage",
            "root_prim": "/Shot_Stage",
            "up_axis": "Y",
            "meters_per_unit": 1.0,
            "frame_range": [96, 12],
        },
        "hydra_delegates": [{"delegate_id": "storm_preview"}, {"delegate_id": "karma_final"}],
        "usd_layers": [
            {
                "layer_id": "plate_a",
                "prim_path": "/Shot_Stage/shots/plate_a",
                "asset_ref": "clip_a:100:1.0:1920x1080",
                "source_path": "/tmp/clip_a.mp4",
                "usd_prim": {"type": "Xform", "kind": "component", "payload": "clip_a.usd"},
            },
            {
                "layer_id": "plate_b",
                "prim_path": "/Shot_Stage/shots/plate_b",
                "asset_ref": "clip_b:200:1.0:1920x1080",
                "source_path": "/tmp/clip_b.mp4",
                "usd_prim": {"type": "Xform", "kind": "component", "payload": "clip_b.usd"},
            },
        ],
    }


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
