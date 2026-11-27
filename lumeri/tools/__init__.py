"""Lumeri v3 verb dispatch table.

Creative-action tools the model can call. ``TOOL_SCHEMAS`` is the
function-calling schema list sent to Gemini. ``DISPATCHER`` maps a verb
name to an async coroutine ``async def(args: dict, ctx: ToolContext)``
that executes the call and returns a tool-result dict.

Implemented:

    - media (ffmpeg / Python): analyze_media, probe_media, edit_video,
      edit_audio, edit_image, color_grade, adjust_media, add_overlay,
      composite, transform_geometry, smart_reframe, extract_frame, mix_audio,
      align_audio, detect_beats, subtitle, animate_captions, paint_overlay,
      paint_mask_effect, arrange_timeline, export
    - provider media: generate_image, generate_video, generate_audio, narrate,
      plus check_job / wait_for_job for long-running jobs
    - host & files: web_search, web_open, fetch, run_shell, build, read_file,
      write_file, list_dir, file_* / move_file / copy_in / organize_files
    - timeline v1: get_timeline, inspect_timeline, render_preview,
      fine-grained timeline_* document verbs, project_export and
      project_export_otio / project_import_otio
    - LumenFrame composition: get_lumenframe, lumen_render, lumen_render_range,
      lumen_seek and the lumen_* layer / timing / mask verbs
    - shotlist: draft_shotlist, refine_shot, get_shotlist, set_shotlist,
      update_shot, assemble_shotlist
    - library & annotations: search_library, search_media, search_frames,
      annotate_media, get_media_annotations, write_media_annotation,
      get_safe_areas, inspect_lottie
    - agent support: recall_skills, save_skill, spawn_subtasks, log_note,
      remember, elicit

``len(DISPATCHER)`` is the authoritative tool count.

Dispatchers must NOT swallow errors. The agent loop wraps each call in
try/except and emits a ``tool_exec_error`` event on exception.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from lumeri.tools._context import (
    AssetRecord,
    AssetRegistry,
    ProgressCallback,
    ProgressUpdate,
    ToolContext,
)
from lumeri.tools._schema import TOOL_NAMES, TOOL_SCHEMAS

from lumeri.tools import add_overlay as _add_overlay
from lumeri.tools import adjust_media as _adjust_media
from lumeri.tools import align_audio as _align_audio
from lumeri.tools import detect_beats as _detect_beats
from lumeri.tools import analyze_media as _analyze_media
from lumeri.tools import animate_captions as _animate_captions
from lumeri.tools import arrange_timeline as _arrange_timeline
from lumeri.tools import assemble_shotlist as _assemble_shotlist
from lumeri.tools import build as _build
from lumeri.tools import color_grade as _color_grade
from lumeri.tools import composite as _composite
from lumeri.tools import edit_audio as _edit_audio
from lumeri.tools import edit_image as _edit_image
from lumeri.tools import edit_video as _edit_video
from lumeri.tools import elicit as _elicit
from lumeri.tools import export as _export
from lumeri.tools import extract_frame as _extract_frame
from lumeri.tools import fetch as _fetch
from lumeri.tools import files as _files
from lumeri.tools import generate_audio as _generate_audio
from lumeri.tools import generate_image as _generate_image
from lumeri.tools import generate_video as _generate_video
from lumeri.tools import inspect_timeline as _inspect_timeline
from lumeri.tools import layer as _layer
from lumeri.tools import lumen_render_range as _lumen_render_range
from lumeri.tools import lumen_seek as _lumen_seek
from lumeri.tools import lottie as _lottie
from lumeri.tools import log_note as _log_note
from lumeri.tools import media_annotations as _media_annotations
from lumeri.tools import mix_audio as _mix_audio
from lumeri.tools import narrate as _narrate
from lumeri.tools import paint as _paint
from lumeri.tools import probe_media as _probe_media
from lumeri.tools import remember as _remember
from lumeri.tools import run_shell as _run_shell
from lumeri.tools import safe_areas as _safe_areas
from lumeri.tools import save_skill as _save_skill
from lumeri.tools import search_library as _search_library
from lumeri.tools import search_media as _search_media
from lumeri.tools import draft_shotlist as _draft_shotlist
from lumeri.tools import refine_shot as _refine_shot
from lumeri.tools import search_frames as _search_frames
from lumeri.tools import shotlist as _shotlist
from lumeri.tools import smart_reframe as _smart_reframe
from lumeri.tools import subtitle as _subtitle
from lumeri.tools import timeline as _timeline
from lumeri.tools import transform_geometry as _transform_geometry
from lumeri.tools import web_search as _web_search

Dispatcher = Callable[[dict[str, Any], ToolContext], Awaitable[dict[str, Any]]]


async def _spawn_subtasks_dispatch(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Multi-agent fan-out verb. Imported lazily because ``lumeri.subtasks``
    imports ``DISPATCHER`` from THIS module (child dispatch reuses the shared
    table) — a top-level import here would be a cycle."""
    from lumeri import subtasks as _subtasks

    return await _subtasks.dispatch(args, ctx)


_STUB_REASONS: dict[str, str] = {}


def _make_stub(name: str) -> Dispatcher:
    reason = _STUB_REASONS.get(name, "not implemented yet")
    async def _stub(_args: dict[str, Any], _ctx: ToolContext) -> dict[str, Any]:
        raise NotImplementedError(f"tool '{name}' is not implemented: {reason}")

    _stub.__name__ = f"stub_{name}"
    return _stub


_REAL: dict[str, Dispatcher] = {
    "add_overlay":               _add_overlay.dispatch,
    "adjust_media":              _adjust_media.dispatch,
    "align_audio":               _align_audio.dispatch,
    "analyze_media":             _analyze_media.dispatch,
    "animate_captions":          _animate_captions.dispatch,
    "annotate_media":            _media_annotations.dispatch_annotate,
    "arrange_timeline":          _arrange_timeline.dispatch,
    "assemble_shotlist":         _assemble_shotlist.dispatch,
    "build":                     _build.dispatch,
    "check_job":                 _build.dispatch_check,
    "color_grade":               _color_grade.dispatch,
    "composite":                 _composite.dispatch,
    "copy_in":                   _files.dispatch_copy_in,
    "detect_beats":              _detect_beats.dispatch,
    "draft_shotlist":            _draft_shotlist.dispatch,
    "edit_audio":                _edit_audio.dispatch,
    "edit_image":                _edit_image.dispatch,
    "edit_video":                _edit_video.dispatch,
    "elicit":                    _elicit.dispatch,
    "export":                    _export.dispatch,
    "extract_frame":             _extract_frame.dispatch,
    "fetch":                     _fetch.dispatch,
    "file_copy":                 _files.dispatch_copy,
    "file_delete":               _files.dispatch_delete,
    "file_list":                 _files.dispatch_list,
    "file_move":                 _files.dispatch_move,
    "file_read":                 _files.dispatch_read,
    "file_write":                _files.dispatch_write,
    "generate_audio":            _generate_audio.dispatch,
    "generate_image":            _generate_image.dispatch,
    "generate_video":            _generate_video.dispatch,
    "get_lumenframe":            _layer.dispatch_get_lumenframe,
    "get_media_annotations":     _media_annotations.dispatch_get,
    "get_safe_areas":            _safe_areas.dispatch,
    "get_shotlist":              _shotlist.dispatch_get,
    "get_timeline":              _timeline.dispatch_get,
    "inspect_lottie":            _lottie.dispatch_inspect,
    "inspect_timeline":          _inspect_timeline.dispatch,
    "list_dir":                  _files.dispatch_list_dir,
    "log_note":                  _log_note.dispatch,
    "lumen_add_layer":           _layer.dispatch_lumen_add_layer,
    "lumen_delete_layer":        _layer.dispatch_lumen_delete_layer,
    "lumen_key":                 _layer.dispatch_lumen_key,
    "lumen_merge_compositions":  _layer.dispatch_lumen_merge_compositions,
    "lumen_move_layer":          _layer.dispatch_lumen_move_layer,
    "lumen_patch":               _layer.dispatch_lumen_patch,
    "lumen_render":              _layer.dispatch_lumen_render,
    "lumen_render_range":        _lumen_render_range.dispatch,
    "lumen_retime_segment":      _layer.dispatch_lumen_retime_segment,
    "lumen_reverse":             _layer.dispatch_lumen_reverse,
    "lumen_ripple_delete":       _layer.dispatch_lumen_ripple_delete,
    "lumen_seek":                _lumen_seek.dispatch,
    "lumen_select":              _layer.dispatch_lumen_select,
    "lumen_set_lane":            _layer.dispatch_lumen_set_lane,
    "lumen_set_mask":            _layer.dispatch_lumen_set_mask,
    "lumen_set_opacity":         _layer.dispatch_lumen_set_opacity,
    "lumen_set_range":           _layer.dispatch_lumen_set_range,
    "lumen_set_transform":       _layer.dispatch_lumen_set_transform,
    "lumen_set_visibility":      _layer.dispatch_lumen_set_visibility,
    "lumen_set_work_area":       _layer.dispatch_lumen_set_work_area,
    "lumen_speed_ramp":          _layer.dispatch_lumen_speed_ramp,
    "lumen_time_remap":          _layer.dispatch_lumen_time_remap,
    "mix_audio":                 _mix_audio.dispatch,
    "move_file":                 _files.dispatch_move_file,
    "narrate":                   _narrate.dispatch,
    "organize_files":            _files.dispatch_organize_files,
    "paint_mask_effect":         _paint.dispatch_mask_effect,
    "paint_overlay":             _paint.dispatch_overlay,
    "probe_media":               _probe_media.dispatch,
    "project_export":            _timeline.dispatch_project_export,
    "project_export_otio":       _timeline.dispatch_export_otio,
    "project_import_otio":       _timeline.dispatch_import_otio,
    "read_file":                 _files.dispatch_read_file,
    "recall_skills":             _save_skill.dispatch_recall_skills,
    "refine_shot":               _refine_shot.dispatch,
    "remember":                  _remember.dispatch,
    "render_preview":            _timeline.dispatch_render_preview,
    "run_shell":                 _run_shell.dispatch,
    "save_skill":                _save_skill.dispatch_save_skill,
    "search_frames":             _search_frames.dispatch,
    "search_library":            _search_library.dispatch,
    "search_media":              _search_media.dispatch,
    "set_shotlist":              _shotlist.dispatch_set,
    "smart_reframe":             _smart_reframe.dispatch,
    "spawn_subtasks":            _spawn_subtasks_dispatch,
    "subtitle":                  _subtitle.dispatch,
    "timeline_add_track":        _timeline.dispatch_add_track,
    "timeline_add_transition":   _timeline.dispatch_transition,
    "timeline_delete_clip":      _timeline.dispatch_delete,
    "timeline_insert_clip":      _timeline.dispatch_insert,
    "timeline_move_clip":        _timeline.dispatch_move,
    "timeline_set_clip_effects": _timeline.dispatch_effects,
    "timeline_set_clip_time":    _timeline.dispatch_set_time,
    "timeline_set_track":        _timeline.dispatch_set_track,
    "timeline_split_clip":       _timeline.dispatch_split,
    "timeline_trim_clip":        _timeline.dispatch_trim,
    "timeline_undo":             _timeline.dispatch_undo,
    "transform_geometry":        _transform_geometry.dispatch,
    "update_shot":               _shotlist.dispatch_update_shot,
    "wait_for_job":              _build.dispatch_wait,
    "web_open":                  _web_search.dispatch_open,
    "web_search":                _web_search.dispatch,
    "write_file":                _files.dispatch_write_file,
    "write_media_annotation":    _media_annotations.dispatch_write,
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
