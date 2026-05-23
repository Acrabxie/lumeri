from __future__ import annotations

import copy
from typing import Any

from gemia.project_model import normalize_project


def apply_timeline_patches(project_state: dict[str, Any] | None, patches: list[dict[str, Any]]) -> dict[str, Any]:
    project = normalize_project(project_state or {})
    for patch in patches:
        if not isinstance(patch, dict) or patch.get("version") != 1:
            raise ValueError("Unsupported TimelinePatch")
        for op in patch.get("ops") or []:
            _apply_op(project, op)
    _recompute_duration(project)
    return project


def _apply_op(project: dict[str, Any], op: dict[str, Any]) -> None:
    if not isinstance(op, dict):
        raise ValueError("TimelinePatch op must be an object")
    operation = str(op.get("op") or "")
    data = op.get("data") if isinstance(op.get("data"), dict) else {}
    asset = data.get("asset") if isinstance(data.get("asset"), dict) else None
    clip = copy.deepcopy(data.get("clip")) if isinstance(data.get("clip"), dict) else None
    if not clip:
        raise ValueError(f"{operation} is missing clip data")
    provenance = op.get("provenance") if isinstance(op.get("provenance"), dict) else None
    if provenance is not None:
        clip["provenance"] = provenance
    if asset:
        _upsert_asset(project, copy.deepcopy(asset))
    if operation == "insert_clip":
        project["timeline"]["clips"].append(clip)
    elif operation == "replace_clip":
        clip_id = str(op.get("clip_id") or clip.get("id") or "")
        if not clip_id:
            raise ValueError("replace_clip is missing clip_id")
        clips = project["timeline"]["clips"]
        for index, existing in enumerate(clips):
            if isinstance(existing, dict) and str(existing.get("id")) == clip_id:
                replacement = {**existing, **clip, "id": clip_id}
                clips[index] = replacement
                return
        raise ValueError(f"replace_clip target not found: {clip_id}")
    else:
        raise ValueError(f"Unsupported TimelinePatch op: {operation}")


def _upsert_asset(project: dict[str, Any], asset: dict[str, Any]) -> None:
    asset_id = str(asset.get("id") or asset.get("asset_id") or "")
    if not asset_id:
        raise ValueError("TimelinePatch asset is missing id")
    asset["id"] = asset_id
    asset["asset_id"] = str(asset.get("asset_id") or asset_id)
    assets = project["assets"]
    for index, existing in enumerate(assets):
        if isinstance(existing, dict) and str(existing.get("id")) == asset_id:
            assets[index] = {**existing, **asset}
            return
    assets.append(asset)


def _recompute_duration(project: dict[str, Any]) -> None:
    end = 0.0
    for clip in project.get("timeline", {}).get("clips") or []:
        if isinstance(clip, dict):
            end = max(end, float(clip.get("start") or 0.0) + float(clip.get("duration") or 0.0))
    project["timeline"]["duration"] = round(end, 6)
