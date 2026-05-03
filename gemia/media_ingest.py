"""Optional image metadata ingest substrate for Gemia media libraries.

The OpenImageIO dependency is intentionally optional.  When it is unavailable,
the module falls back to Pillow, then OpenCV, then a minimal file-only record so
media-library import stays deterministic in offline automation.
"""
from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Any, Iterable


IMAGE_EXTENSIONS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


def openimageio_available() -> bool:
    return _load_oiio() is not None


def probe_still_metadata(path: str | Path) -> dict[str, Any]:
    image = _validate_image(path)
    diagnostics: list[str] = []
    oiio = _probe_with_oiio(image, diagnostics)
    if oiio:
        return oiio
    pil = _probe_with_pillow(image, diagnostics)
    if pil:
        return pil
    cv2 = _probe_with_cv2(image, diagnostics)
    if cv2:
        return cv2
    diagnostics.append("metadata fallback limited to filesystem fields")
    return _base_record(image, "filesystem", diagnostics)


def probe_image_sequence(paths: Iterable[str | Path]) -> dict[str, Any]:
    frames = [probe_still_metadata(path) for path in paths]
    if not frames:
        raise ValueError("paths cannot be empty")
    widths = sorted({int(frame.get("width") or 0) for frame in frames})
    heights = sorted({int(frame.get("height") or 0) for frame in frames})
    channels = sorted({int(frame.get("channels") or 0) for frame in frames})
    fingerprints = [str(frame.get("fingerprint") or "") for frame in frames]
    sequence_fingerprint = hashlib.sha256("|".join(fingerprints).encode("utf-8")).hexdigest()
    diagnostics = _sequence_diagnostics(widths, heights, channels)
    dimensions_summary = sorted(
        {f"{int(frame.get('width') or 0)}x{int(frame.get('height') or 0)}" for frame in frames}
    )
    return {
        "schema_version": 1,
        "ingest_backend": _sequence_backend(frames),
        "openimageio_available": openimageio_available(),
        "frame_count": len(frames),
        "sequence_fingerprint": sequence_fingerprint,
        "consistent_dimensions": len(widths) == 1 and len(heights) == 1,
        "width": widths[0] if len(widths) == 1 else 0,
        "height": heights[0] if len(heights) == 1 else 0,
        "channels": channels[0] if len(channels) == 1 else 0,
        "frames": frames,
        "dimensions_summary": dimensions_summary,
        "diagnostics": diagnostics,
    }


def _validate_image(path: str | Path) -> Path:
    image = Path(path).expanduser().resolve()
    if not image.exists() or not image.is_file():
        raise FileNotFoundError(f"image file not found: {path}")
    if image.suffix.lower() not in IMAGE_EXTENSIONS:
        raise ValueError(f"unsupported image extension: {image.suffix or 'unknown'}")
    return image


def _base_record(path: Path, backend: str, diagnostics: list[str]) -> dict[str, Any]:
    stat = path.stat()
    return {
        "schema_version": 1,
        "ingest_backend": backend,
        "openimageio_available": openimageio_available(),
        "path": str(path),
        "name": path.name,
        "extension": path.suffix.lower(),
        "mime_type": mimetypes.guess_type(str(path))[0] or "application/octet-stream",
        "file_size_bytes": int(stat.st_size),
        "fingerprint": _sha256_file(path),
        "width": 0,
        "height": 0,
        "channels": 0,
        "color_space": "",
        "bit_depth": "",
        "metadata": {},
        "diagnostics": list(diagnostics),
    }


def _probe_with_oiio(path: Path, diagnostics: list[str]) -> dict[str, Any] | None:
    oiio = _load_oiio()
    if oiio is None:
        diagnostics.append("OpenImageIO unavailable; used fallback metadata reader")
        return None
    try:
        inp = oiio.ImageInput.open(str(path))
        if inp is None:
            diagnostics.append("OpenImageIO could not open image; used fallback metadata reader")
            return None
        try:
            spec = inp.spec()
            record = _base_record(path, "openimageio", diagnostics)
            attrs = {}
            extra_attribs = getattr(spec, "extra_attribs", [])
            for attr in extra_attribs:
                name = str(getattr(attr, "name", "") or "")
                if name:
                    attrs[name] = _json_safe(getattr(attr, "value", ""))
            record.update(
                {
                    "width": int(getattr(spec, "width", 0) or 0),
                    "height": int(getattr(spec, "height", 0) or 0),
                    "channels": int(getattr(spec, "nchannels", 0) or 0),
                    "color_space": str(attrs.get("oiio:ColorSpace") or attrs.get("ColorSpace") or ""),
                    "bit_depth": str(getattr(getattr(spec, "format", None), "basetype", "") or ""),
                    "metadata": attrs,
                }
            )
            return record
        finally:
            close = getattr(inp, "close", None)
            if callable(close):
                close()
    except Exception as exc:
        diagnostics.append(f"OpenImageIO metadata probe failed: {exc}")
        return None


def _probe_with_pillow(path: Path, diagnostics: list[str]) -> dict[str, Any] | None:
    try:
        from PIL import Image
    except Exception:
        diagnostics.append("Pillow unavailable; trying OpenCV fallback")
        return None
    try:
        with Image.open(path) as image:
            record = _base_record(path, "pillow", diagnostics)
            record.update(
                {
                    "width": int(image.width),
                    "height": int(image.height),
                    "channels": len(image.getbands()),
                    "color_space": str(image.mode or ""),
                    "bit_depth": str(image.getbands()[0] if image.getbands() else ""),
                    "metadata": {str(k): _json_safe(v) for k, v in image.info.items()},
                }
            )
            return record
    except Exception as exc:
        diagnostics.append(f"Pillow metadata probe failed: {exc}")
        return None


def _probe_with_cv2(path: Path, diagnostics: list[str]) -> dict[str, Any] | None:
    try:
        import cv2
    except Exception:
        diagnostics.append("OpenCV unavailable")
        return None
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        diagnostics.append("OpenCV could not read image")
        return None
    height, width = image.shape[:2]
    channels = int(image.shape[2]) if len(image.shape) > 2 else 1
    record = _base_record(path, "opencv", diagnostics)
    record.update({"width": int(width), "height": int(height), "channels": channels, "metadata": {}})
    return record


def _load_oiio() -> Any | None:
    try:
        import OpenImageIO as oiio
        return oiio
    except Exception:
        return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


def _sequence_backend(frames: list[dict[str, Any]]) -> str:
    backends = {str(frame.get("ingest_backend") or "") for frame in frames}
    if len(backends) == 1:
        return backends.pop()
    return "mixed"


def _sequence_diagnostics(widths: list[int], heights: list[int], channels: list[int]) -> list[str]:
    diagnostics: list[str] = []
    if len(widths) > 1 or len(heights) > 1:
        diagnostics.append("image sequence has mixed dimensions")
    if len(channels) > 1:
        diagnostics.append("image sequence has mixed channel counts")
    return diagnostics


__all__ = ["IMAGE_EXTENSIONS", "openimageio_available", "probe_image_sequence", "probe_still_metadata"]
