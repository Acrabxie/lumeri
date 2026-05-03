from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.fusion_macro_inspector import render_fusion_macro_editor_inspector_manifest


def test_fusion_macro_inspector_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.fusion_macro_inspector.render_fusion_macro_editor_inspector_manifest" in catalog_for_prompt("video")


def test_fusion_macro_inspector_writes_controls_groups_and_bindings(tmp_path: Path) -> None:
    clip = _make_video(tmp_path / "macro_source.mp4")
    manifest_path = Path(render_fusion_macro_editor_inspector_manifest(
        [str(clip)],
        str(tmp_path / "macro"),
        macro_id="Client Macro 01",
        controls=[
            {"id": "Glow Mix", "type": "float", "default": 0.4, "minimum": 0, "maximum": 2, "publish_group": "Look"},
            {"id": "Mode", "type": "enum", "default": "soft", "options": ["soft", "hard"], "publish_group": "Behavior"},
        ],
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "resolve21_fusion_macro_editor_inspector_manifest"
    assert manifest["macro"]["macro_id"] == "client_macro_01"
    assert manifest["macro"]["control_count"] == 2
    assert {group["label"] for group in manifest["inspector"]["publish_groups"]} == {"Look", "Behavior"}
    assert {binding["control_id"] for binding in manifest["template_bindings"]} == {"glow_mix", "mode"}
    assert manifest["sources"][0]["asset_ref"]


def test_fusion_macro_inspector_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_fusion_macro_editor_inspector_manifest([], str(tmp_path / "out"))
    with pytest.raises(FileNotFoundError):
        render_fusion_macro_editor_inspector_manifest([str(tmp_path / "missing.mp4")], str(tmp_path / "out"))
    clip = _make_video(tmp_path / "source.mp4")
    manifest_path = Path(render_fusion_macro_editor_inspector_manifest(
        [str(clip)],
        str(tmp_path / "out"),
        controls=[{"id": "bad_range", "type": "float", "minimum": 3, "maximum": 3}],
    ))
    hints = json.loads(manifest_path.read_text(encoding="utf-8"))["inspector"]["validation_hints"]
    assert "bad_range has no usable numeric range" in hints


def test_fusion_macro_inspector_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_macro")
    assert manifest["sources"][0]["source_probe"]["width"] > 0
    assert manifest["macro"]["clip_count"] == 1


def test_fusion_macro_inspector_reproduces_with_two_real_clips(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "pair_macro")
    assert manifest["macro"]["clip_count"] == 2
    refs = {source["asset_ref"] for source in manifest["sources"]}
    assert len(refs) == 2
    assert manifest["inspector"]["validation_hints"] == ["macro inspector manifest is ready for review"]


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(render_fusion_macro_editor_inspector_manifest([str(path) for path in paths], str(output_dir)))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        assert source["source_probe"]["duration_seconds"] > 0
        assert source["asset_ref"]
    return manifest


def _make_video(path: Path) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=s=180x100:r=12:d=0.8",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.8",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path
