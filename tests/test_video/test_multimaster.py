import json
import subprocess
from pathlib import Path

import pytest

from gemia.registry import catalog_for_prompt, clear_catalog_cache
from gemia.video.multimaster import render_multimaster_trim_pass_manager


def test_multimaster_trim_pass_manager_is_planner_visible() -> None:
    clear_catalog_cache()
    catalog = catalog_for_prompt("video")
    assert "gemia.video.multimaster.render_multimaster_trim_pass_manager" in catalog


def test_multimaster_trim_pass_manager_writes_linked_deliverables(tmp_path: Path) -> None:
    source = _make_video(tmp_path / "source.mp4", duration=1.4)
    manifest_path = Path(render_multimaster_trim_pass_manager(
        str(source),
        str(tmp_path / "multimaster"),
        timeline_id="spot_master_001",
        trim_passes=[
            {"id": "hdr_show", "target": "hdr10_pq", "peak_nits": 1200, "start_trim_seconds": 0.0, "end_trim_seconds": 0.1},
            {"id": "sdr_broadcast", "target": "sdr_rec709", "peak_nits": 100, "start_trim_seconds": 0.1, "end_trim_seconds": 0.1},
        ],
        review_proxy_long_edge=160,
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["effect"] == "resolve21_multimaster_trim_pass_manager"
    assert manifest["timeline"]["timeline_id"] == "spot_master_001"
    assert manifest["timeline"]["linked_deliverable_count"] == 2
    assert manifest["sync_groups"][0]["members"] == ["hdr_show", "sdr_broadcast"]
    assert manifest["deliverables"][0]["color_delivery"]["gamut"] == "rec2020"
    assert manifest["deliverables"][1]["color_delivery"]["gamut"] == "rec709"
    assert manifest["deliverables"][0]["asset_identity"].startswith("spot_master_001:hdr_show:")
    for item in manifest["deliverables"]:
        proxy = Path(item["review_proxy_path"])
        assert proxy.exists()
        assert item["review_proxy_probe"]["duration_seconds"] > 0


def test_multimaster_trim_pass_manager_validation(tmp_path: Path) -> None:
    source = _make_video(tmp_path / "source.mp4", duration=0.7)
    with pytest.raises(FileNotFoundError):
        render_multimaster_trim_pass_manager(str(tmp_path / "missing.mp4"), str(tmp_path / "out"))
    with pytest.raises(ValueError, match="review_proxy_long_edge"):
        render_multimaster_trim_pass_manager(str(source), str(tmp_path / "out"), review_proxy_long_edge=0)
    with pytest.raises(ValueError, match="smaller than input duration"):
        render_multimaster_trim_pass_manager(
            str(source),
            str(tmp_path / "out"),
            trim_passes=[{"id": "bad", "start_trim_seconds": 0.5, "end_trim_seconds": 0.5}],
        )


def test_multimaster_trim_pass_manager_reproduces_with_demo_video(tmp_path: Path) -> None:
    manifest = _run_real_repro(Path("inputs/demo.mp4"), tmp_path / "demo_multimaster")
    assert manifest["timeline"]["source_probe"]["duration_seconds"] > 0
    assert len(manifest["deliverables"]) == 2


def test_multimaster_trim_pass_manager_reproduces_with_timeline_video(tmp_path: Path) -> None:
    manifest = _run_real_repro(Path("inputs/gemia_timeline_demo.mp4"), tmp_path / "timeline_multimaster")
    fingerprints = {item["asset_identity"].split(":", 2)[2] for item in manifest["deliverables"]}
    assert len(fingerprints) == 1


def _run_real_repro(video_path: Path, output_dir: Path) -> dict:
    if not video_path.exists():
        pytest.skip(f"real local video not found: {video_path}")
    manifest_path = Path(render_multimaster_trim_pass_manager(
        str(video_path),
        str(output_dir),
        timeline_id=f"{video_path.stem}_multimaster",
        trim_passes=[
            {"id": "hdr_trim", "target": "hdr10_pq", "start_trim_seconds": 0.0, "end_trim_seconds": 0.05},
            {"id": "sdr_trim", "target": "sdr_rec709", "start_trim_seconds": 0.05, "end_trim_seconds": 0.05},
        ],
        review_proxy_long_edge=180,
    ))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for deliverable in manifest["deliverables"]:
        assert Path(deliverable["review_proxy_path"]).exists()
        assert deliverable["review_proxy_probe"]["width"] > 0
        assert deliverable["review_proxy_probe"]["height"] > 0
    return manifest


def _make_video(path: Path, *, duration: float) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=s=320x180:r=15:d={duration}",
            "-f", "lavfi", "-i", f"sine=frequency=660:duration={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-shortest", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path
