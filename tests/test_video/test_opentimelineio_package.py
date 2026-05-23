from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.opentimelineio_package import (
    import_opentimelineio_timeline_package,
    render_opentimelineio_timeline_package_backend,
)


def test_opentimelineio_timeline_package_backend_is_planner_visible() -> None:
    clear_catalog_cache()
    assert "gemia.video.opentimelineio_package.render_opentimelineio_timeline_package_backend" in catalog_for_prompt("video")


def test_opentimelineio_timeline_package_backend_writes_sidecars(tmp_path: Path) -> None:
    clip_a = _make_video(tmp_path / "hero.mp4", size="384x216")
    clip_b = _make_video(tmp_path / "detail.mp4", size="320x180")
    manifest_path = Path(render_opentimelineio_timeline_package_backend(
        [str(clip_a), str(clip_b)],
        str(tmp_path / "otio"),
        package_id="OTIO Review",
        timeline_name="Launch Cut",
        frame_rate=30,
        markers=[{"time": 0.2, "name": "Hook"}, {"time": 99, "comment": "End"}],
        track_layout=[{"name": "V1", "kind": "video", "source_indexes": [0, 1]}],
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "github_opentimelineio_timeline_package_backend"
    assert manifest["package"]["package_id"] == "otio_review"
    assert manifest["package"]["requires_opentimelineio_runtime"] is False
    assert manifest["timeline"]["frame_rate"] == 30
    assert manifest["timeline"]["tracks"][0]["clips"][1]["start_seconds"] > 0
    assert manifest["timeline"]["markers"][1]["time_seconds"] == manifest["timeline"]["duration_seconds"]
    assert (manifest_path.parent / "timeline.otio.json").exists()
    assert (manifest_path.parent / "relink_map.json").exists()


def test_opentimelineio_timeline_package_backend_imports_manifest_and_otio_sidecar(tmp_path: Path) -> None:
    clip = _make_video(tmp_path / "single.mp4", size="320x180")
    manifest_path = Path(render_opentimelineio_timeline_package_backend([str(clip)], str(tmp_path / "otio")))
    manifest = import_opentimelineio_timeline_package(manifest_path)
    otio = import_opentimelineio_timeline_package(manifest_path.parent / "timeline.otio.json")
    assert manifest["package"]["clip_count"] == 1
    assert otio["effect"] == "github_opentimelineio_timeline_package_backend"
    assert otio["package"]["clip_count"] == 1
    assert otio["media_references"][0]["metadata"]["asset_ref"]


def test_opentimelineio_timeline_package_backend_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4")], tmp_path / "demo_otio")
    assert manifest["timeline"]["duration_seconds"] > 0
    assert manifest["media_references"][0]["metadata"]["asset_ref"]


def test_opentimelineio_timeline_package_backend_reproduces_with_two_real_clips(tmp_path: Path) -> None:
    manifest = _run_real_repro([Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")], tmp_path / "pair_otio")
    assert manifest["package"]["clip_count"] == 2
    assert manifest["package"]["track_count"] >= 1
    refs = {ref["media_ref_id"] for ref in manifest["media_references"]}
    for track in manifest["timeline"]["tracks"]:
        assert {clip["media_ref_id"] for clip in track["clips"]}.issubset(refs)


def _run_real_repro(paths: list[Path], output_dir: Path) -> dict:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(f"real local video not found: {', '.join(missing)}")
    manifest_path = Path(render_opentimelineio_timeline_package_backend([str(path) for path in paths], str(output_dir)))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for ref in manifest["media_references"]:
        assert ref["metadata"]["cache_key"]
        assert Path(ref["source_path"]).exists()
    return manifest


def _make_video(path: Path, *, size: str) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=s={size}:r=12:d=1.0",
            "-f", "lavfi", "-i", "sine=frequency=660:duration=1.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path
