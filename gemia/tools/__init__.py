"""Lumeri v3 verb dispatch table.

Creative-action tools the model can call. ``TOOL_SCHEMAS`` is the
function-calling schema list sent to Gemini. ``DISPATCHER`` maps a verb
name to an async coroutine ``async def(args: dict, ctx: ToolContext)``
that executes the call and returns a tool-result dict.

Implemented:

    - batch 0 + 1 (pure ffmpeg): analyze_media, edit_video, color_grade,
      add_overlay, export, composite, arrange_timeline, mix_audio,
      transform_geometry, edit_image, extract_frame
    - batch 2.1 (sync provider, real money): generate_image (Nano Banana 2
      via Vertex)
    - batch 2.2/2.3 (provider media): generate_video (Veo LRO via Vertex),
      generate_audio (Lyria predict via Vertex)
    - batch 3 (v4 build): web_search / web_open / fetch (host-side internet),
      run_shell (sandboxed bash via the M1 two-tier sandbox-exec boundary)
    - timeline v1: get_timeline + fine-grained timeline_* document verbs +
      render_preview (persistent per-session timeline, logged + undoable)

Dispatchers must NOT swallow errors. The agent loop wraps each call in
try/except and emits a ``tool_exec_error`` event on exception.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from gemia.tools._context import (
    AssetRecord,
    AssetRegistry,
    ProgressCallback,
    ProgressUpdate,
    ToolContext,
)
from gemia.tools._schema import TOOL_NAMES, TOOL_SCHEMAS

from gemia.tools import add_overlay as _add_overlay
from gemia.tools import adjust_media as _adjust_media
from gemia.tools import analyze_media as _analyze_media
from gemia.tools import arrange_timeline as _arrange_timeline
from gemia.tools import build as _build
from gemia.tools import color_grade as _color_grade
from gemia.tools import composite as _composite
from gemia.tools import edit_audio as _edit_audio
from gemia.tools import edit_image as _edit_image
from gemia.tools import edit_video as _edit_video
from gemia.tools import elicit as _elicit
from gemia.tools import export as _export
from gemia.tools import extract_frame as _extract_frame
from gemia.tools import fetch as _fetch
from gemia.tools import files as _files
from gemia.tools import generate_audio as _generate_audio
from gemia.tools import generate_image as _generate_image
from gemia.tools import generate_video as _generate_video
from gemia.tools import inspect_timeline as _inspect_timeline
from gemia.tools import layer as _layer
from gemia.tools import lumen_render_range as _lumen_render_range
from gemia.tools import lumen_seek as _lumen_seek
from gemia.tools import lottie as _lottie
from gemia.tools import log_note as _log_note
from gemia.tools import media_annotations as _media_annotations
from gemia.tools import mix_audio as _mix_audio
from gemia.tools import paint as _paint
from gemia.tools import probe_media as _probe_media
from gemia.tools import remember as _remember
from gemia.tools import run_shell as _run_shell
from gemia.tools import safe_areas as _safe_areas
from gemia.tools import save_skill as _save_skill
from gemia.tools import search_library as _search_library
from gemia.tools import smart_reframe as _smart_reframe
from gemia.tools import timeline as _timeline
from gemia.tools import transform_geometry as _transform_geometry
from gemia.tools import web_search as _web_search

Dispatcher = Callable[[dict[str, Any], ToolContext], Awaitable[dict[str, Any]]]


_STUB_REASONS: dict[str, str] = {}


def _make_stub(name: str) -> Dispatcher:
    reason = _STUB_REASONS.get(name, "not implemented yet")
    async def _stub(_args: dict[str, Any], _ctx: ToolContext) -> dict[str, Any]:
        raise NotImplementedError(f"tool '{name}' is not implemented: {reason}")

    _stub.__name__ = f"stub_{name}"
    return _stub


_REAL: dict[str, Dispatcher] = {
    "analyze_media":      _analyze_media.dispatch,
    "adjust_media":       _adjust_media.dispatch,
    "paint_overlay":      _paint.dispatch_overlay,
    "paint_mask_effect":  _paint.dispatch_mask_effect,
    "edit_video":         _edit_video.dispatch,
    "color_grade":        _color_grade.dispatch,
    "add_overlay":        _add_overlay.dispatch,
    "export":             _export.dispatch,
    "composite":          _composite.dispatch,
    "arrange_timeline":   _arrange_timeline.dispatch,
    "mix_audio":          _mix_audio.dispatch,
    "edit_audio":         _edit_audio.dispatch,
    "transform_geometry": _transform_geometry.dispatch,
    "smart_reframe":      _smart_reframe.dispatch,
    "edit_image":         _edit_image.dispatch,
    "extract_frame":      _extract_frame.dispatch,
    "probe_media":        _probe_media.dispatch,
    "generate_image":     _generate_image.dispatch,
    "generate_video":     _generate_video.dispatch,
    "generate_audio":     _generate_audio.dispatch,
    "search_library":     _search_library.dispatch,
    "annotate_media":     _media_annotations.dispatch_annotate,
    "get_media_annotations": _media_annotations.dispatch_get,
    "write_media_annotation": _media_annotations.dispatch_write,
    "inspect_lottie":     _lottie.dispatch_inspect,
    "web_search":         _web_search.dispatch,
    "web_open":           _web_search.dispatch_open,
    "fetch":              _fetch.dispatch,
    "read_file":          _files.dispatch_read_file,
    "write_file":         _files.dispatch_write_file,
    "copy_in":            _files.dispatch_copy_in,
    "list_dir":           _files.dispatch_list_dir,
    "move_file":          _files.dispatch_move_file,
    "organize_files":     _files.dispatch_organize_files,
    "elicit":             _elicit.dispatch,
    "run_shell":          _run_shell.dispatch,
    "file_list":          _files.dispatch_list,
    "file_read":          _files.dispatch_read,
    "file_write":         _files.dispatch_write,
    "file_copy":          _files.dispatch_copy,
    "file_move":          _files.dispatch_move,
    "file_delete":        _files.dispatch_delete,
    "build":              _build.dispatch,
    "check_job":          _build.dispatch_check,
    "wait_for_job":       _build.dispatch_wait,
    "save_skill":         _save_skill.dispatch_save_skill,
    "recall_skills":      _save_skill.dispatch_recall_skills,
    "remember":           _remember.dispatch,
    "log_note":           _log_note.dispatch,
    "get_timeline":             _timeline.dispatch_get,
    "timeline_insert_clip":     _timeline.dispatch_insert,
    "timeline_delete_clip":     _timeline.dispatch_delete,
    "timeline_move_clip":       _timeline.dispatch_move,
    "timeline_trim_clip":       _timeline.dispatch_trim,
    "timeline_split_clip":      _timeline.dispatch_split,
    "timeline_set_clip_time":   _timeline.dispatch_set_time,
    "timeline_add_transition":  _timeline.dispatch_transition,
    "timeline_set_clip_effects": _timeline.dispatch_effects,
    "timeline_add_track":       _timeline.dispatch_add_track,
    "timeline_set_track":       _timeline.dispatch_set_track,
    "timeline_undo":            _timeline.dispatch_undo,
    "inspect_timeline":         _inspect_timeline.dispatch,
    "get_safe_areas":           _safe_areas.dispatch,
    "render_preview":           _timeline.dispatch_render_preview,
    "project_export":           _timeline.dispatch_project_export,
    "project_export_otio":      _timeline.dispatch_export_otio,
    "project_import_otio":      _timeline.dispatch_import_otio,
    "get_lumenframe":           _layer.dispatch_get_lumenframe,
    "lumen_patch":              _layer.dispatch_lumen_patch,
    "lumen_add_layer":          _layer.dispatch_lumen_add_layer,
    "lumen_set_transform":      _layer.dispatch_lumen_set_transform,
    "lumen_set_opacity":        _layer.dispatch_lumen_set_opacity,
    "lumen_delete_layer":       _layer.dispatch_lumen_delete_layer,
    "lumen_move_layer":         _layer.dispatch_lumen_move_layer,
    "lumen_set_visibility":     _layer.dispatch_lumen_set_visibility,
    "lumen_select":             _layer.dispatch_lumen_select,
    "lumen_set_mask":           _layer.dispatch_lumen_set_mask,
    "lumen_key":                _layer.dispatch_lumen_key,
    "lumen_render":             _layer.dispatch_lumen_render,
    "lumen_set_range":          _layer.dispatch_lumen_set_range,
    "lumen_set_lane":           _layer.dispatch_lumen_set_lane,
    "lumen_retime_segment":     _layer.dispatch_lumen_retime_segment,
    "lumen_reverse":            _layer.dispatch_lumen_reverse,
    "lumen_time_remap":         _layer.dispatch_lumen_time_remap,
    "lumen_speed_ramp":         _layer.dispatch_lumen_speed_ramp,
    "lumen_ripple_delete":      _layer.dispatch_lumen_ripple_delete,
    "lumen_merge_compositions": _layer.dispatch_lumen_merge_compositions,
    "lumen_set_work_area":      _layer.dispatch_lumen_set_work_area,
    "lumen_seek":               _lumen_seek.dispatch,
    "lumen_render_range":       _lumen_render_range.dispatch,
}


DISPATCHER: dict[str, Dispatcher] = {
    name: _REAL.get(name) or _make_stub(name) for name in TOOL_NAMES
}


__all__ = [
    "TOOL_SCHEMAS",
    "TOOL_NAMES",
    "DISPATCHER",
    "Dispatcher",
    "ToolContext",
    "AssetRegistry",
    "AssetRecord",
    "ProgressCallback",
    "ProgressUpdate",
]
