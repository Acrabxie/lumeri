from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.fusion_effect_animation import render_animate_fusion_effects_edit_page_manifest


def _make_video(path: Path) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=s=320x180:r=24:d=2.0",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(
        render_animate_fusion_effects_edit_page_manifest([str(path) for path in paths], str(output_dir))
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        assert Path(source["source_path"]).exists()
        assert source["source_probe"]["media_kind"] == "video"
        assert source["cache_key"]
    return manifest


def test_fusion_effect_animation_manifest_is_planner_visible() -> None:
    clear_catalog_cache()
    assert (
        "gemia.video.fusion_effect_animation.render_animate_fusion_effects_edit_page_manifest"
        in catalog_for_prompt("video")
    )


def test_fusion_effect_animation_writes_bounded_keyframes_and_normalizes_ids(tmp_path: Path) -> None:
    clip = _make_video(tmp_path / "test_clip.mp4")
    manifest_path = Path(
        render_animate_fusion_effects_edit_page_manifest(
            [str(clip)],
            str(tmp_path / "fusion_anim_out"),
            package_id="FusionEditPageProject",
            preset_name="CurveReview",
            effect_controls={
                "GlowPulse": {
                    "fusion_effect": "Glow",
                    "parameter": "Blend",
                    "curve_editor": "EditPageCurves",
                    "duration_policy": "EffectRegion",
                    "keyframes": [
                        {"time_fraction": -1.0, "value": -12, "easing": "hold"},
                        {"time_fraction": 0.5, "value": 0.75, "easing": "EaseInOut"},
                        {"time_fraction": 2.0, "value": 15, "easing": "bad"},
                    ],
                },
                "TransformZoom": {
                    "fusion_effect": "Transform",
                    "parameter": "Size",
                    "keyframes": [{"time_fraction": 0.3, "value": 1.05}],
                },
            },
        )
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "resolve21_animate_fusion_effects_edit_page_manifest"
    assert manifest["schema_version"] == 1
    assert manifest["package"]["package_id"] == "fusion_edit_page_project"
    assert manifest["package"]["preset_name"] == "curve_review"
    assert manifest["sources"][0]["source_probe"]["width"] > 0
    assert manifest["sources"][0]["cache_key"]
    assert manifest["sources"][0]["asset_ref"]

    controls = manifest["fusion_effect_controls"]
    assert len(controls) == 2
    glow = next(control for control in controls if control["id"] == "glow_pulse")
    assert glow["label"] == "GlowPulse"
    assert glow["fusion_effect"] == "Glow"
    assert glow["parameter"] == "blend"
    assert glow["curve_editor"] == "edit_page_curves"
    assert glow["duration_policy"] == "effect_region"
    assert glow["keyframes"] == [
        {"time_fraction": 0.0, "value": -10.0, "easing": "hold"},
        {"time_fraction": 0.5, "value": 0.75, "easing": "ease_in_out"},
        {"time_fraction": 1.0, "value": 10.0, "easing": "linear"},
    ]

    transform = next(control for control in controls if control["id"] == "transform_zoom")
    assert transform["parameter"] == "size"
    assert len(transform["keyframes"]) == 2
    assert transform["keyframes"][0]["time_fraction"] == 0.0
    assert transform["keyframes"][1]["time_fraction"] == 1.0
    assert manifest["clip_assignments"][0]["analysis_window"]["estimated_frames"] >= 1


def test_fusion_effect_animation_rejects_bad_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_animate_fusion_effects_edit_page_manifest([], str(tmp_path))
    with pytest.raises(FileNotFoundError):
        render_animate_fusion_effects_edit_page_manifest([str(tmp_path / "missing.mp4")], str(tmp_path))
    directory = tmp_path / "folder"
    directory.mkdir()
    with pytest.raises(OSError):
        render_animate_fusion_effects_edit_page_manifest([str(directory)], str(tmp_path))

    audio_file = tmp_path / "audio.mp3"
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.5",
            "-c:a",
            "libmp3lame",
            str(audio_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    with pytest.raises(ValueError, match="visual media"):
        render_animate_fusion_effects_edit_page_manifest([str(audio_file)], str(tmp_path))


def test_fusion_effect_animation_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_fusion_effects")
    assert manifest["package"]["clip_count"] == 1
    assert len(manifest["fusion_effect_controls"]) == 2
    assert manifest["clip_assignments"][0]["effect_control"]["id"] == "glow_intensity_ramp"


def test_fusion_effect_animation_reproduces_with_two_real_clips(tmp_path: Path) -> None:
    manifest = _run_real_repro(
        [Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")],
        tmp_path / "pair_fusion_effects",
    )
    assert manifest["package"]["clip_count"] == 2
    assert len(manifest["clip_assignments"]) == 2
    assert {item["asset_ref"] for item in manifest["clip_assignments"]} == {
        source["asset_ref"] for source in manifest["sources"]
    }
    assert {item["effect_control"]["id"] for item in manifest["clip_assignments"]} == {
        "glow_intensity_ramp",
        "transform_zoom_pulse",
    }
