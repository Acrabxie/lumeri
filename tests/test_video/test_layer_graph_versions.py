import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.layer_graph_versions import render_layer_list_node_graph_versions


def test_layer_list_node_graph_versions_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.layer_graph_versions.render_layer_list_node_graph_versions" in catalog_for_prompt("video")


def test_layer_list_node_graph_versions_writes_versions(tmp_path: Path) -> None:
    clips = [_make_video(tmp_path / "a.mp4", "red"), _make_video(tmp_path / "b.mp4", "blue")]
    manifest_path = Path(render_layer_list_node_graph_versions(
        [str(path) for path in clips],
        str(tmp_path / "graphs"),
        graph_id="scene_graph_01",
        node_graph_versions=[
            {"id": "base", "look": "neutral", "nodes": ["media_input", "primary_balance", "output"]},
            {"id": "pop", "look": "commercial", "nodes": ["contrast_curve", "serial_composite"]},
        ],
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "resolve21_layer_list_node_graph_versions"
    assert manifest["graph"]["graph_id"] == "scene_graph_01"
    assert manifest["graph"]["layer_count"] == 2
    assert manifest["graph"]["version_count"] == 2
    assert len(manifest["layers"]) == 2
    assert manifest["graph_versions"][0]["node_count"] == 6
    assert manifest["graph_versions"][1]["output_node_ids"]
    assert {layer["blend_mode"] for layer in manifest["layers"]} == {"normal", "over"}


def test_layer_list_node_graph_versions_validation(tmp_path: Path) -> None:
    clip = _make_video(tmp_path / "a.mp4", "green")
    with pytest.raises(ValueError, match="input_paths"):
        render_layer_list_node_graph_versions([], str(tmp_path / "out"))
    with pytest.raises(FileNotFoundError):
        render_layer_list_node_graph_versions([str(tmp_path / "missing.mp4")], str(tmp_path / "out"))
    manifest_path = Path(render_layer_list_node_graph_versions(
        [str(clip)],
        str(tmp_path / "out"),
        node_graph_versions=[{"id": "minimal", "nodes": []}],
    ))
    version = json.loads(manifest_path.read_text(encoding="utf-8"))["graph_versions"][0]
    kinds = [node["kind"] for node in version["nodes"]]
    assert kinds[0] == "media_input"
    assert kinds[-1] == "output"


def test_layer_list_node_graph_versions_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_graph")
    assert manifest["layers"][0]["source_probe"]["width"] > 0
    assert manifest["graph_versions"][0]["node_count"] >= 4


def test_layer_list_node_graph_versions_reproduces_with_timeline_pair(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "pair_graph")
    assert manifest["graph"]["layer_count"] == 2
    refs_by_version = []
    for version in manifest["graph_versions"]:
        refs_by_version.append({node["asset_ref"] for node in version["nodes"] if node["asset_ref"]})
    assert len(refs_by_version[0]) == 2
    assert refs_by_version[0] == refs_by_version[1]


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(render_layer_list_node_graph_versions(
        [str(path) for path in paths],
        str(output_dir),
        graph_id="real_media_graph",
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for layer in manifest["layers"]:
        assert layer["asset_ref"]
        assert layer["source_probe"]["duration_seconds"] > 0
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
