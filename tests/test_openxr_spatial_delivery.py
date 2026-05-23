from __future__ import annotations

import json
from pathlib import Path

import pytest

from gemia.openxr_spatial_delivery import build_openxr_spatial_delivery_profile, write_openxr_spatial_delivery_package
from gemia.video.blended_immersive_delivery_pip_script_scene import render_blended_immersive_delivery_pip_script_scene


def test_build_openxr_spatial_delivery_profile_from_manifest_set() -> None:
    profile = build_openxr_spatial_delivery_profile(_manifests(), profile_id="Unit XR")
    assert profile["backend"] == "openxr_optional_local"
    assert profile["profile"]["profile_id"] == "unit_xr"
    assert profile["profile"]["view_configuration"] == "primary_stereo"
    assert profile["validation"]["has_immersive_render_passes"] is True
    assert profile["validation"]["has_delivery_swapchains"] is True
    assert profile["validation"]["has_panomap_controls"] is True
    assert any(action["name"] == "toggle_picture_in_picture" for action in profile["action_sets"][0]["actions"])


def test_write_openxr_spatial_delivery_package(tmp_path: Path) -> None:
    paths = []
    for index, manifest in enumerate(_manifests()):
        path = tmp_path / f"manifest_{index}.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        paths.append(str(path))
    profile_path = Path(write_openxr_spatial_delivery_package(paths, str(tmp_path / "openxr"), profile_id="package"))
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    action_manifest = Path(profile["files"]["action_manifest"])
    assert action_manifest.exists()
    assert profile["profile"]["profile_identifier"].startswith("gemia:openxr:package:")
    assert json.loads(action_manifest.read_text(encoding="utf-8"))["action_sets"][0]["set_id"] == "gemia_spatial_review"


def test_openxr_spatial_delivery_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="manifest_paths"):
        write_openxr_spatial_delivery_package([], str(tmp_path / "out"))
    with pytest.raises(FileNotFoundError):
        write_openxr_spatial_delivery_package([str(tmp_path / "missing.json")], str(tmp_path / "out"))
    with pytest.raises(ValueError, match="asset_ref"):
        build_openxr_spatial_delivery_profile([{"effect": "empty", "sources": []}])


def test_openxr_spatial_delivery_reproduces_from_blended_real_scene(tmp_path: Path) -> None:
    real_inputs = [Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")]
    if not all(path.exists() for path in real_inputs):
        pytest.skip("No two local real input videos found for OpenXR spatial delivery testing")
    output = tmp_path / "scene.mp4"
    render_blended_immersive_delivery_pip_script_scene([str(path) for path in real_inputs], str(output), scene_id="openxr_real", max_seconds=0.5)
    metadata = json.loads(output.with_suffix(".blended_immersive_delivery_pip_script.json").read_text(encoding="utf-8"))
    profile_path = Path(write_openxr_spatial_delivery_package(list(metadata["components"].values()), str(tmp_path / "openxr"), profile_id="real_openxr"))
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    assert profile["profile"]["clip_count"] == 2
    assert profile["validation"]["shared_asset_ref_count"] == 2
    assert profile["validation"]["has_pip_actions"] is True
    assert Path(profile["files"]["action_manifest"]).exists()


def _manifests() -> list[dict]:
    sources = [
        {"clip_id": "a", "asset_ref": "a:100:1.0:1920x1080", "source_path": "/tmp/a.mp4", "source_probe": {"width": 1920, "height": 1080, "duration": 1.0, "has_audio": True}},
        {"clip_id": "b", "asset_ref": "b:200:1.0:1920x1080", "source_path": "/tmp/b.mp4", "source_probe": {"width": 1920, "height": 1080, "duration": 1.0, "has_audio": True}},
    ]
    return [
        {"effect": "resolve21_apple_immersive_foveated_rendering_manifest", "sources": sources, "render_passes": [{"pass_id": "preview", "profile": {"eye_buffer_width": 4320, "eye_buffer_height": 4320, "foveation": "balanced"}, "device_profile": "apple_vision_pro"}]},
        {"effect": "resolve21_mainconcept_h265_mvhevc_delivery_manifest", "sources": sources, "deliverables": [{"deliverable_id": "mvhevc", "clip_asset_refs": ["a:100:1.0:1920x1080", "b:200:1.0:1920x1080"], "mainconcept_settings": {"codec": "mv-hevc", "container": "mov", "view_count": 2, "target_bitrate_mbps": 80}}]},
        {"effect": "resolve21_panomap_ilpd_stereo_retarget_manifest", "sources": sources, "retargets": [{"retarget_id": "comfort"}]},
        {"effect": "resolve21_picture_in_picture_resolvefx_layout", "sources": sources, "layouts": [{"layout_id": "pip"}]},
        {"effect": "resolve21_finaldraft_intelliscript_ingest_manifest", "sources": sources, "assignments": [{"assignment_id": "scene"}]},
    ]
