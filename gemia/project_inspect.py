"""Read-only inspection helpers for stored Lumeri projects.

The goal is to give AI and developers a *compact* view of the timeline they
can quote in prompts or read at a glance, instead of dumping the full
normalized project JSON (which is verbose and includes UI noise).
"""
from __future__ import annotations

from typing import Any

from .project_store import ProjectStore


_DEFAULT_EFFECTS = {
    "rotation": 0,
    "mirrored": False,
    "muted": False,
    "audioDetached": False,
    "speed": 1,
}


def _non_default_effects(effects: Any) -> dict[str, Any]:
    if not isinstance(effects, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in effects.items():
        default = _DEFAULT_EFFECTS.get(key)
        if default is None or value != default:
            out[key] = value
    return out


def _summarize_clip(clip: dict[str, Any]) -> dict[str, Any]:
    start = float(clip.get("start") or 0.0)
    duration = float(clip.get("duration") or 0.0)
    return {
        "id": str(clip.get("id") or ""),
        "track_id": str(clip.get("track_id") or ""),
        "name": str(clip.get("name") or ""),
        "media_kind": str(clip.get("media_kind") or ""),
        "start": round(start, 6),
        "end": round(start + duration, 6),
        "duration": round(duration, 6),
        "source_in": round(float(clip.get("source_in") or 0.0), 6),
        "source_out": round(float(clip.get("source_out") or 0.0), 6),
        "effects": _non_default_effects(clip.get("effects")),
    }


def inspect_project(
    store: ProjectStore, project_id: str, *, history: int = 0
) -> dict[str, Any]:
    state = store.load(project_id)
    meta = store.load_meta(project_id)
    timeline = state.get("timeline") or {}
    clips_raw = timeline.get("clips") or []
    summary = {
        "project_id": project_id,
        "patch_seq": int(meta.get("patch_seq") or 0),
        "created_at": meta.get("created_at"),
        "updated_at": meta.get("updated_at"),
        "timeline": {
            "fps": timeline.get("fps"),
            "width": timeline.get("width"),
            "height": timeline.get("height"),
            "duration": timeline.get("duration"),
            "clip_count": len(clips_raw),
            "clips": [_summarize_clip(c) for c in clips_raw if isinstance(c, dict)],
        },
        "asset_count": len(state.get("assets") or []),
        "state_path": str(store.state_path(project_id)),
    }
    undo_log = list(meta.get("undo_log") or [])
    if undo_log:
        summary["undo_log_tail"] = undo_log[-3:]
    if history > 0:
        entries = store.history(project_id)
        tail = entries[-history:] if history < len(entries) else entries
        summary["recent_patches"] = [
            {
                "seq": int(e.get("seq") or 0),
                "session_id": e.get("session_id"),
                "script_hash": e.get("script_hash"),
                "applied_at": e.get("applied_at"),
                "op_count": len(((e.get("patch") or {}).get("ops") or [])),
            }
            for e in tail
        ]
    return summary


def render_text(summary: dict[str, Any]) -> str:
    timeline = summary.get("timeline") or {}
    lines: list[str] = []
    lines.append(
        f"Project {summary.get('project_id')}  "
        f"(seq={summary.get('patch_seq')}, updated={summary.get('updated_at')})"
    )
    lines.append(
        f"Timeline {timeline.get('fps')}fps "
        f"{timeline.get('width')}x{timeline.get('height')}  "
        f"duration={timeline.get('duration')}s  "
        f"clips={timeline.get('clip_count')}"
    )
    for clip in timeline.get("clips") or []:
        effects = clip.get("effects") or {}
        effect_str = f"  fx={effects}" if effects else ""
        lines.append(
            f"  {clip.get('track_id'):<3} "
            f"[{clip.get('start'):>6.2f}-{clip.get('end'):>6.2f}]  "
            f"{clip.get('id')}  {clip.get('name')}{effect_str}"
        )
    lines.append(f"Assets: {summary.get('asset_count')}")
    recent = summary.get("recent_patches") or []
    if recent:
        lines.append("Recent patches:")
        for entry in recent:
            lines.append(
                f"  seq={entry.get('seq')}  "
                f"session={entry.get('session_id')}  "
                f"ops={entry.get('op_count')}  "
                f"at={entry.get('applied_at')}"
            )
    undo_tail = summary.get("undo_log_tail") or []
    if undo_tail:
        lines.append("Undo log (tail):")
        for entry in undo_tail:
            lines.append(
                f"  {entry.get('at')}  "
                f"{entry.get('from_seq')} -> {entry.get('to_seq')}  "
                f"discarded={entry.get('discarded')}"
            )
    return "\n".join(lines) + "\n"
