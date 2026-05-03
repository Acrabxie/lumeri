import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.group_versions import render_group_versions_color_workflow


def test_group_versions_color_workflow_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.group_versions.render_group_versions_color_workflow" in catalog_for_prompt("video")


def test_group_versions_color_workflow_writes_groups(tmp_path: Path) -> None:
    clips = [_make_video(tmp_path / "hero_a.mp4", "red"), _make_video(tmp_path / "hero_b.mp4", "blue")]
    manifest_path = Path(render_group_versions_color_workflow(
        [str(path) for path in clips],
        str(tmp_path / "groups"),
        group_id="scene_color_groups",
        group_assignments={"hero_a.mp4": "hero", "hero_b.mp4": "hero"},
        grade_versions=[
            {"id": "base", "look": "neutral", "temperature": 0, "contrast": 1.0},
            {"id": "warm", "look": "warm", "temperature": 300, "contrast": 1.1, "saturation": 1.05},
        ],
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "resolve21_group_versions_color_workflow"
    assert manifest["workflow"]["clip_count"] == 2
    assert manifest["workflow"]["group_count"] == 1
    assert manifest["workflow"]["version_ids"] == ["base", "warm"]
    group = manifest["groups"][0]
    assert group["clip_count"] == 2
    assert group["grade_versions"][1]["grade"]["temperature"] == 300
    assert len(group["grade_versions"][0]["node_recipe"]) == 4


def test_group_versions_color_workflow_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input_paths"):
        render_group_versions_color_workflow([], str(tmp_path / "out"))
    with pytest.raises(FileNotFoundError):
        render_group_versions_color_workflow([str(tmp_path / "missing.mp4")], str(tmp_path / "out"))
    clip = _make_video(tmp_path / "solo.mp4", "green")
    manifest_path = Path(render_group_versions_color_workflow([str(clip)], str(tmp_path / "out"), grade_versions=[{"id": ""}]))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["workflow"]["version_count"] == 1
    assert manifest["groups"][0]["grade_versions"][0]["version_id"] == "grade_version_0"


def test_group_versions_color_workflow_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_groups")
    assert manifest["clips"][0]["source_probe"]["width"] > 0
    assert manifest["groups"][0]["grade_versions"][0]["group_grade_ref"]


def test_group_versions_color_workflow_reproduces_with_timeline_pair(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "pair_groups")
    assert manifest["workflow"]["clip_count"] == 2
    assert manifest["workflow"]["group_count"] == 2
    refs = {clip["asset_ref"] for clip in manifest["clips"]}
    grouped_refs = {ref for group in manifest["groups"] for ref in group["asset_refs"]}
    assert refs == grouped_refs


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(render_group_versions_color_workflow(
        [str(path) for path in paths],
        str(output_dir),
        group_id="real_color_groups",
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for clip in manifest["clips"]:
        assert clip["asset_ref"]
        assert clip["source_probe"]["duration_seconds"] > 0
    return manifest


def _make_video(path: Path, color: str) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=15:d=0.8",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path
