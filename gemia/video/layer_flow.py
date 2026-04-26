"""Planner-facing layer-first workflow entry points."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from gemia.video.layers import materialize_layer_plan
from gemia.video.preview import render_shadow_preview

_CANVAS_KEYS = {"width", "height", "fps", "total_frames"}
_LAYER_KEYS = {
    "id",
    "name",
    "type",
    "source",
    "text",
    "color",
    "position",
    "font_config",
    "start_frame",
    "end_frame",
    "duration",
    "z_index",
    "opacity",
    "scale",
    "rotation_deg",
    "blend_mode",
    "mask_source",
    "primitives",
    "keyframes",
    "metadata",
}


def render_layer_workflow(
    input_path: str,
    output_path: str,
    *,
    overlay_layers: list[dict[str, Any]] | None = None,
    title: str = "",
    title_position: Sequence[int | float] | None = None,
    title_font_size: int = 48,
    title_start_frame: int = 0,
    title_duration_frames: int | None = None,
    canvas: dict[str, Any] | None = None,
    include_source: bool = True,
    backend: str | None = "auto",
    frame_step: int = 2,
    max_long_edge: int = 540,
    proxy_resolution: int = 540,
    proxy_root: str | None = None,
) -> str:
    """Render a planner-authored layer-first video workflow via the graph backend."""
    source_path = Path(input_path).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Layer workflow input does not exist: {source_path}")

    layer_plan = _build_layer_workflow_plan(
        source_path,
        overlay_layers=overlay_layers,
        title=title,
        title_position=title_position,
        title_font_size=title_font_size,
        title_start_frame=title_start_frame,
        title_duration_frames=title_duration_frames,
        canvas=canvas,
        include_source=include_source,
    )
    materialized_plan = materialize_layer_plan(layer_plan)
    result = render_shadow_preview(
        layer_plan,
        output_path,
        backend=backend,
        frame_step=frame_step,
        max_long_edge=max_long_edge,
        proxy_resolution=proxy_resolution,
        proxy_root=proxy_root,
    )
    _write_layer_flow_manifest(
        result.output_path,
        layer_plan=layer_plan,
        materialized_plan=materialized_plan,
        preview_manifest_path=result.manifest_path,
    )
    return result.output_path


def _build_layer_workflow_plan(
    source_path: Path,
    *,
    overlay_layers: list[dict[str, Any]] | None,
    title: str,
    title_position: Sequence[int | float] | None,
    title_font_size: int,
    title_start_frame: int,
    title_duration_frames: int | None,
    canvas: dict[str, Any] | None,
    include_source: bool,
) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "metadata": {
            "authoring_mode": "planner_controller_layer_flow",
            "source_input": str(source_path),
        },
        "layers": [],
    }
    for key, value in dict(canvas or {}).items():
        if key in _CANVAS_KEYS and value is not None:
            plan[key] = value

    if include_source:
        plan["layers"].append(
            {
                "id": "source_video",
                "type": "video",
                "source": str(source_path),
                "start_frame": 0,
                "z_index": 0,
            }
        )

    for index, layer in enumerate(list(overlay_layers or []), start=1):
        plan["layers"].append(_normalize_overlay_layer(layer, source_path, index=index))

    cleaned_title = str(title or "").strip()
    if cleaned_title:
        title_layer: dict[str, Any] = {
            "id": "title_overlay",
            "type": "text",
            "text": cleaned_title,
            "position": _position_pair(title_position, default=(48, 48)),
            "font_config": {"size": max(int(title_font_size or 48), 1)},
            "start_frame": max(int(title_start_frame or 0), 0),
            "z_index": 100,
        }
        if title_duration_frames is not None:
            title_layer["duration"] = max(int(title_duration_frames), 1)
        plan["layers"].append(title_layer)

    if not plan["layers"]:
        raise ValueError("Layer workflow needs at least the source video or one overlay layer.")
    return plan


def _normalize_overlay_layer(layer: Mapping[str, Any], source_path: Path, *, index: int) -> dict[str, Any]:
    if not isinstance(layer, Mapping):
        raise TypeError("overlay_layers entries must be objects.")
    normalized = {
        key: deepcopy(value)
        for key, value in layer.items()
        if key in _LAYER_KEYS and value is not None
    }
    if not normalized.get("id"):
        normalized["id"] = f"overlay_{index}"
    if not normalized.get("type"):
        normalized["type"] = "text" if normalized.get("text") else "image"
    if normalized.get("source") == "$input":
        normalized["source"] = str(source_path)
    if normalized.get("mask_source") == "$input":
        normalized["mask_source"] = str(source_path)
    normalized.setdefault("start_frame", 0)
    normalized.setdefault("z_index", index)
    if normalized["type"] == "text":
        normalized["text"] = str(normalized.get("text", "")).strip()
        normalized.setdefault("position", [48, 48 + (index - 1) * 56])
        font_config = dict(normalized.get("font_config", {}) or {})
        font_config.setdefault("size", 42)
        normalized["font_config"] = font_config
    return normalized


def _position_pair(
    value: Sequence[int | float] | None,
    *,
    default: tuple[int, int],
) -> list[int]:
    if value is None or len(value) != 2:
        return [int(default[0]), int(default[1])]
    return [int(round(float(value[0]))), int(round(float(value[1])))]


def _write_layer_flow_manifest(
    output_path: str,
    *,
    layer_plan: dict[str, Any],
    materialized_plan: dict[str, Any],
    preview_manifest_path: str,
) -> None:
    path = Path(output_path).expanduser().resolve().with_suffix(".layer-flow.json")
    payload = {
        "output_path": str(Path(output_path).expanduser().resolve()),
        "preview_manifest_path": preview_manifest_path,
        "authoring_mode": "planner_controller_layer_flow",
        "layer_count": len(materialized_plan.get("layers", [])),
        "authored_plan": layer_plan,
        "materialized_plan": materialized_plan,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


__all__ = ["render_layer_workflow"]
