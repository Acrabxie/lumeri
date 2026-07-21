from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import gc
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import weakref

import cv2
import numpy as np
import pytest

import gemia.video.layers as layers
from gemia.video.layers import Layer, LayerStack


class _CountingCapture:
    def __init__(self, capture, *, creator_thread: int) -> None:
        self.capture = capture
        self.creator_thread = creator_thread
        self.set_calls: list[tuple[int, float]] = []
        self.get_calls: list[int] = []
        self.read_calls = 0
        self.release_calls = 0
        self.operation_threads: list[tuple[str, int]] = []

    def _record(self, operation: str) -> None:
        self.operation_threads.append((operation, threading.get_ident()))

    def isOpened(self) -> bool:
        self._record("isOpened")
        return bool(self.capture.isOpened())

    def set(self, prop: int, value: float) -> bool:
        self._record("set")
        self.set_calls.append((int(prop), float(value)))
        return bool(self.capture.set(prop, value))

    def get(self, prop: int) -> float:
        self._record("get")
        self.get_calls.append(int(prop))
        return float(self.capture.get(prop))

    def read(self):
        self._record("read")
        self.read_calls += 1
        return self.capture.read()

    def release(self) -> None:
        self._record("release")
        self.release_calls += 1
        self.capture.release()


def _direct_bgr_frame(real_capture, path: str, frame_index: int) -> np.ndarray:
    capture = real_capture(path)
    try:
        assert capture.isOpened()
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = capture.read()
        assert ok and frame is not None
        return frame
    finally:
        capture.release()


def _write_color_video(path: Path, bgr: tuple[int, int, int]) -> None:
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        10.0,
        (32, 24),
    )
    assert writer.isOpened()
    try:
        frame = np.zeros((24, 32, 3), dtype=np.uint8)
        frame[:] = bgr
        for _ in range(4):
            writer.write(frame)
    finally:
        writer.release()


def test_true_video_reader_reuses_one_decoder_and_preserves_random_seek(
    sample_video_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layers.release_video_decoders(all_threads=True)
    real_capture = layers.cv2.VideoCapture
    captures: list[_CountingCapture] = []

    def counted_capture(path: str) -> _CountingCapture:
        proxy = _CountingCapture(
            real_capture(path),
            creator_thread=threading.get_ident(),
        )
        captures.append(proxy)
        return proxy

    monkeypatch.setattr(layers.cv2, "VideoCapture", counted_capture)
    try:
        with layers.video_decoder_scope():
            decoded = [
                layers._read_video_frame(sample_video_path, frame_index)
                for frame_index in (0, 1, 2, 9, 10)
            ]
            assert len(captures) == 1
            assert captures[0].set_calls == [(int(cv2.CAP_PROP_POS_FRAMES), 9.0)]

            expected_bgr = _direct_bgr_frame(real_capture, sample_video_path, 9)
            expected_rgb = cv2.cvtColor(expected_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
            assert np.allclose(decoded[3][..., :3], expected_rgb, atol=1.0 / 255.0)
            assert captures[0].isOpened()

        assert captures[0].release_calls == 1
        assert not captures[0].isOpened()
    finally:
        layers.release_video_decoders(all_threads=True)


def test_repeated_source_frames_reuse_pixels_without_backward_seek(
    sample_video_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """24->30fps and slow-motion repeats must not seek back through the GOP."""
    layers.release_video_decoders(all_threads=True)
    real_capture = layers.cv2.VideoCapture
    captures: list[_CountingCapture] = []

    def counted_capture(path: str) -> _CountingCapture:
        proxy = _CountingCapture(real_capture(path), creator_thread=threading.get_ident())
        captures.append(proxy)
        return proxy

    monkeypatch.setattr(layers.cv2, "VideoCapture", counted_capture)
    try:
        requested = (0, 0, 1, 1, 2)
        with layers.video_decoder_scope():
            decoded = [layers._read_video_frame(sample_video_path, index) for index in requested]

        assert len(captures) == 1
        assert captures[0].read_calls == 3
        assert captures[0].set_calls == []
        assert captures[0].release_calls == 1
        assert np.array_equal(decoded[0], decoded[1])
        assert np.array_equal(decoded[2], decoded[3])
        for index, frame in zip(requested, decoded):
            expected_bgr = _direct_bgr_frame(real_capture, sample_video_path, index)
            expected_rgb = cv2.cvtColor(expected_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
            assert np.allclose(frame[..., :3], expected_rgb, atol=1.0 / 255.0)
    finally:
        layers.release_video_decoders(all_threads=True)


def test_compile_shared_asset_probes_metadata_once_and_reuses_one_decoder(
    sample_video_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two layers sharing one path cause one metadata open plus one render open."""
    from lumenframe import apply_layer_patch, empty_doc
    from lumenframe.compile import compile_to_layer_stack

    layers.release_video_decoders(all_threads=True)
    real_capture = layers.cv2.VideoCapture
    captures: list[_CountingCapture] = []

    def counted_capture(path: str) -> _CountingCapture:
        proxy = _CountingCapture(real_capture(path), creator_thread=threading.get_ident())
        captures.append(proxy)
        return proxy

    monkeypatch.setattr(layers.cv2, "VideoCapture", counted_capture)
    doc = empty_doc(width=128, height=128, fps=30.0)
    doc["assets"].append({"id": "shared-video", "path": sample_video_path})
    doc = apply_layer_patch(
        doc,
        {
            "version": 1,
            "ops": [
                {
                    "op": "add_layer",
                    "id": f"shared-{index}",
                    "type": "video",
                    "asset_id": "shared-video",
                    "duration": 0.1,
                }
                for index in range(2)
            ],
        },
    )

    try:
        stack = compile_to_layer_stack(doc)
        assert len(captures) == 1
        metadata_capture = captures[0]
        assert metadata_capture.get_calls == [
            int(cv2.CAP_PROP_FRAME_WIDTH),
            int(cv2.CAP_PROP_FRAME_HEIGHT),
            int(cv2.CAP_PROP_FPS),
            int(cv2.CAP_PROP_FRAME_COUNT),
        ]
        assert metadata_capture.release_calls == 1

        assert len(stack.render_frames(end_frame=2)) == 2
        assert len(captures) == 2
        decoder_capture = captures[1]
        assert decoder_capture.get_calls == []
        assert decoder_capture.read_calls == 1
        assert decoder_capture.set_calls == []
        assert decoder_capture.release_calls == 1
    finally:
        layers.release_video_decoders(all_threads=True)


def test_render_frames_releases_its_thread_local_decoder(
    sample_video_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layers.release_video_decoders(all_threads=True)
    real_capture = layers.cv2.VideoCapture
    captures: list[_CountingCapture] = []

    def counted_capture(path: str) -> _CountingCapture:
        proxy = _CountingCapture(
            real_capture(path),
            creator_thread=threading.get_ident(),
        )
        captures.append(proxy)
        return proxy

    monkeypatch.setattr(layers.cv2, "VideoCapture", counted_capture)
    stack = LayerStack(width=128, height=128, fps=15.0, total_frames=3)
    stack.add_layer(
        Layer(
            id="video",
            name="video",
            end_frame=3,
            content_fn=lambda frame_index: layers._read_video_frame(
                sample_video_path,
                frame_index,
            ),
        )
    )
    try:
        frames = stack.render_frames()
        assert len(frames) == 3
        assert len(captures) == 1
        assert captures[0].set_calls == []
        assert captures[0].release_calls == 1
        assert not captures[0].isOpened()
    finally:
        layers.release_video_decoders(all_threads=True)


def test_render_failure_still_releases_open_decoder(
    sample_video_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layers.release_video_decoders(all_threads=True)
    real_capture = layers.cv2.VideoCapture
    captures: list[_CountingCapture] = []

    def counted_capture(path: str) -> _CountingCapture:
        proxy = _CountingCapture(real_capture(path), creator_thread=threading.get_ident())
        captures.append(proxy)
        return proxy

    monkeypatch.setattr(layers.cv2, "VideoCapture", counted_capture)
    stack = LayerStack(width=128, height=128, fps=15.0, total_frames=3)

    def failing_video(frame_index: int) -> np.ndarray:
        frame = layers._read_video_frame(sample_video_path, frame_index)
        if frame_index == 1:
            raise RuntimeError("synthetic frame failure")
        return frame

    stack.add_layer(
        Layer(id="failing-video", name="failing-video", end_frame=3, content_fn=failing_video)
    )
    try:
        with pytest.raises(RuntimeError, match="synthetic frame failure"):
            stack.render_frames()
        assert len(captures) == 1
        assert captures[0].release_calls == 1
        assert not captures[0].isOpened()
    finally:
        layers.release_video_decoders(all_threads=True)


def test_unscoped_single_frame_releases_decoder_immediately(
    sample_video_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layers.release_video_decoders(all_threads=True)
    real_capture = layers.cv2.VideoCapture
    captures: list[_CountingCapture] = []

    def counted_capture(path: str) -> _CountingCapture:
        proxy = _CountingCapture(
            real_capture(path),
            creator_thread=threading.get_ident(),
        )
        captures.append(proxy)
        return proxy

    monkeypatch.setattr(layers.cv2, "VideoCapture", counted_capture)

    worker = threading.Thread(
        target=lambda: layers._read_video_frame(sample_video_path, 0),
        daemon=False,
    )
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive()
    gc.collect()

    try:
        assert len(captures) == 1
        assert captures[0].release_calls == 1
        assert not captures[0].isOpened()
    finally:
        layers.release_video_decoders(all_threads=True)


def test_frame_scope_reuses_twelve_active_readers_and_releases_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal >8-layer composition must not churn every decoder each frame."""
    layers.release_video_decoders(all_threads=True)
    tracker = {"open": 0, "peak": 0}
    captures: list[object] = []

    class SyntheticCapture:
        def __init__(self, _path: str) -> None:
            self.closed = False
            self.read_calls = 0
            tracker["open"] += 1
            tracker["peak"] = max(tracker["peak"], tracker["open"])
            captures.append(self)

        def isOpened(self) -> bool:
            return not self.closed

        def set(self, _prop: int, _value: float) -> bool:
            return True

        def read(self):
            self.read_calls += 1
            return True, np.zeros((4, 4, 3), dtype=np.uint8)

        def release(self) -> None:
            if not self.closed:
                self.closed = True
                tracker["open"] -= 1

    monkeypatch.setattr(layers.cv2, "VideoCapture", SyntheticCapture)
    stack = LayerStack(width=4, height=4, fps=30.0, total_frames=3)
    for index in range(12):
        path = f"/tmp/synthetic-active-{index}.mp4"
        stack.add_layer(
            Layer(
                id=f"video-{index:02d}",
                name=f"video-{index:02d}",
                end_frame=3,
                content_fn=lambda frame_index, source=path: layers._read_video_frame(
                    source, frame_index
                ),
            )
        )

    try:
        rendered = stack.render_frames()
        assert len(rendered) == 3
        assert len(captures) == 12
        assert tracker["peak"] == 12
        assert tracker["open"] == 0
        assert all(capture.read_calls == 3 for capture in captures)
        assert all(capture.closed for capture in captures)
    finally:
        layers.release_video_decoders(all_threads=True)


def test_reader_cache_hard_caps_transient_open_decoder_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layers.release_video_decoders(all_threads=True)
    tracker = {"open": 0, "peak": 0, "created": 0}

    class SyntheticCapture:
        def __init__(self, _path: str) -> None:
            self.closed = False
            tracker["created"] += 1
            tracker["open"] += 1
            tracker["peak"] = max(tracker["peak"], tracker["open"])

        def isOpened(self) -> bool:
            return not self.closed

        def set(self, _prop: int, _value: float) -> bool:
            return True

        def read(self):
            return True, np.zeros((4, 4, 3), dtype=np.uint8)

        def release(self) -> None:
            if not self.closed:
                self.closed = True
                tracker["open"] -= 1

    monkeypatch.setattr(layers.cv2, "VideoCapture", SyntheticCapture)
    try:
        with layers.video_decoder_scope():
            for index in range(80):
                layers._read_video_frame(f"/tmp/synthetic-cap-{index}.mp4", 0)
        assert tracker["created"] == 80
        assert tracker["peak"] == layers._VIDEO_READER_CACHE_LIMIT == 64
        assert tracker["open"] == 0
    finally:
        layers.release_video_decoders(all_threads=True)


def test_frame_scope_bounds_sequential_reader_working_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readers from earlier clips are evicted instead of accumulating for the film."""
    layers.release_video_decoders(all_threads=True)
    tracker = {"open": 0, "peak": 0, "created": 0}

    class SyntheticCapture:
        def __init__(self, _path: str) -> None:
            self.closed = False
            tracker["created"] += 1
            tracker["open"] += 1
            tracker["peak"] = max(tracker["peak"], tracker["open"])

        def isOpened(self) -> bool:
            return not self.closed

        def set(self, _prop: int, _value: float) -> bool:
            return True

        def read(self):
            return True, np.zeros((4, 4, 3), dtype=np.uint8)

        def release(self) -> None:
            if not self.closed:
                self.closed = True
                tracker["open"] -= 1

    monkeypatch.setattr(layers.cv2, "VideoCapture", SyntheticCapture)
    frame_count = 80
    stack = LayerStack(width=4, height=4, fps=30.0, total_frames=frame_count)
    for index in range(frame_count):
        path = f"/tmp/synthetic-sequential-{index}.mp4"
        stack.add_layer(
            Layer(
                id=f"clip-{index:02d}",
                name=f"clip-{index:02d}",
                start_frame=index,
                end_frame=index + 1,
                content_fn=lambda _frame_index, source=path: layers._read_video_frame(source, 0),
            )
        )

    try:
        assert len(stack.render_frames()) == frame_count
        assert tracker["created"] == frame_count
        # One previous-frame reader overlaps the newly opened current reader;
        # it is released at the outer frame boundary.
        assert tracker["peak"] <= 2
        assert tracker["open"] == 0
    finally:
        layers.release_video_decoders(all_threads=True)


def test_unscoped_preview_reads_replaced_file_at_same_path(tmp_path: Path) -> None:
    layers.release_video_decoders(all_threads=True)
    source = tmp_path / "replaceable.mp4"
    replacement = tmp_path / "replacement.mp4"
    _write_color_video(source, (0, 0, 255))

    first = layers._read_video_frame(source, 0)
    _write_color_video(replacement, (255, 0, 0))
    replacement.replace(source)
    second = layers._read_video_frame(source, 0)

    assert first[0, 0, 0] > 0.8 and first[0, 0, 2] < 0.2
    assert second[0, 0, 2] > 0.8 and second[0, 0, 0] < 0.2


def test_decoder_cursors_are_isolated_between_concurrent_threads(
    sample_video_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layers.release_video_decoders(all_threads=True)
    real_capture = layers.cv2.VideoCapture
    captures: list[_CountingCapture] = []
    start_barrier = threading.Barrier(2)
    finish_barrier = threading.Barrier(2)

    def counted_capture(path: str) -> _CountingCapture:
        proxy = _CountingCapture(
            real_capture(path),
            creator_thread=threading.get_ident(),
        )
        captures.append(proxy)
        return proxy

    monkeypatch.setattr(layers.cv2, "VideoCapture", counted_capture)

    def read_sequence(indices: tuple[int, ...]) -> tuple[int, tuple[int, ...], list[np.ndarray]]:
        start_barrier.wait(timeout=10)
        with layers.video_decoder_scope():
            decoded = [
                layers._read_video_frame(sample_video_path, index)
                for index in indices
            ]
            finish_barrier.wait(timeout=10)
            return threading.get_ident(), indices, decoded

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    read_sequence,
                    ((0, 1, 6), (3, 4, 2)),
                )
            )

        worker_ids = {thread_id for thread_id, _indices, _decoded in results}
        assert len(worker_ids) == 2
        assert len(captures) == 2
        assert {capture.creator_thread for capture in captures} == worker_ids
        assert all(capture.release_calls == 1 for capture in captures)
        for thread_id, indices, decoded in results:
            for index, frame in zip(indices, decoded):
                expected_bgr = _direct_bgr_frame(real_capture, sample_video_path, index)
                expected_rgb = cv2.cvtColor(expected_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
                assert frame.shape == (128, 128, 4)
                assert np.allclose(frame[..., :3], expected_rgb, atol=1.0 / 255.0)
            owned_capture = next(
                capture for capture in captures if capture.creator_thread == thread_id
            )
            assert {
                operation_thread
                for _operation, operation_thread in owned_capture.operation_threads
            } == {thread_id}

        seek_targets = sorted(
            tuple(value for prop, value in capture.set_calls if prop == cv2.CAP_PROP_POS_FRAMES)
            for capture in captures
        )
        assert seek_targets == [(3.0, 2.0), (6.0,)]
    finally:
        layers.release_video_decoders(all_threads=True)


def test_streaming_export_passes_a_lazy_bounded_frame_generator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    width, height, frame_count = 320, 180, 120
    frame_bytes = width * height * 4 * np.dtype(np.float32).itemsize
    stack = LayerStack(width=width, height=height, fps=30.0, total_frames=frame_count)
    active_bytes = 0
    peak_bytes = 0
    rendered_indices: list[int] = []

    def on_release(size: int) -> None:
        nonlocal active_bytes
        active_bytes -= size

    def tracked_render(frame_index: int) -> np.ndarray:
        nonlocal active_bytes, peak_bytes
        frame = np.zeros((height, width, 4), dtype=np.float32)
        frame[..., 3] = 1.0
        active_bytes += int(frame.nbytes)
        peak_bytes = max(peak_bytes, active_bytes)
        weakref.finalize(frame, on_release, int(frame.nbytes))
        rendered_indices.append(frame_index)
        return frame

    def fake_stream(frames, output_path: Path, **_kwargs) -> None:
        for frame in frames:
            assert frame.shape == (height, width, 4)
        output_path.write_bytes(b"bounded-stream-proof")

    monkeypatch.setattr(stack, "render_frame", tracked_render)
    monkeypatch.setattr(
        stack,
        "render_frames",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not materialize frames")),
    )
    monkeypatch.setattr(layers.shutil, "which", lambda name: "/fake/ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(layers, "_stream_browser_mp4", fake_stream)

    output = tmp_path / "streamed.mp4"
    assert stack.render_to_video(output) == str(output.resolve())
    gc.collect()

    assert rendered_indices == list(range(frame_count))
    assert peak_bytes <= 2 * frame_bytes
    assert peak_bytes < frame_count * frame_bytes / 20
    assert active_bytes == 0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_real_streaming_export_peak_rss_does_not_scale_with_duration(tmp_path: Path) -> None:
    """A 10x longer real encode must not retain its rendered frames in parent RSS."""
    script = r'''
import gc
import json
from pathlib import Path
import resource
import sys

import numpy as np
from gemia.video.layers import Layer, LayerStack

root = Path(sys.argv[1])
width, height = 320, 180

def rss_bytes():
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024

def render(frame_count, name):
    stack = LayerStack(width=width, height=height, fps=30.0, total_frames=frame_count)
    def content(frame_index):
        frame = np.zeros((height, width, 4), dtype=np.float32)
        frame[..., 0] = frame_index / max(frame_count, 1)
        frame[..., 3] = 1.0
        return frame
    stack.add_layer(Layer(id=name, name=name, end_frame=frame_count, content_fn=content))
    stack.render_to_video(root / f"{name}.mp4")
    gc.collect()
    return rss_bytes()

small = render(30, "small")
large = render(300, "large")
print(json.dumps({"small": small, "large": large}))
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    metrics = json.loads(result.stdout.strip().splitlines()[-1])
    rss_growth = int(metrics["large"]) - int(metrics["small"])
    materialized_growth = (300 - 30) * 320 * 180 * 4 * np.dtype(np.float32).itemsize
    assert rss_growth < 64 * 1024 * 1024, metrics
    assert rss_growth < materialized_growth / 2, metrics


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe are required for real H.264 verification",
)
def test_real_streaming_export_is_playable_h264_bt709(tmp_path: Path) -> None:
    width, height, frame_count = 64, 48, 12
    stack = LayerStack(width=width, height=height, fps=12.0, total_frames=frame_count)

    def content(frame_index: int) -> np.ndarray:
        frame = np.zeros((height, width, 4), dtype=np.float32)
        frame[..., 0] = frame_index / frame_count
        frame[..., 1] = 0.35
        frame[..., 2] = 1.0 - frame_index / frame_count
        frame[..., 3] = 1.0
        return frame

    stack.add_layer(
        Layer(
            id="animated",
            name="animated",
            end_frame=frame_count,
            content_fn=content,
        )
    )
    output = tmp_path / "real-stream.mp4"
    mode_probe = tmp_path / "creation-mode-probe"
    probe_fd = os.open(mode_probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
    try:
        expected_creation_mode = os.fstat(probe_fd).st_mode & 0o777
    finally:
        os.close(probe_fd)
        mode_probe.unlink()
    assert stack.render_to_video(output) == str(output.resolve())
    assert output.stat().st_mode & 0o777 == expected_creation_mode

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,pix_fmt,width,height,nb_frames,color_space,color_primaries,color_transfer",
            "-of",
            "json",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream["codec_name"] == "h264"
    assert stream["pix_fmt"] == "yuv420p"
    assert int(stream["width"]) == width
    assert int(stream["height"]) == height
    assert int(stream["nb_frames"]) == frame_count
    assert stream["color_space"] == "bt709"
    assert stream["color_primaries"] == "bt709"
    assert stream["color_transfer"] == "bt709"

    capture = cv2.VideoCapture(str(output))
    decoded = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            assert frame is not None and frame.shape[:2] == (height, width)
            decoded += 1
    finally:
        capture.release()
    assert decoded == frame_count


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_streaming_export_preserves_existing_destination_mode(tmp_path: Path) -> None:
    output = tmp_path / "mode-preserved.mp4"
    output.write_bytes(b"previous")
    output.chmod(0o640)
    stack = LayerStack(width=16, height=16, fps=5.0, total_frames=2)
    rgba = np.ones((16, 16, 4), dtype=np.float32)
    stack.add_layer(
        Layer(
            id="mode",
            name="mode",
            end_frame=2,
            content_fn=lambda _frame_index: rgba.copy(),
        )
    )

    stack.render_to_video(output)
    assert output.stat().st_mode & 0o777 == 0o640


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_new_export_respects_restrictive_process_umask(tmp_path: Path) -> None:
    script = r'''
import os
from pathlib import Path
import sys

import numpy as np
from gemia.video.layers import Layer, LayerStack

os.umask(0o077)
output = Path(sys.argv[1])
rgba = np.ones((16, 16, 4), dtype=np.float32)
stack = LayerStack(width=16, height=16, fps=5.0, total_frames=2)
stack.add_layer(Layer(id="private", name="private", end_frame=2,
                      content_fn=lambda _index: rgba.copy()))
stack.render_to_video(output)
print(oct(output.stat().st_mode & 0o777))
'''
    output = tmp_path / "private.mp4"
    result = subprocess.run(
        [sys.executable, "-c", script, str(output)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "0o600"
    assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_blocking_pipe_fallback_remains_playable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the watchdog writer used where select cannot monitor pipe fds."""
    monkeypatch.setattr(layers, "_encoder_supports_nonblocking_pipe", lambda: False)
    stack = LayerStack(width=32, height=24, fps=10.0, total_frames=4)
    rgba = np.ones((24, 32, 4), dtype=np.float32)
    stack.add_layer(
        Layer(id="blocking", name="blocking", end_frame=4,
              content_fn=lambda _frame_index: rgba.copy())
    )
    output = tmp_path / "blocking-fallback.mp4"
    assert stack.render_to_video(output) == str(output.resolve())
    capture = cv2.VideoCapture(str(output))
    try:
        assert capture.isOpened()
        decoded = 0
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            assert frame is not None
            decoded += 1
    finally:
        capture.release()
    assert decoded == 4


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe are required for concurrent export verification",
)
def test_concurrent_exports_to_same_path_remain_atomic_and_playable(tmp_path: Path) -> None:
    output = tmp_path / "concurrent.mp4"

    def export_color(start_barrier: threading.Barrier, color: tuple[float, float, float]) -> str:
        stack = LayerStack(width=32, height=24, fps=10.0, total_frames=6)
        rgba = np.zeros((24, 32, 4), dtype=np.float32)
        rgba[..., :3] = color
        rgba[..., 3] = 1.0
        stack.add_layer(
            Layer(
                id=f"color-{color}",
                name="color",
                end_frame=6,
                content_fn=lambda _frame_index: rgba.copy(),
            )
        )
        start_barrier.wait(timeout=10)
        return stack.render_to_video(output)

    for _attempt in range(3):
        barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(export_color, barrier, (1.0, 0.0, 0.0)),
                executor.submit(export_color, barrier, (0.0, 0.0, 1.0)),
            ]
            results = [future.result(timeout=20) for future in futures]
        assert results == [str(output.resolve()), str(output.resolve())]

        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,nb_frames",
                "-of",
                "json",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        stream = json.loads(probe.stdout)["streams"][0]
        assert stream["codec_name"] == "h264"
        assert int(stream["nb_frames"]) == 6
        assert not list(tmp_path.glob(".concurrent.*.h264-tmp.mp4"))


def test_noisy_encoder_cannot_deadlock_stdin_and_stderr_pipes(tmp_path: Path) -> None:
    """Saturating stderr before reading stdin must fail promptly and atomically."""
    fake_encoder = tmp_path / "noisy-encoder.py"
    fake_encoder.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "os.write(2, b'x' * (1024 * 1024))\n"
        "while os.read(0, 65536):\n"
        "    pass\n"
        "sys.exit(7)\n",
        encoding="utf-8",
    )
    fake_encoder.chmod(0o755)
    output = tmp_path / "noisy.mp4"
    output.write_bytes(b"existing-output")
    child = r'''
from pathlib import Path
import sys

import numpy as np
from gemia.video import layers

output = Path(sys.argv[2])
frames = (np.ones((256, 256, 4), dtype=np.float32) for _ in range(8))
try:
    layers._stream_browser_mp4(
        frames,
        output,
        width=256,
        height=256,
        fps=24.0,
        background_color=(0.0, 0.0, 0.0),
        ffmpeg_path=sys.argv[1],
    )
except RuntimeError as exc:
    if "ffmpeg streaming MP4 encode failed" not in str(exc):
        raise
else:
    raise AssertionError("synthetic encoder failure was not surfaced")
'''
    result = subprocess.run(
        [sys.executable, "-c", child, str(fake_encoder), str(output)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert output.read_bytes() == b"existing-output"
    assert not list(tmp_path.glob(".noisy.*.h264-tmp.mp4"))


def test_stderr_temp_allocation_failure_cannot_leak_export_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "stderr-temp.mp4"

    def fail_stderr_temp():
        raise OSError("synthetic stderr temp allocation failure")

    monkeypatch.setattr(layers.tempfile, "TemporaryFile", fail_stderr_temp)
    with pytest.raises(OSError, match="synthetic stderr temp allocation failure"):
        layers._stream_browser_mp4(
            (),
            output,
            width=16,
            height=16,
            fps=10.0,
            background_color=(0.0, 0.0, 0.0),
            ffmpeg_path="/unused/ffmpeg",
        )

    assert not output.exists()
    assert not list(tmp_path.glob(".stderr-temp.*.h264-tmp.mp4"))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_streaming_encoder_failure_preserves_existing_output_and_cleans_temp(
    tmp_path: Path,
) -> None:
    stack = LayerStack(width=32, height=32, fps=10.0, total_frames=5)
    output = tmp_path / "existing.mp4"
    output.write_bytes(b"existing-output")

    def fail_during_render(frame_index: int) -> np.ndarray:
        if frame_index == 2:
            raise ValueError("synthetic render failure")
        frame = np.zeros((32, 32, 4), dtype=np.float32)
        frame[..., 3] = 1.0
        return frame

    stack.render_frame = fail_during_render  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="synthetic render failure"):
        stack.render_to_video(output)

    assert output.read_bytes() == b"existing-output"
    assert not list(tmp_path.glob(".existing.*.h264-tmp.mp4"))
