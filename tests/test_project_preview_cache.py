"""Content-addressed proxy-segment cache regression tests."""
from __future__ import annotations

import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gemia.project_model import empty_project
from gemia.project_render import (
    _PREVIEW_CACHE_LOCKS,
    _empty_cache_stats,
    _materialize_preview_segment,
    _preview_segment_cache_key,
    _preview_segment_spec,
    _valid_cached_segment,
    ffprobe_media,
    render_project_preview,
)
from gemia.project_store import ProjectStore


def _image(path: Path, color: str) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=160x90:d=1",
            "-frames:v",
            "1",
            str(path),
        ],
        capture_output=True,
        check=True,
    )


def _store(tmp_path: Path, source: Path) -> ProjectStore:
    project = empty_project(title="preview cache test")
    project["timeline"].update({"width": 320, "height": 180, "fps": 12.0})
    project["assets"] = [
        {
            "id": "image_asset",
            "asset_id": "image_asset",
            "name": "shape",
            "media_kind": "image",
            "mime_type": "image/png",
            "source_path": str(source),
            "duration": 1.0,
            "metadata": {},
        }
    ]
    project["timeline"]["clips"] = [
        {
            "id": "image_clip",
            "asset_id": "image_asset",
            "track_id": "V1",
            "media_kind": "image",
            "name": "shape",
            "start": 0.0,
            "duration": 1.0,
            "source_in": 0.0,
            "source_out": 1.0,
            "enabled": True,
            "effects": {},
        }
    ]
    project["timeline"]["duration"] = 1.0
    store = ProjectStore(tmp_path / "projects")
    store.create("cache_test", seed=project)
    return store


def _render(store: ProjectStore, output: Path, label: str) -> dict:
    return render_project_preview(
        store,
        "cache_test",
        output_root=output,
        label=label,
        max_long_edge=320,
    )


def test_cache_reuses_segments_across_project_patch_sequence(tmp_path: Path) -> None:
    source = tmp_path / "shape.png"
    _image(source, "0x5fc6de")
    store = _store(tmp_path, source)
    output = tmp_path / "output"

    first = _render(store, output, "first")
    assert first["segment_cache"] == {
        "schema": "lumeri.preview-segment-cache.v1",
        "segments_total": 1,
        "hits": 0,
        "misses": 1,
        "rebuilds": 0,
        "bypassed": 0,
        "hit_ratio": 0.0,
    }

    store.apply_patches(
        "cache_test",
        [
            {
                "version": 1,
                "ops": [
                    {
                        "op": "add_marker",
                        "marker_id": "review",
                        "time": 0.5,
                        "label": "review",
                    }
                ],
            }
        ],
        session_id="cache-test",
        script_hash="marker-only-patch",
    )
    second = _render(store, output, "after-patch")

    assert second["patch_seq"] == 1
    assert second["segment_cache"]["hits"] == 1
    assert second["segment_cache"]["misses"] == 0
    assert second["segment_cache"]["hit_ratio"] == 1.0
    assert Path(first["preview_path"]).read_bytes() == Path(second["preview_path"]).read_bytes()


def test_cache_invalidates_when_source_content_changes(tmp_path: Path) -> None:
    source = tmp_path / "shape.png"
    _image(source, "0x5fc6de")
    store = _store(tmp_path, source)
    output = tmp_path / "output"
    first = _render(store, output, "first")

    _image(source, "0xff3355")
    second = _render(store, output, "source-changed")

    assert second["segment_cache"]["hits"] == 0
    assert second["segment_cache"]["misses"] == 1
    assert Path(first["preview_path"]).read_bytes() != Path(second["preview_path"]).read_bytes()


def test_corrupt_cache_entry_is_rebuilt_without_render_failure(tmp_path: Path) -> None:
    source = tmp_path / "shape.png"
    _image(source, "0x5fc6de")
    store = _store(tmp_path, source)
    output = tmp_path / "output"
    _render(store, output, "first")

    cache_files = list((output / "runtime" / ".preview-segment-cache" / "v1").glob("*/*.mp4"))
    assert len(cache_files) == 1
    cache_files[0].write_bytes(b"corrupt")

    recovered = _render(store, output, "recovered")
    assert recovered["segment_cache"]["misses"] == 1
    assert recovered["segment_cache"]["rebuilds"] == 1
    assert recovered["segment_cache"]["bypassed"] == 0
    assert float(ffprobe_media(recovered["preview_path"])["format"]["duration"]) > 0


def test_unavailable_cache_filesystem_falls_back_to_work_segment(tmp_path: Path) -> None:
    source = tmp_path / "shape.png"
    _image(source, "0x5fc6de")
    store = _store(tmp_path, source)
    output = tmp_path / "output"
    runtime = output / "runtime"
    runtime.mkdir(parents=True)
    (runtime / ".preview-segment-cache").write_text("not a directory", encoding="utf-8")

    result = _render(store, output, "cache-bypass")

    assert result["segment_cache"]["misses"] == 1
    assert result["segment_cache"]["bypassed"] == 1
    assert Path(result["preview_path"]).is_file()


def test_cache_key_covers_trim_canvas_fps_and_media_kind() -> None:
    base = dict(
        source={"kind": "file", "sha256": "a" * 64, "bytes": 100},
        source_in=0.0,
        duration=1.0,
        width=320,
        height=180,
        fps=12.0,
        media_kind="video",
    )
    keys = {
        _preview_segment_cache_key(_preview_segment_spec(**base)),
        _preview_segment_cache_key(_preview_segment_spec(**{**base, "source_in": 0.25})),
        _preview_segment_cache_key(_preview_segment_spec(**{**base, "duration": 0.75})),
        _preview_segment_cache_key(_preview_segment_spec(**{**base, "width": 640})),
        _preview_segment_cache_key(_preview_segment_spec(**{**base, "fps": 24.0})),
        _preview_segment_cache_key(_preview_segment_spec(**{**base, "media_kind": "image"})),
    }
    assert len(keys) == 6


def test_concurrent_same_key_encodes_once_and_publishes_valid_entry(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    cache_spec = _preview_segment_spec(
        source={"kind": "file", "sha256": "b" * 64, "bytes": 321},
        source_in=0.0,
        duration=1.0,
        width=320,
        height=180,
        fps=12.0,
        media_kind="video",
    )
    key = _preview_segment_cache_key(cache_spec)
    barrier = threading.Barrier(2)
    count_lock = threading.Lock()
    encode_count = 0

    def materialize(index: int) -> tuple[Path, dict[str, int]]:
        stats = _empty_cache_stats()

        def encode(output: Path) -> None:
            nonlocal encode_count
            with count_lock:
                encode_count += 1
            time.sleep(0.05)
            output.write_bytes(b"one deterministic encoded segment")

        barrier.wait(timeout=2)
        result = _materialize_preview_segment(
            cache_root=cache_root,
            fallback_path=tmp_path / f"work-{index}" / "segment.mp4",
            cache_spec=cache_spec,
            stats=stats,
            render=encode,
        )
        return result, stats

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(materialize, (1, 2)))

    cache_dir = cache_root / key[:2]
    cache_path = cache_dir / f"{key}.mp4"
    metadata_path = cache_dir / f"{key}.json"
    assert encode_count == 1
    assert sorted(item[1]["hits"] for item in results) == [0, 1]
    assert sorted(item[1]["misses"] for item in results) == [0, 1]
    assert all(item[0].read_bytes() == b"one deterministic encoded segment" for item in results)
    assert _valid_cached_segment(cache_path, metadata_path, key)
    assert list(cache_dir.glob(".*.tmp.*")) == []
    assert _PREVIEW_CACHE_LOCKS == {}
