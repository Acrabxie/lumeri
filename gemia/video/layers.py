"""Layer-based compositing system for RGBA frame rendering."""
from __future__ import annotations

import atexit
from collections import OrderedDict
from contextlib import contextmanager, suppress
from copy import deepcopy
from dataclasses import dataclass, field
import os
from pathlib import Path
import queue
import select
import secrets
import signal
import shutil
import subprocess
import tempfile
import threading
import time
import weakref
from typing import Any, Callable
from collections.abc import Iterable, Mapping, Sequence

import cv2
import numpy as np
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFilter, ImageFont

from gemia.primitives_common import ensure_float32, to_uint8
from gemia.registry import get_info, get_registry, resolve
from gemia.video.keyframe import KeyframeTrack
from gemia.video.layer_validation import validate_layer_plan

RGBAFrame = np.ndarray
PrimitiveChain = list[tuple[str, dict[str, Any]]]


def _clamp01(value: np.ndarray | float) -> np.ndarray | float:
    return np.clip(value, 0.0, 1.0)


def _to_rgba(image: np.ndarray) -> RGBAFrame:
    """Convert grayscale/BGR/RGB/RGBA arrays to float32 RGBA in [0, 1]."""
    arr = ensure_float32(np.asarray(image))
    if arr.ndim == 2:
        rgb = np.repeat(arr[..., np.newaxis], 3, axis=2)
        alpha = np.ones((*arr.shape, 1), dtype=np.float32)
        return np.concatenate([rgb, alpha], axis=2).astype(np.float32)
    if arr.ndim != 3:
        raise ValueError(f"Expected 2D or 3D image, got shape {arr.shape}.")
    if arr.shape[2] == 4:
        return _clamp01(arr).astype(np.float32)
    if arr.shape[2] == 3:
        alpha = np.ones((arr.shape[0], arr.shape[1], 1), dtype=np.float32)
        return np.concatenate([arr, alpha], axis=2).astype(np.float32)
    if arr.shape[2] == 1:
        rgb = np.repeat(arr, 3, axis=2)
        alpha = np.ones((arr.shape[0], arr.shape[1], 1), dtype=np.float32)
        return np.concatenate([rgb, alpha], axis=2).astype(np.float32)
    raise ValueError(f"Unsupported channel count: {arr.shape[2]}.")


def _rgba_to_bgr(image: RGBAFrame) -> tuple[np.ndarray, np.ndarray]:
    rgba = _to_rgba(image)
    rgb = rgba[..., :3]
    alpha = rgba[..., 3:4]
    bgr = rgb[..., ::-1]
    return bgr.astype(np.float32), alpha.astype(np.float32)


def _bgr_to_rgba(image: np.ndarray, alpha: np.ndarray | None = None) -> RGBAFrame:
    arr = ensure_float32(np.asarray(image))
    if arr.ndim == 2:
        arr = np.repeat(arr[..., np.newaxis], 3, axis=2)
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise ValueError(f"Expected BGR image with 3 channels, got {arr.shape}.")
    rgb = arr[..., ::-1]
    if alpha is None:
        alpha = np.ones((arr.shape[0], arr.shape[1], 1), dtype=np.float32)
    else:
        alpha = ensure_float32(alpha)
        if alpha.ndim == 2:
            alpha = alpha[..., np.newaxis]
    return np.concatenate([rgb, alpha], axis=2).astype(np.float32)


def _read_image_rgba(path: str | Path) -> RGBAFrame:
    img = PILImage.open(path).convert("RGBA")
    arr = np.asarray(img, dtype=np.float32) / 255.0
    return _to_rgba(arr)


def _load_font(font_config: dict[str, Any] | None) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    cfg = dict(font_config or {})
    font_size = int(cfg.get("size", 48))
    font_path = cfg.get("path")
    if not font_path:
        try:
            from gemia.video.fonts import resolve_font_path

            font_path = resolve_font_path(cfg)
        except Exception:
            font_path = None
    if font_path:
        try:
            return ImageFont.truetype(font_path, font_size)
        except OSError:
            pass
    try:
        return ImageFont.truetype("DejaVuSans.ttf", font_size)
    except OSError:
        return ImageFont.load_default()


_VIDEO_READER_CACHE_LIMIT = 64
_VideoCacheKey = str
_video_reader_local = threading.local()
_video_reader_registry_lock = threading.RLock()
_video_reader_registry: weakref.WeakSet["_VideoFrameReader"] = weakref.WeakSet()
_export_destination_locks_guard = threading.Lock()
_export_destination_locks: dict[str, tuple[threading.Lock, int]] = {}
_FFMPEG_IO_STALL_TIMEOUT_SECONDS = 30.0
_FFMPEG_EXIT_TIMEOUT_SECONDS = 30.0
_FFMPEG_KILL_TIMEOUT_SECONDS = 5.0


@contextmanager
def _export_destination_lock(output_path: Path):
    """Serialize writers targeting one final path without retaining idle locks."""
    key = str(output_path)
    with _export_destination_locks_guard:
        state = _export_destination_locks.get(key)
        lock, users = state if state is not None else (threading.Lock(), 0)
        _export_destination_locks[key] = (lock, users + 1)
    try:
        with lock:
            yield
    finally:
        with _export_destination_locks_guard:
            current_lock, users = _export_destination_locks[key]
            if users <= 1:
                _export_destination_locks.pop(key, None)
            else:
                _export_destination_locks[key] = (current_lock, users - 1)


class _VideoFrameReader:
    """One OpenCV decoder cursor, owned by exactly one calling thread."""

    def __init__(self, video_path: str) -> None:
        self.video_path = video_path
        self._lock = threading.RLock()
        capture = cv2.VideoCapture(video_path)
        if not capture.isOpened():
            capture.release()
            raise FileNotFoundError(f"Cannot open video: {video_path}")
        self._capture = capture
        # A newly opened VideoCapture is positioned immediately before frame 0.
        self._next_frame_index: int | None = 0
        self._last_frame_index: int | None = None
        # Keep the repeated-frame cache in the decoder's native uint8/BGR
        # representation.  A 4K float32 RGBA copy would consume ~4x as much.
        self._last_frame_bgr: np.ndarray | None = None

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._capture is None

    def read(self, frame_index: int) -> RGBAFrame:
        requested = max(int(frame_index), 0)
        with self._lock:
            capture = self._capture
            if capture is None:
                raise RuntimeError(f"Video decoder is closed: {self.video_path}")
            if self._last_frame_index == requested and self._last_frame_bgr is not None:
                # Frame-rate conversion and slow motion commonly request one
                # source frame more than once.  Reuse the decoded pixels rather
                # than seeking backwards through a long GOP.
                frame = self._last_frame_bgr
            else:
                # Sequential renders advance the existing decoder cursor. Random
                # access remains supported by seeking only when the requested frame
                # is not the cursor's natural next frame.
                if self._next_frame_index != requested:
                    if not capture.set(cv2.CAP_PROP_POS_FRAMES, requested):
                        raise RuntimeError(
                            f"Video decoder cannot seek to frame {requested}: {self.video_path}"
                        )

                ok, frame = capture.read()
                if not ok or frame is None:
                    raise IndexError(f"Frame {frame_index} out of range for {self.video_path}")
                self._next_frame_index = requested + 1
                self._last_frame_bgr = frame
                self._last_frame_index = requested
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
            return _to_rgba(rgb)

    def close(self) -> None:
        with self._lock:
            capture = self._capture
            self._capture = None
            self._next_frame_index = None
            self._last_frame_index = None
            self._last_frame_bgr = None
        if capture is not None:
            capture.release()

    def __del__(self) -> None:
        with suppress(Exception):
            self.close()


def _video_cache_key(video_path: str | Path) -> _VideoCacheKey:
    # ``absolute`` normalises relative paths without a per-frame filesystem
    # stat/realpath round trip. Assets are immutable during one export, and the
    # export's finally block releases the cursor before a later render can reuse
    # a replaced file at the same path.
    return str(Path(video_path).expanduser().absolute())


def _thread_video_reader_cache() -> OrderedDict[_VideoCacheKey, _VideoFrameReader]:
    cache = getattr(_video_reader_local, "readers", None)
    if cache is None:
        cache = OrderedDict()
        _video_reader_local.readers = cache
    return cache


def _enter_video_decoder_scope() -> None:
    depth = int(getattr(_video_reader_local, "scope_depth", 0))
    _video_reader_local.scope_depth = depth + 1


def _exit_video_decoder_scope() -> None:
    depth = int(getattr(_video_reader_local, "scope_depth", 0))
    if depth <= 1:
        _video_reader_local.scope_depth = 0
        release_video_decoders()
    else:
        _video_reader_local.scope_depth = depth - 1


def _enter_video_decoder_frame() -> None:
    """Start one possibly nested compositing frame and its active-reader set."""
    depth = int(getattr(_video_reader_local, "frame_depth", 0))
    if depth <= 0:
        _video_reader_local.frame_touched = set()
    _video_reader_local.frame_depth = depth + 1


def _exit_video_decoder_frame() -> None:
    """Drop readers that were not active in the just-rendered outer frame."""
    depth = int(getattr(_video_reader_local, "frame_depth", 0))
    if depth > 1:
        _video_reader_local.frame_depth = depth - 1
        return
    _video_reader_local.frame_depth = 0
    touched = set(getattr(_video_reader_local, "frame_touched", set()))
    _video_reader_local.frame_touched = set()
    cache = getattr(_video_reader_local, "readers", None)
    if cache is None:
        return
    for key, reader in list(cache.items()):
        if key not in touched:
            _discard_video_reader(cache, key, reader)


@contextmanager
def _video_decoder_frame_scope():
    _enter_video_decoder_frame()
    try:
        yield
    finally:
        _exit_video_decoder_frame()


@contextmanager
def video_decoder_scope():
    """Reuse decoder cursors within one render, then release them deterministically."""
    _enter_video_decoder_scope()
    try:
        yield
    finally:
        _exit_video_decoder_scope()


def _unregister_video_reader(reader: _VideoFrameReader) -> None:
    with _video_reader_registry_lock:
        _video_reader_registry.discard(reader)


def _discard_video_reader(
    cache: OrderedDict[_VideoCacheKey, _VideoFrameReader],
    key: _VideoCacheKey,
    reader: _VideoFrameReader,
) -> None:
    if cache.get(key) is reader:
        cache.pop(key, None)
    reader.close()
    _unregister_video_reader(reader)


def _video_reader(video_path: str | Path) -> tuple[_VideoCacheKey, _VideoFrameReader]:
    cache = _thread_video_reader_cache()
    key = _video_cache_key(video_path)
    reader = cache.get(key)
    if reader is not None and not reader.closed:
        cache.move_to_end(key)
        return key, reader
    if reader is not None:
        _discard_video_reader(cache, key, reader)

    # Evict before opening the next decoder so even the transient peak stays
    # within the declared resource ceiling.
    while len(cache) >= _VIDEO_READER_CACHE_LIMIT:
        old_key, old_reader = cache.popitem(last=False)
        old_reader.close()
        _unregister_video_reader(old_reader)

    reader = _VideoFrameReader(key)
    cache[key] = reader
    with _video_reader_registry_lock:
        _video_reader_registry.add(reader)
    return key, reader


def release_video_decoders(*, all_threads: bool = False) -> None:
    """Release cached OpenCV decoders for this thread, or every thread at exit.

    The normal render path releases only its own thread-local cursors, so two
    concurrent renders cannot close or move each other's decoder state.
    """
    if all_threads:
        with _video_reader_registry_lock:
            readers = list(_video_reader_registry)
            _video_reader_registry.clear()
        for reader in readers:
            reader.close()
        cache = getattr(_video_reader_local, "readers", None)
        if cache is not None:
            cache.clear()
        return

    cache = getattr(_video_reader_local, "readers", None)
    if cache is None:
        return
    readers = list(cache.values())
    cache.clear()
    for reader in readers:
        reader.close()
        _unregister_video_reader(reader)


def _read_video_frame(video_path: str | Path, frame_index: int) -> RGBAFrame:
    cache = _thread_video_reader_cache()
    key, reader = _video_reader(video_path)
    touched = getattr(_video_reader_local, "frame_touched", None)
    if isinstance(touched, set):
        touched.add(key)
    try:
        frame = reader.read(frame_index)
    except Exception:
        # A failed read must not poison the next attempt with an unknown cursor.
        _discard_video_reader(cache, key, reader)
        raise
    if int(getattr(_video_reader_local, "scope_depth", 0)) <= 0:
        # One-off previews stay fresh when a file at the same path is replaced;
        # only an explicit render scope is allowed to retain a decoder cursor.
        _discard_video_reader(cache, key, reader)
    return frame


def _release_all_video_decoders_at_exit() -> None:
    release_video_decoders(all_threads=True)


atexit.register(_release_all_video_decoders_at_exit)


def _video_metadata(video_path: str | Path) -> dict[str, int | float]:
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            raise FileNotFoundError(f"Cannot open video: {video_path}")
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        return {"width": width, "height": height, "fps": fps, "frames": frames}
    finally:
        cap.release()


def _resolve_primitive(name: str) -> Callable[..., np.ndarray]:
    if "." in name:
        info = get_info(name)
        if info.domain != "picture":
            raise ValueError(f"Layer primitive must be a picture primitive: {name}")
        return resolve(name)

    picture_matches = [
        info for info in get_registry().values()
        if info.name == name and info.domain == "picture"
    ]
    if len(picture_matches) == 1:
        return picture_matches[0].func
    if not picture_matches:
        raise KeyError(f"Unknown picture primitive: {name}")
    fqns = ", ".join(sorted(info.fqn for info in picture_matches))
    raise ValueError(f"Ambiguous primitive name '{name}': {fqns}")


def _apply_primitive_chain(frame: RGBAFrame, primitives_chain: PrimitiveChain | None) -> RGBAFrame:
    result = _to_rgba(frame)
    for primitive_name, kwargs in primitives_chain or []:
        func = _resolve_primitive(primitive_name)
        bgr, alpha = _rgba_to_bgr(result)
        processed = func(bgr, **dict(kwargs))
        result = _bgr_to_rgba(processed, alpha=alpha)
    return _to_rgba(result)


def _tint_rgba(frame: RGBAFrame, color: Sequence[float]) -> RGBAFrame:
    rgba = _to_rgba(frame).copy()
    tint = np.array(list(color)[:4], dtype=np.float32)
    if tint.shape[0] < 4:
        tint = np.pad(tint, (0, 4 - tint.shape[0]), constant_values=1.0)
    tint = np.clip(tint, 0.0, 1.0)
    shade = np.max(rgba[..., :3], axis=2, keepdims=True)
    rgba[..., :3] = shade * tint[:3].reshape(1, 1, 3)
    rgba[..., 3] = rgba[..., 3] * float(tint[3])
    return rgba


def _track_from_spec(spec: dict[str, Any]) -> KeyframeTrack:
    mode = str(spec.get("mode", "clamp")) if "mode" in spec else "clamp"
    relative_to = float(spec.get("relative_to", 0.0)) if "relative_to" in spec else 0.0
    raw_points = spec.get("keyframes", spec.get("points", spec))
    if isinstance(raw_points, list):
        items = [
            (float(item.get("time", item.get("frame", 0.0))), item)
            for item in raw_points
        ]
    else:
        ignored = {"mode", "relative_to", "keyframes", "points"}
        items = [
            (float(frame_key), value_spec)
            for frame_key, value_spec in raw_points.items()
            if frame_key not in ignored
        ]
    track = KeyframeTrack(mode=mode, relative_to=relative_to)
    for frame_number, value_spec in sorted(items, key=lambda item: item[0]):
        if isinstance(value_spec, dict):
            value = float(value_spec.get("value", 0.0))
            easing = str(value_spec.get("easing", "linear"))
        else:
            value = float(value_spec)
            easing = "linear"
        track.add_keyframe(frame_number, value, easing=easing)
    return track


def _vector_tracks_from_spec(spec: dict[str, Any]) -> dict[str, KeyframeTrack]:
    mode = str(spec.get("mode", "clamp")) if "mode" in spec else "clamp"
    relative_to = float(spec.get("relative_to", 0.0)) if "relative_to" in spec else 0.0
    raw_points = spec.get("keyframes", spec.get("points", spec))
    if isinstance(raw_points, list):
        items = [
            (float(item.get("time", item.get("frame", 0.0))), item)
            for item in raw_points
            if isinstance(item, Mapping)
        ]
    else:
        ignored = {"mode", "relative_to", "keyframes", "points"}
        items = [
            (float(frame_key), value_spec)
            for frame_key, value_spec in raw_points.items()
            if frame_key not in ignored
        ]

    track_x = KeyframeTrack(mode=mode, relative_to=relative_to)
    track_y = KeyframeTrack(mode=mode, relative_to=relative_to)
    for frame_number, value_spec in sorted(items, key=lambda item: item[0]):
        easing = "linear"
        value: Any = value_spec
        if isinstance(value_spec, Mapping):
            value = value_spec.get("value", value_spec)
            easing = str(value_spec.get("easing", "linear"))
        if isinstance(value, Mapping):
            x_value = value.get("x", value.get("left"))
            y_value = value.get("y", value.get("top"))
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) >= 2:
            x_value, y_value = value[0], value[1]
        else:
            raise ValueError("Vector keyframe values must be [x, y] or {x, y}.")
        track_x.add_keyframe(frame_number, float(x_value), easing=easing)
        track_y.add_keyframe(frame_number, float(y_value), easing=easing)
    return {"position_x": track_x, "position_y": track_y}


def _fit_to_canvas(content: RGBAFrame, width: int, height: int, position: tuple[int, int]) -> RGBAFrame:
    frame = _to_rgba(content)
    if frame.shape[0] == height and frame.shape[1] == width and position == (0, 0):
        return frame

    canvas = np.zeros((height, width, 4), dtype=np.float32)
    x, y = position
    src_h, src_w = frame.shape[:2]

    dst_x0 = max(x, 0)
    dst_y0 = max(y, 0)
    dst_x1 = min(x + src_w, width)
    dst_y1 = min(y + src_h, height)
    if dst_x1 <= dst_x0 or dst_y1 <= dst_y0:
        return canvas

    src_x0 = max(-x, 0)
    src_y0 = max(-y, 0)
    src_x1 = src_x0 + (dst_x1 - dst_x0)
    src_y1 = src_y0 + (dst_y1 - dst_y0)
    canvas[dst_y0:dst_y1, dst_x0:dst_x1] = frame[src_y0:src_y1, src_x0:src_x1]
    return canvas


def _transform_frame(frame: RGBAFrame, *, scale: float = 1.0, rotation_deg: float = 0.0) -> RGBAFrame:
    rgba = _to_rgba(frame)
    src_h, src_w = rgba.shape[:2]

    if not np.isclose(scale, 1.0):
        dst_w = max(1, int(round(src_w * scale)))
        dst_h = max(1, int(round(src_h * scale)))
        rgba = cv2.resize(rgba, (dst_w, dst_h), interpolation=cv2.INTER_LINEAR)

    if np.isclose(rotation_deg, 0.0):
        return _to_rgba(rgba)

    height, width = rgba.shape[:2]
    center = (width / 2.0, height / 2.0)
    matrix = cv2.getRotationMatrix2D(center, rotation_deg, 1.0)
    cos = abs(matrix[0, 0])
    sin = abs(matrix[0, 1])
    bound_w = max(1, int(round((height * sin) + (width * cos))))
    bound_h = max(1, int(round((height * cos) + (width * sin))))
    matrix[0, 2] += (bound_w / 2.0) - center[0]
    matrix[1, 2] += (bound_h / 2.0) - center[1]
    rotated = cv2.warpAffine(
        rgba,
        matrix,
        (bound_w, bound_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0.0, 0.0, 0.0, 0.0),
    )
    return _to_rgba(rotated)


def _apply_alpha_mask(frame: RGBAFrame, mask: np.ndarray | None) -> RGBAFrame:
    if mask is None:
        return _to_rgba(frame)
    rgba = _to_rgba(frame).copy()
    alpha_mask = ensure_float32(mask)
    if alpha_mask.ndim == 3:
        alpha_mask = alpha_mask[..., 0]
    if alpha_mask.shape != rgba.shape[:2]:
        alpha_mask = cv2.resize(alpha_mask, (rgba.shape[1], rgba.shape[0]), interpolation=cv2.INTER_LINEAR)
    rgba[..., 3] *= np.clip(alpha_mask, 0.0, 1.0)
    return rgba


def _apply_gaussian_blur_rgba(frame: RGBAFrame, radius: float | None) -> RGBAFrame:
    rgba = _to_rgba(frame)
    if radius is None or radius <= 0:
        return rgba
    blurred = cv2.GaussianBlur(
        rgba,
        (0, 0),
        sigmaX=float(radius),
        sigmaY=float(radius),
        borderType=cv2.BORDER_DEFAULT,
    )
    return _to_rgba(np.clip(blurred, 0.0, 1.0).astype(np.float32))


def _flatten_rgba_for_video(
    frame: RGBAFrame,
    background_color: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> np.ndarray:
    rgba = _to_rgba(frame)
    alpha = np.clip(rgba[..., 3:4], 0.0, 1.0)
    bg = np.array(background_color, dtype=np.float32).reshape(1, 1, 3)
    rgb = np.clip(rgba[..., :3] * alpha + bg * (1.0 - alpha), 0.0, 1.0)
    return rgb[..., ::-1].astype(np.float32)


#: Blend modes whose separable RGB mix function ``f(cb, cs)`` the compositor
#: implements. ``normal`` / ``multiply`` / ``screen`` / ``overlay`` are the
#: original four (kept byte-identical); the rest are the W3C compositing set plus
#: the photographic ``add`` / ``subtract`` family. Unknown modes degrade to
#: ``normal`` (safe, no crash) so a stray spec never breaks a render.
BLEND_MODES: tuple[str, ...] = (
    "normal",
    "multiply",
    "screen",
    "overlay",
    "add",
    "linear_dodge",
    "lighten",
    "darken",
    "difference",
    "exclusion",
    "subtract",
    "hard_light",
    "soft_light",
    "color_dodge",
    "color_burn",
)


def _blend_rgb(cb: np.ndarray, cs: np.ndarray, blend_mode: str) -> np.ndarray:
    """Separable per-channel blend function ``f(cb, cs)`` for the W3C model.

    ``cb`` is the backdrop colour, ``cs`` the source colour, both float32 in
    ``[0, 1]``. The original four modes (normal / multiply / screen / overlay)
    return exactly the same expression as before, so the premultiplied-alpha
    composite around this stays golden-identical. Unknown modes fall back to
    ``normal`` (return ``cs``) — never raise — so the renderer cannot crash on a
    stray blend name.
    """
    if blend_mode == "normal":
        return cs
    if blend_mode == "multiply":
        return cb * cs
    if blend_mode == "screen":
        return 1.0 - (1.0 - cb) * (1.0 - cs)
    if blend_mode == "overlay":
        return np.where(cb <= 0.5, 2.0 * cb * cs, 1.0 - 2.0 * (1.0 - cb) * (1.0 - cs))
    if blend_mode in ("add", "linear_dodge"):
        return _clamp01(cb + cs)
    if blend_mode == "lighten":
        return np.maximum(cb, cs)
    if blend_mode == "darken":
        return np.minimum(cb, cs)
    if blend_mode == "difference":
        return np.abs(cb - cs)
    if blend_mode == "exclusion":
        return cb + cs - 2.0 * cb * cs
    if blend_mode == "subtract":
        return _clamp01(cb - cs)
    if blend_mode == "hard_light":
        # Overlay with backdrop/source swapped (drive by the source).
        return np.where(cs <= 0.5, 2.0 * cb * cs, 1.0 - 2.0 * (1.0 - cb) * (1.0 - cs))
    if blend_mode == "soft_light":
        # W3C soft-light: a smooth dodge/burn that pivots on cs == 0.5.
        d = np.where(cb <= 0.25, ((16.0 * cb - 12.0) * cb + 4.0) * cb, np.sqrt(cb))
        return np.where(
            cs <= 0.5,
            cb - (1.0 - 2.0 * cs) * cb * (1.0 - cb),
            cb + (2.0 * cs - 1.0) * (d - cb),
        )
    if blend_mode == "color_dodge":
        # cb / (1 - cs): cb==0 stays 0, cs==1 saturates to 1 (W3C edge cases).
        denom = 1.0 - cs
        return np.where(
            cb <= 0.0,
            np.zeros_like(cb),
            np.where(denom <= 0.0, np.ones_like(cb), _clamp01(cb / np.where(denom <= 0.0, 1.0, denom))),
        )
    if blend_mode == "color_burn":
        # 1 - (1 - cb) / cs: cb==1 stays 1, cs==0 burns to 0 (W3C edge cases).
        return np.where(
            cb >= 1.0,
            np.ones_like(cb),
            np.where(cs <= 0.0, np.zeros_like(cb), _clamp01(1.0 - (1.0 - cb) / np.where(cs <= 0.0, 1.0, cs))),
        )
    # Unknown mode: degrade to normal rather than raising (renderer-safe).
    return cs


def _blend_colors(backdrop: RGBAFrame, source: RGBAFrame, blend_mode: str) -> RGBAFrame:
    cb = backdrop[..., :3]
    cs = source[..., :3]
    ab = backdrop[..., 3:4]
    a_s = source[..., 3:4]

    blend_rgb = _blend_rgb(cb, cs, blend_mode)

    out_alpha = a_s + ab * (1.0 - a_s)
    out_rgb_premul = (
        a_s * (1.0 - ab) * cs
        + a_s * ab * blend_rgb
        + (1.0 - a_s) * ab * cb
    )
    safe_alpha = np.where(out_alpha > 1e-6, out_alpha, 1.0)
    out_rgb = np.where(out_alpha > 1e-6, out_rgb_premul / safe_alpha, 0.0)
    return np.concatenate([_clamp01(out_rgb), _clamp01(out_alpha)], axis=2).astype(np.float32)


@dataclass
class Layer:
    """Single renderable layer within a stack."""

    id: str
    name: str
    start_frame: int = 0
    end_frame: int | None = None
    z_index: int = 0
    blend_mode: str = "normal"
    opacity: float = 1.0
    scale: float = 1.0
    rotation_deg: float = 0.0
    content_fn: Callable[[int], RGBAFrame] = lambda _frame_index: np.zeros((1, 1, 4), dtype=np.float32)
    mask_fn: Callable[[int], np.ndarray] | None = None
    time_map_fn: Callable[[int], int] | None = None
    keyframes: dict[str, KeyframeTrack] = field(default_factory=dict)
    position: tuple[int, int] = (0, 0)
    expressions: dict[str, dict] = field(default_factory=dict)  # property_name -> {"expr": str, ...}

    def is_active(self, frame_index: int) -> bool:
        if frame_index < self.start_frame:
            return False
        if self.end_frame is None:
            return True
        return frame_index < self.end_frame

    def property_value(self, name: str, frame_index: int, fps: float) -> float:
        # Precedence: expressions > keyframes > static value
        if name in self.expressions:
            expr_data = self.expressions[name]
            expr_str = expr_data.get("expr", "")
            if expr_str:
                try:
                    from gemia.expressions import SafeEvaluator
                    time_sec = float(frame_index) / fps if fps > 0 else 0.0
                    # Get layer duration (end_frame - start_frame) / fps
                    duration_frames = (self.end_frame or self.start_frame) - self.start_frame
                    duration_sec = float(duration_frames) / fps if fps > 0 else 1.0
                    evaluator = SafeEvaluator(time=time_sec, duration=duration_sec)
                    result = evaluator.eval(expr_str)
                    return float(result)
                except Exception:
                    # If expression fails, fall through to keyframes
                    pass
        
        base = getattr(self, name)
        track = self.keyframes.get(name)
        if track is None:
            return float(base)
        del fps
        return float(track.evaluate(float(frame_index)))

    def position_value(self, frame_index: int) -> tuple[int, int]:
        x, y = self.position
        x_track = self.keyframes.get("position_x") or self.keyframes.get("x")
        y_track = self.keyframes.get("position_y") or self.keyframes.get("y")
        if x_track is not None:
            x = x_track.evaluate(float(frame_index))
        if y_track is not None:
            y = y_track.evaluate(float(frame_index))
        return (int(round(float(x))), int(round(float(y))))

    def frame_content(self, frame_index: int) -> RGBAFrame:
        local_frame = frame_index - self.start_frame
        local_frame = self.time_map_fn(local_frame) if self.time_map_fn is not None else local_frame
        frame = _to_rgba(self.content_fn(local_frame))
        scale = max(0.001, float(self.keyframes.get("scale").evaluate(float(frame_index)))) if "scale" in self.keyframes else float(self.scale)
        rotation_deg = float(self.keyframes.get("rotation_deg").evaluate(float(frame_index))) if "rotation_deg" in self.keyframes else float(self.rotation_deg)
        frame = _transform_frame(frame, scale=scale, rotation_deg=rotation_deg)
        mask = self.mask_fn(local_frame) if self.mask_fn is not None else None
        return _apply_alpha_mask(frame, mask)


@dataclass
class LayerStack:
    """Layer stack for compositing an RGBA frame sequence."""

    width: int
    height: int
    fps: float
    total_frames: int
    layers: list[Layer] = field(default_factory=list)

    def add_layer(self, layer: Layer) -> None:
        self.layers.append(layer)
        self.layers.sort(key=lambda item: (item.z_index, item.id))

    def remove_layer(self, layer_id: str) -> None:
        self.layers = [layer for layer in self.layers if layer.id != layer_id]

    def render_frame(self, frame_index: int) -> RGBAFrame:
        if frame_index < 0 or frame_index >= self.total_frames:
            raise IndexError(f"Frame index {frame_index} outside [0, {self.total_frames}).")

        with _video_decoder_frame_scope():
            canvas = np.zeros((self.height, self.width, 4), dtype=np.float32)
            for layer in sorted(self.layers, key=lambda item: (item.z_index, item.id)):
                if not layer.is_active(frame_index):
                    continue
                content = layer.frame_content(frame_index)
                placed = _fit_to_canvas(content, self.width, self.height, layer.position_value(frame_index))
                opacity = float(np.clip(layer.property_value("opacity", frame_index, self.fps), 0.0, 1.0))
                if opacity <= 0.0:
                    continue
                placed = placed.copy()
                placed[..., 3:4] *= opacity
                canvas = _blend_colors(canvas, placed, layer.blend_mode)
            return canvas.astype(np.float32)

    def render_frames(
        self,
        *,
        start_frame: int = 0,
        end_frame: int | None = None,
        step: int = 1,
    ) -> list[RGBAFrame]:
        if step <= 0:
            raise ValueError("step must be >= 1")
        start = max(0, int(start_frame))
        stop = self.total_frames if end_frame is None else min(int(end_frame), self.total_frames)
        if stop < start:
            raise ValueError("end_frame must be >= start_frame")
        _enter_video_decoder_scope()
        try:
            return [self.render_frame(frame_index) for frame_index in range(start, stop, step)]
        finally:
            _exit_video_decoder_scope()

    def render_to_video(
        self,
        output_path: str | Path,
        *,
        codec: str = "mp4v",
        background_color: tuple[float, float, float] = (0.0, 0.0, 0.0),
        start_frame: int = 0,
        end_frame: int | None = None,
        step: int = 1,
    ) -> str:
        if step <= 0:
            raise ValueError("step must be >= 1")
        start = max(0, int(start_frame))
        stop = self.total_frames if end_frame is None else min(int(end_frame), self.total_frames)
        if stop < start:
            raise ValueError("end_frame must be >= start_frame")
        frame_indices = range(start, stop, step)
        if len(frame_indices) == 0:
            raise ValueError("No frames selected for render.")

        final_path = Path(output_path).expanduser().resolve()
        final_path.parent.mkdir(parents=True, exist_ok=True)
        fps = self.fps / max(int(step), 1)
        ffmpeg_path = shutil.which("ffmpeg")
        _enter_video_decoder_scope()
        try:
            if final_path.suffix.lower() == ".mp4" and ffmpeg_path is not None:
                _stream_browser_mp4(
                    (self.render_frame(frame_index) for frame_index in frame_indices),
                    final_path,
                    width=self.width,
                    height=self.height,
                    fps=fps,
                    background_color=background_color,
                    ffmpeg_path=ffmpeg_path,
                )
                return str(final_path)

            write_path = final_path.with_name(
                f"{final_path.stem}.opencv-tmp{final_path.suffix}"
            )
            fourcc = cv2.VideoWriter_fourcc(*codec)
            writer = cv2.VideoWriter(str(write_path), fourcc, fps, (self.width, self.height))
            if not writer.isOpened():
                writer.release()
                raise RuntimeError(f"OpenCV video writer could not open: {write_path}")
            try:
                try:
                    for frame_index in frame_indices:
                        frame = self.render_frame(frame_index)
                        writer.write(
                            to_uint8(
                                _flatten_rgba_for_video(
                                    frame,
                                    background_color=background_color,
                                )
                            )
                        )
                finally:
                    writer.release()
            except BaseException:
                with suppress(OSError):
                    write_path.unlink()
                raise
            write_path.replace(final_path)
            return str(final_path)
        finally:
            _exit_video_decoder_scope()


def make_video_layer(video_path: str, primitives_chain: PrimitiveChain | None = None) -> Layer:
    meta = _video_metadata(video_path)

    def content_fn(frame_index: int) -> RGBAFrame:
        frame = _read_video_frame(video_path, frame_index)
        return _apply_primitive_chain(frame, primitives_chain)

    return Layer(
        id=Path(video_path).stem,
        name=Path(video_path).name,
        start_frame=0,
        end_frame=int(meta["frames"]) if meta["frames"] else None,
        z_index=0,
        blend_mode="normal",
        opacity=1.0,
        content_fn=content_fn,
    )


def make_image_layer(
    image_path: str,
    duration: int,
    size: tuple[int, int] | None = None,
    *,
    blur_radius: float | None = None,
) -> Layer:
    frame = _read_image_rgba(image_path)
    if size is not None:
        target_w, target_h = max(int(size[0]), 1), max(int(size[1]), 1)
        if frame.shape[1] != target_w or frame.shape[0] != target_h:
            frame = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
    frame = _apply_gaussian_blur_rgba(frame, blur_radius)

    def content_fn(_frame_index: int) -> RGBAFrame:
        return frame.copy()

    return Layer(
        id=Path(image_path).stem,
        name=Path(image_path).name,
        start_frame=0,
        end_frame=duration,
        z_index=0,
        blend_mode="normal",
        opacity=1.0,
        content_fn=content_fn,
    )


def make_mask_layer(mask_path: str, duration: int) -> Callable[[int], np.ndarray]:
    mask_rgba = _read_image_rgba(mask_path)
    if mask_rgba.shape[2] == 4:
        alpha = mask_rgba[..., 3]
    else:
        alpha = np.mean(mask_rgba[..., :3], axis=2)
    alpha = np.clip(alpha.astype(np.float32), 0.0, 1.0)

    def content_fn(_frame_index: int) -> np.ndarray:
        return alpha.copy()

    del duration
    return content_fn


def make_text_layer(text: str, position: tuple[int, int], font_config: dict[str, Any] | None = None) -> Layer:
    cfg = dict(font_config or {})
    base_padding = int(cfg.get("padding", 4))
    glow_radius = max(0.0, float(cfg.get("glow_radius", cfg.get("aura_radius", 0.0)) or 0.0))
    glow_extra = int(round(glow_radius * 2.0)) if glow_radius > 0 else 0
    padding = base_padding + glow_extra
    fill = tuple(cfg.get("color", (1.0, 1.0, 1.0, 1.0)))
    fill_u8 = tuple(int(np.clip(channel, 0.0, 1.0) * 255) for channel in fill)
    glow_color = tuple(cfg.get("glow_color", fill))
    if len(glow_color) < 4:
        glow_color = tuple(list(glow_color) + [0.55])
    glow_u8 = tuple(int(np.clip(channel, 0.0, 1.0) * 255) for channel in glow_color[:4])
    font = _load_font(cfg)

    dummy = PILImage.new("RGBA", (1, 1), (0, 0, 0, 0))
    draw = ImageDraw.Draw(dummy)
    bbox = draw.textbbox((0, 0), text, font=font)
    width = max(1, bbox[2] - bbox[0] + padding * 2)
    height = max(1, bbox[3] - bbox[1] + padding * 2)

    def content_fn(_frame_index: int) -> RGBAFrame:
        img = PILImage.new("RGBA", (width, height), (0, 0, 0, 0))
        text_xy = (padding - bbox[0], padding - bbox[1])
        if glow_radius > 0:
            glow = PILImage.new("RGBA", (width, height), (0, 0, 0, 0))
            glow_draw = ImageDraw.Draw(glow)
            glow_draw.text(text_xy, text, fill=glow_u8, font=font)
            glow = glow.filter(ImageFilter.GaussianBlur(glow_radius))
            img.alpha_composite(glow)
        draw = ImageDraw.Draw(img)
        draw.text(text_xy, text, fill=fill_u8, font=font)
        return np.asarray(img, dtype=np.float32) / 255.0

    return Layer(
        id=f"text_{abs(hash((text, position))) % 100000}",
        name=text[:32] or "text",
        start_frame=0,
        end_frame=None,
        z_index=0,
        blend_mode="normal",
        opacity=1.0,
        content_fn=content_fn,
        position=tuple(position),
    )


def make_solid_layer(
    color: tuple[float, float, float, float],
    duration: int,
    size: tuple[int, int] = (1, 1),
) -> Layer:
    width, height = max(int(size[0]), 1), max(int(size[1]), 1)
    rgba = np.tile(np.array(color, dtype=np.float32).reshape(1, 1, 4), (height, width, 1))
    rgba = _to_rgba(rgba)

    def content_fn(_frame_index: int) -> RGBAFrame:
        return rgba.copy()

    return Layer(
        id=f"solid_{abs(hash(tuple(float(v) for v in color))) % 100000}",
        name="solid",
        start_frame=0,
        end_frame=duration,
        z_index=0,
        blend_mode="normal",
        opacity=1.0,
        content_fn=content_fn,
    )


def make_html_layer(
    *,
    source: str | None = None,
    html: str | None = None,
    duration: int,
    size: tuple[int, int],
) -> Layer:
    from gemia.video.html_graphics import render_html_frame

    width, height = max(int(size[0]), 1), max(int(size[1]), 1)
    frame = render_html_frame(source, html, width=width, height=height)

    def content_fn(_frame_index: int) -> RGBAFrame:
        return frame.copy()

    return Layer(
        id=f"html_{abs(hash((source, html, width, height))) % 100000}",
        name="html_graphic",
        start_frame=0,
        end_frame=duration,
        z_index=0,
        blend_mode="normal",
        opacity=1.0,
        content_fn=content_fn,
    )


def make_lottie_layer(source: str, *, duration: int, size: tuple[int, int]) -> Layer:
    from gemia.video.html_graphics import render_lottie_frame

    width, height = max(int(size[0]), 1), max(int(size[1]), 1)

    def content_fn(frame_index: int) -> RGBAFrame:
        return render_lottie_frame(source, width=width, height=height, frame_index=frame_index)

    return Layer(
        id=Path(source).stem,
        name=Path(source).name,
        start_frame=0,
        end_frame=duration,
        z_index=0,
        blend_mode="normal",
        opacity=1.0,
        content_fn=content_fn,
    )


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _resolve_layer_timing(
    layer_spec: dict[str, Any],
    *,
    natural_frames: int | None = None,
    plan_total_frames: int | None = None,
) -> tuple[int, int | None, int | None]:
    start_frame = int(layer_spec.get("start_frame", 0) or 0)
    end_frame = _optional_int(layer_spec.get("end_frame"))
    duration = _optional_int(layer_spec.get("duration"))

    if end_frame is None:
        if duration is not None:
            end_frame = start_frame + duration
        elif natural_frames is not None and natural_frames > 0:
            end_frame = start_frame + natural_frames
        elif plan_total_frames is not None and plan_total_frames > 0:
            end_frame = int(plan_total_frames)

    if end_frame is not None:
        duration = max(end_frame - start_frame, 0)

    return start_frame, end_frame, duration


def _layer_blur_radius(layer_spec: Mapping[str, Any]) -> float | None:
    metadata = layer_spec.get("metadata")
    candidates = [
        layer_spec.get("blur_radius"),
        layer_spec.get("gaussian_blur_radius"),
    ]
    if isinstance(metadata, Mapping):
        candidates.extend(
            [
                metadata.get("blur_radius"),
                metadata.get("contact_shadow_blur_radius"),
                metadata.get("gaussian_blur_radius"),
            ]
        )
    for candidate in candidates:
        if candidate is None or candidate == "":
            continue
        try:
            radius = float(candidate)
        except (TypeError, ValueError):
            continue
        if radius > 0:
            return min(radius, 64.0)
    return None


def materialize_layer_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Return a plan with inferred metrics and explicit per-layer timing."""
    materialized = deepcopy(plan)
    width = int(materialized.get("width", 0) or 0)
    height = int(materialized.get("height", 0) or 0)
    fps = float(materialized.get("fps", 0) or 0)
    explicit_total_frames = int(materialized.get("total_frames", 0) or 0)
    max_end = explicit_total_frames

    video_meta_cache: dict[str, dict[str, int | float]] = {}
    image_meta_cache: dict[str, tuple[int, int]] = {}
    layers: list[dict[str, Any]] = []

    for layer_spec in materialized.get("layers", []):
        normalized_layer = deepcopy(layer_spec)
        natural_frames: int | None = None
        layer_type = normalized_layer.get("type")

        if layer_type == "video" and normalized_layer.get("source"):
            source_path = str(normalized_layer["source"])
            meta = video_meta_cache.setdefault(source_path, _video_metadata(source_path))
            width = width or int(meta["width"])
            height = height or int(meta["height"])
            fps = fps or float(meta["fps"])
            natural_frames = int(meta["frames"] or 0) or None
        elif layer_type == "image" and normalized_layer.get("source"):
            source_path = str(normalized_layer["source"])
            if source_path not in image_meta_cache:
                rgba = _read_image_rgba(source_path)
                image_meta_cache[source_path] = (int(rgba.shape[1]), int(rgba.shape[0]))
            source_width, source_height = image_meta_cache[source_path]
            width = width or source_width
            height = height or source_height
        elif layer_type == "lottie" and normalized_layer.get("source"):
            from gemia.video.html_graphics import lottie_metadata

            source_path = str(normalized_layer["source"])
            meta = lottie_metadata(source_path)
            width = width or int(meta["width"])
            height = height or int(meta["height"])
            fps = fps or float(meta["fps"])
            natural_frames = int(meta["frames"] or 0) or None

        start_frame, end_frame, duration = _resolve_layer_timing(
            normalized_layer,
            natural_frames=natural_frames,
            plan_total_frames=explicit_total_frames or None,
        )
        normalized_layer["start_frame"] = start_frame
        if end_frame is None:
            normalized_layer.pop("end_frame", None)
        else:
            normalized_layer["end_frame"] = end_frame
            max_end = max(max_end, end_frame)
        if duration is None:
            normalized_layer.pop("duration", None)
        else:
            normalized_layer["duration"] = duration
        layers.append(normalized_layer)

    materialized["layers"] = layers
    materialized["width"] = width or 1920
    materialized["height"] = height or 1080
    materialized["fps"] = fps or 30.0
    materialized["total_frames"] = max(max_end, 1)
    return materialized


def _infer_stack_metrics(plan: dict[str, Any]) -> tuple[int, int, float, int]:
    materialized = materialize_layer_plan(plan)
    return (
        int(materialized["width"]),
        int(materialized["height"]),
        float(materialized["fps"]),
        int(materialized["total_frames"]),
    )


def execute_layer_plan(plan: dict[str, Any]) -> LayerStack:
    """Create a LayerStack from a Gemini-generated layer plan."""
    validate_layer_plan(plan)
    materialized = materialize_layer_plan(plan)
    width = int(materialized["width"])
    height = int(materialized["height"])
    fps = float(materialized["fps"])
    total_frames = int(materialized["total_frames"])
    stack = LayerStack(width=width, height=height, fps=fps, total_frames=total_frames)

    for layer_spec in materialized.get("layers", []):
        layer_type = layer_spec.get("type")
        if layer_type == "video":
            layer = make_video_layer(layer_spec["source"])
        elif layer_type == "image":
            duration = int(layer_spec.get("duration", 1) or 1)
            raw_size = layer_spec.get("size")
            size = tuple(raw_size) if raw_size is not None else None
            layer = make_image_layer(
                layer_spec["source"],
                duration=duration,
                size=size,
                blur_radius=_layer_blur_radius(layer_spec),
            )
        elif layer_type == "text":
            layer = make_text_layer(
                layer_spec["text"],
                tuple(layer_spec.get("position", (0, 0))),
                layer_spec.get("font_config"),
            )
        elif layer_type == "solid":
            duration = int(layer_spec.get("duration", 1) or 1)
            layer = make_solid_layer(
                tuple(layer_spec["color"]),
                duration=duration,
                size=tuple(layer_spec.get("size", (width, height))),
            )
        elif layer_type == "html":
            duration = int(layer_spec.get("duration", total_frames) or total_frames)
            layer = make_html_layer(
                source=layer_spec.get("source"),
                html=layer_spec.get("html"),
                duration=duration,
                size=tuple(layer_spec.get("size", (width, height))),
            )
        elif layer_type == "lottie":
            duration = int(layer_spec.get("duration", total_frames) or total_frames)
            layer = make_lottie_layer(
                str(layer_spec["source"]),
                duration=duration,
                size=tuple(layer_spec.get("size", (width, height))),
            )
        else:
            raise ValueError(f"Unsupported layer type: {layer_type}")

        layer.id = str(layer_spec.get("id", layer.id))
        layer.name = str(layer_spec.get("name", layer.name))
        layer.start_frame = int(layer_spec.get("start_frame", layer.start_frame))
        layer.end_frame = layer_spec.get("end_frame", layer.end_frame)
        if layer.end_frame is not None:
            layer.end_frame = int(layer.end_frame)
        layer.z_index = int(layer_spec.get("z_index", layer.z_index))
        layer.blend_mode = str(layer_spec.get("blend_mode", layer.blend_mode))
        layer.opacity = float(layer_spec.get("opacity", layer.opacity))
        layer.scale = float(layer_spec.get("scale", layer.scale))
        layer.rotation_deg = float(layer_spec.get("rotation_deg", layer.rotation_deg))
        if "position" in layer_spec:
            layer.position = tuple(layer_spec["position"])
            if layer_type == "text":
                font_config = layer_spec.get("font_config") if isinstance(layer_spec.get("font_config"), dict) else {}
                glow_radius = max(
                    0.0,
                    float(font_config.get("glow_radius", font_config.get("aura_radius", 0.0)) or 0.0),
                )
                if glow_radius > 0:
                    glow_extra = int(round(glow_radius * 2.0))
                    layer.position = (
                        int(round(float(layer.position[0]) - glow_extra)),
                        int(round(float(layer.position[1]) - glow_extra)),
                    )
        if layer_type == "image" and "color" in layer_spec:
            original_content_fn = layer.content_fn
            tint_color = list(layer_spec["color"])

            def tinted_content_fn(
                frame_index: int,
                *,
                _fn: Callable[[int], RGBAFrame] = original_content_fn,
                _color: Sequence[float] = tint_color,
            ) -> RGBAFrame:
                return _tint_rgba(_fn(frame_index), _color)

            layer.content_fn = tinted_content_fn
        if layer_spec.get("mask_source"):
            duration = int(layer.end_frame or total_frames or 1)
            layer.mask_fn = make_mask_layer(str(layer_spec["mask_source"]), duration=duration)
        if layer_spec.get("primitives"):
            original_content_fn = layer.content_fn
            primitive_chain = list(layer_spec["primitives"])

            def wrapped_content_fn(frame_index: int, *, _fn: Callable[[int], RGBAFrame] = original_content_fn,
                                   _chain: PrimitiveChain = primitive_chain) -> RGBAFrame:
                return _apply_primitive_chain(_fn(frame_index), _chain)

            layer.content_fn = wrapped_content_fn
        keyframes: dict[str, KeyframeTrack] = {}
        for name, spec in (layer_spec.get("keyframes") or {}).items():
            if name == "position":
                keyframes.update(_vector_tracks_from_spec(spec))
            else:
                keyframes[name] = _track_from_spec(spec)
        layer.keyframes = keyframes
        stack.add_layer(layer)

    return stack


def render_layer_plan(
    plan: dict[str, Any],
    output_path: str | Path,
    *,
    codec: str = "mp4v",
    background_color: tuple[float, float, float] = (0.0, 0.0, 0.0),
    start_frame: int = 0,
    end_frame: int | None = None,
    step: int = 1,
) -> str:
    """Build a LayerStack from *plan* and render it to a video file."""
    stack = execute_layer_plan(plan)
    return stack.render_to_video(
        output_path,
        codec=codec,
        background_color=background_color,
        start_frame=start_frame,
        end_frame=end_frame,
        step=step,
    )


def _stream_browser_mp4(
    frames: Iterable[RGBAFrame],
    output_path: Path,
    *,
    width: int,
    height: int,
    fps: float,
    background_color: tuple[float, float, float],
    ffmpeg_path: str,
) -> None:
    """Serialize same-destination exports, then stream to a unique temp file."""
    with _export_destination_lock(output_path):
        _stream_browser_mp4_locked(
            frames,
            output_path,
            width=width,
            height=height,
            fps=fps,
            background_color=background_color,
            ffmpeg_path=ffmpeg_path,
        )


def _encoder_stderr_tail(stderr_file, *, limit: int = 800) -> bytes:
    """Read only the diagnostic tail without retaining unbounded stderr in RAM."""
    try:
        stderr_file.flush()
        stderr_file.seek(0, os.SEEK_END)
        size = stderr_file.tell()
        stderr_file.seek(max(0, size - limit), os.SEEK_SET)
        return stderr_file.read(limit)
    except (OSError, ValueError):
        return b""


def _create_export_temp(output_path: Path) -> tuple[Path, int]:
    """Create a unique sibling using the caller's normal file-creation umask."""
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    for _attempt in range(100):
        candidate = output_path.with_name(
            f".{output_path.stem}.{secrets.token_hex(8)}.h264-tmp{output_path.suffix}"
        )
        try:
            fd = os.open(candidate, flags, 0o666)
        except FileExistsError:
            continue
        try:
            created_mode = os.fstat(fd).st_mode & 0o777
        finally:
            os.close(fd)
        return candidate, created_mode
    raise FileExistsError(f"Could not allocate a unique export temp beside {output_path}")


def _kill_encoder_process(proc: subprocess.Popen) -> None:
    """Stop an encoder and any subprocesses it spawned, with bounded waits."""
    if proc.poll() is None:
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except (OSError, ProcessLookupError):
            with suppress(OSError):
                proc.kill()
    try:
        proc.wait(timeout=_FFMPEG_KILL_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        with suppress(OSError):
            proc.kill()
        with suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=_FFMPEG_KILL_TIMEOUT_SECONDS)


def _encoder_supports_nonblocking_pipe() -> bool:
    """POSIX select supports pipe fds; Windows select is socket-only."""
    return os.name == "posix"


class _BlockingEncoderPipeWriter:
    """Bounded Windows-compatible pipe writer with a persistent watchdog thread."""

    def __init__(self, proc: subprocess.Popen) -> None:
        if proc.stdin is None:
            raise RuntimeError("ffmpeg streaming MP4 encoder has no stdin pipe")
        self._proc = proc
        self._queue: queue.Queue[tuple[bytes, threading.Event, list[BaseException]] | None] = (
            queue.Queue(maxsize=1)
        )
        self._thread = threading.Thread(
            target=self._run,
            name="lumeri-ffmpeg-stdin",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while True:
            task = self._queue.get()
            if task is None:
                return
            payload, done, errors = task
            try:
                stdin = self._proc.stdin
                if stdin is None:
                    raise BrokenPipeError("ffmpeg raw-frame pipe is unavailable")
                stdin.write(payload)
                stdin.flush()
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

    def write(self, payload: bytes) -> None:
        done = threading.Event()
        errors: list[BaseException] = []
        try:
            self._queue.put(
                (payload, done, errors),
                timeout=_FFMPEG_IO_STALL_TIMEOUT_SECONDS,
            )
        except queue.Full as exc:
            raise TimeoutError("ffmpeg input queue stopped accepting raw frames") from exc
        if not done.wait(_FFMPEG_IO_STALL_TIMEOUT_SECONDS):
            _kill_encoder_process(self._proc)
            done.wait(_FFMPEG_KILL_TIMEOUT_SECONDS)
            raise TimeoutError("ffmpeg stopped accepting raw frames")
        if errors:
            raise errors[0]

    def close(self) -> None:
        if not self._thread.is_alive():
            return
        try:
            self._queue.put(None, timeout=_FFMPEG_KILL_TIMEOUT_SECONDS)
        except queue.Full:
            return
        self._thread.join(timeout=_FFMPEG_KILL_TIMEOUT_SECONDS)


def _write_encoder_bytes(proc: subprocess.Popen, payload: bytes) -> None:
    """Write one frame without allowing a stuck encoder pipe to hang forever."""
    if proc.stdin is None:
        raise RuntimeError("ffmpeg streaming MP4 encoder has no stdin pipe")
    fd = proc.stdin.fileno()
    remaining = memoryview(payload)
    last_progress = time.monotonic()
    while remaining:
        if proc.poll() is not None:
            raise BrokenPipeError("ffmpeg exited while receiving raw frames")
        try:
            written = os.write(fd, remaining)
        except BlockingIOError:
            timeout = _FFMPEG_IO_STALL_TIMEOUT_SECONDS - (time.monotonic() - last_progress)
            if timeout <= 0.0:
                raise TimeoutError("ffmpeg stopped accepting raw frames")
            try:
                select.select([], [fd], [], min(timeout, 0.5))
            except InterruptedError:
                pass
            continue
        if written <= 0:
            raise BrokenPipeError("ffmpeg raw-frame pipe closed")
        remaining = remaining[written:]
        last_progress = time.monotonic()


def _stream_browser_mp4_locked(
    frames: Iterable[RGBAFrame],
    output_path: Path,
    *,
    width: int,
    height: int,
    fps: float,
    background_color: tuple[float, float, float],
    ffmpeg_path: str,
) -> None:
    """Stream RGBA frames directly into an atomic, browser-ready H.264 MP4."""
    try:
        output_mode = output_path.stat().st_mode & 0o777
    except OSError:
        output_mode = None
    # Allocate stderr first: if the OS cannot provide it, no sibling output
    # temp has been created and therefore nothing can leak.
    stderr_file = tempfile.TemporaryFile()
    try:
        tmp_path, created_mode = _create_export_temp(output_path)
    except BaseException:
        stderr_file.close()
        raise
    if output_mode is None:
        output_mode = created_mode
    cmd = [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-s:v",
        f"{width}x{height}",
        "-r",
        f"{fps:.12g}",
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        "libx264",
        "-vf",
        "scale=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-x264-params",
        "colorprim=bt709:transfer=bt709:colormatrix=bt709",
        "-movflags",
        "+faststart",
        str(tmp_path),
    ]
    proc: subprocess.Popen | None = None
    blocking_writer: _BlockingEncoderPipeWriter | None = None
    stderr = b""
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stderr=stderr_file,
            start_new_session=(os.name == "posix"),
        )
        if proc.stdin is None:
            raise RuntimeError("ffmpeg streaming MP4 encoder has no stdin pipe")
        if _encoder_supports_nonblocking_pipe():
            try:
                os.set_blocking(proc.stdin.fileno(), False)
            except (AttributeError, OSError) as exc:
                raise RuntimeError("ffmpeg raw-frame pipe cannot be made non-blocking") from exc
        else:
            blocking_writer = _BlockingEncoderPipeWriter(proc)
        for frame in frames:
            bgr = to_uint8(
                _flatten_rgba_for_video(frame, background_color=background_color)
            )
            payload = bgr.tobytes()
            if blocking_writer is None:
                _write_encoder_bytes(proc, payload)
            else:
                blocking_writer.write(payload)
        if blocking_writer is not None:
            blocking_writer.close()
            blocking_writer = None
        proc.stdin.close()
        try:
            return_code = proc.wait(timeout=_FFMPEG_EXIT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("ffmpeg did not exit after its input closed") from exc
        stderr = _encoder_stderr_tail(stderr_file)
    except BaseException as exc:
        if proc is not None:
            with suppress(Exception):
                if proc.stdin is not None and not proc.stdin.closed:
                    proc.stdin.close()
            _kill_encoder_process(proc)
        if blocking_writer is not None:
            blocking_writer.close()
        failure_stderr = _encoder_stderr_tail(stderr_file)
        with suppress(OSError):
            tmp_path.unlink()
        if isinstance(exc, (BrokenPipeError, TimeoutError)):
            detail = failure_stderr.decode("utf-8", errors="replace")[-800:]
            if not detail:
                detail = str(exc)
            raise RuntimeError(f"ffmpeg streaming MP4 encode failed: {detail}") from exc
        raise
    finally:
        if blocking_writer is not None:
            blocking_writer.close()
        stderr_file.close()
    if return_code != 0:
        with suppress(OSError):
            tmp_path.unlink()
        detail = stderr.decode("utf-8", errors="replace")[-800:]
        raise RuntimeError(f"ffmpeg streaming MP4 encode failed: {detail}")
    if not tmp_path.is_file() or tmp_path.stat().st_size <= 0:
        with suppress(OSError):
            tmp_path.unlink()
        raise RuntimeError("ffmpeg streaming MP4 encode produced no output file")
    try:
        tmp_path.chmod(output_mode)
        tmp_path.replace(output_path)
    except BaseException:
        with suppress(OSError):
            tmp_path.unlink()
        raise


def _transcode_browser_mp4(source_path: Path, output_path: Path) -> None:
    """Re-encode OpenCV's mp4v output into Chromium/Safari-friendly H.264."""
    tmp_path = output_path.with_name(f"{output_path.stem}.h264-tmp{output_path.suffix}")
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source_path),
        "-an",
        "-vf",
        "scale=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-profile:v",
        "baseline",
        "-movflags",
        "+faststart",
        str(tmp_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        if tmp_path.exists():
            tmp_path.unlink()
        raise RuntimeError(f"ffmpeg browser MP4 transcode failed: {proc.stderr[-800:]}")
    tmp_path.replace(output_path)


__all__ = [
    "BLEND_MODES",
    "Layer",
    "LayerStack",
    "execute_layer_plan",
    "materialize_layer_plan",
    "render_layer_plan",
    "release_video_decoders",
    "video_decoder_scope",
    "make_video_layer",
    "make_image_layer",
    "make_mask_layer",
    "make_text_layer",
    "make_solid_layer",
    "make_html_layer",
    "make_lottie_layer",
]
