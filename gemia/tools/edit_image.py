"""edit_image: crop | rotate | resize | blur | denoise | remove_background.

Each op uses ffmpeg-only filters. ``crop``/``rotate``/``resize`` overlap with
``transform_geometry`` on purpose — this verb is the image-specific surface
the model reaches for when it's working with an image asset and doesn't
need to think about geometry math. ``blur`` and ``denoise`` are image-only
filters that don't belong in transform_geometry.

``remove_background`` is intentionally NOT implemented in this batch.
True background removal needs an ML model (U2Net / rembg / SAM) which
batch 1 ("纯 ffmpeg, 低风险") excludes. Calling it raises
NotImplementedError with a clear message so the model knows to fall back
to another approach (e.g. chroma_key on a known-color background via a
follow-up verb, or composite with a pre-cut PNG).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from gemia.tools._context import ToolContext
from gemia.tools._ffmpeg import run_ffmpeg_with_progress


async def dispatch(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    asset_id = str(args["asset_id"])
    operation = str(args["operation"])
    params = args.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError(f"params must be an object, got {type(params).__name__}")

    src = ctx.registry.get(asset_id)
    if src.kind != "image":
        raise ValueError(f"edit_image requires an image asset, got {src.kind!r}")

    handler = _OPS.get(operation)
    if handler is None:
        raise ValueError(
            f"unknown edit_image operation: {operation!r}. Known: {', '.join(_OPS.keys())}"
        )

    vf, label, metadata = handler(params)
    return await _run(ctx, src.path, asset_id, vf, label, metadata, operation)


def _op_crop(params: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    for key in ("x", "y", "w", "h"):
        if key not in params:
            raise ValueError(f"crop requires params.{key}")
    x, y, w, h = (int(params[k]) for k in ("x", "y", "w", "h"))
    if w <= 0 or h <= 0:
        raise ValueError(f"crop w/h must be > 0, got w={w} h={h}")
    return (
        f"crop={w}:{h}:{x}:{y}",
        f"crop {w}x{h}@({x},{y})",
        {"x": x, "y": y, "w": w, "h": h},
    )


def _op_rotate(params: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    if "angle_deg" not in params:
        raise ValueError("rotate requires params.angle_deg")
    angle = float(params["angle_deg"])
    rad = angle * 3.141592653589793 / 180.0
    return (
        f"rotate={rad:.6f}:ow=rotw({rad:.6f}):oh=roth({rad:.6f}):c=black",
        f"rotate {angle:+.1f}°",
        {"angle_deg": angle},
    )


def _op_resize(params: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    target_w = params.get("w")
    target_h = params.get("h")
    if target_w is None and target_h is None:
        raise ValueError("resize requires params.w and/or params.h")
    w_expr = int(target_w) if target_w is not None else -2
    h_expr = int(target_h) if target_h is not None else -2
    return (
        f"scale={w_expr}:{h_expr}",
        f"resize w={w_expr} h={h_expr}",
        {"w": w_expr if target_w is not None else None,
         "h": h_expr if target_h is not None else None},
    )


def _op_blur(params: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    radius_raw = params.get("radius", 5.0)
    try:
        radius = float(radius_raw)
    except (TypeError, ValueError):
        raise ValueError(f"blur radius must be a number, got {radius_raw!r}")
    if radius <= 0:
        raise ValueError(f"blur radius must be > 0, got {radius}")
    sigma = max(0.5, radius / 2.0)
    return (
        f"gblur=sigma={sigma:.3f}",
        f"gaussian blur σ={sigma:.2f}",
        {"radius": radius, "sigma": sigma},
    )


def _op_denoise(params: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    strength_raw = params.get("strength", 1.0)
    try:
        strength = float(strength_raw)
    except (TypeError, ValueError):
        raise ValueError(f"denoise strength must be a number, got {strength_raw!r}")
    if strength <= 0:
        raise ValueError(f"denoise strength must be > 0, got {strength}")
    # nlmeans is high-quality but slow; tune patch/research sizes by strength.
    s = max(1.0, min(strength, 10.0))
    return (
        f"nlmeans=s={s:.2f}:p=7:r=15",
        f"denoise strength={s:.2f}",
        {"strength": s, "method": "nlmeans"},
    )


def _op_remove_background(_params: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    raise NotImplementedError(
        "remove_background requires an ML model (rembg / U2Net / SAM) that batch 1 "
        "deliberately excludes. Workarounds: (a) if the background is a known solid "
        "color, use a future chroma_key verb; (b) provide a pre-cut PNG with alpha "
        "and use composite. This op is registered so the schema doesn't lie about it "
        "being available, but the host honestly raises rather than producing a fake "
        "background-removed image."
    )


async def _run(
    ctx: ToolContext,
    src_path: Path,
    src_id: str,
    vf: str,
    label: str,
    metadata: dict[str, Any],
    op_name: str,
) -> dict[str, Any]:
    new_id = ctx.registry.allocate_id("image")
    out_path = ctx.child_path(new_id, _image_out_ext(src_path))
    cmd = [
        "ffmpeg", "-y",
        "-i", str(src_path),
        "-vf", vf,
        "-frames:v", "1",
        str(out_path),
    ]
    await run_ffmpeg_with_progress(cmd, total_seconds=0.0, progress=ctx.emit_progress)

    summary = f"edited image {src_id} — {label}"
    record = ctx.registry.register_output(
        new_id, kind="image", path=out_path, summary=summary, lineage=[src_id]
    )
    return {
        "asset_id": new_id,
        "summary": record.summary,
        "metadata": {"operation": op_name, "params": metadata, "kind": "image"},
    }


def _image_out_ext(src_path: Path) -> str:
    ext = src_path.suffix.lower()
    if ext in {".png", ".jpg", ".jpeg", ".webp"}:
        return ext
    return ".png"


_OPS: dict[str, Callable[[dict[str, Any]], tuple[str, str, dict[str, Any]]]] = {
    "crop": _op_crop,
    "rotate": _op_rotate,
    "resize": _op_resize,
    "blur": _op_blur,
    "denoise": _op_denoise,
    "remove_background": _op_remove_background,
}


__all__ = ["dispatch"]
