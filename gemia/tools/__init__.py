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
    - batch 3 (v4 build): fetch (host-side networking), run_shell (sandboxed
      bash via the M1 two-tier sandbox-exec boundary)

Still registered as stubs (raise NotImplementedError):

    - generate_video, generate_audio  — async LRO (Veo / Lyria), batch 2.2/2.3
    - search_library                  — needs embedding / index, batch 3

Schemas stay exposed so the model can see the full action vocabulary;
the host will not pretend to execute work it can't do.

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
from gemia.tools import analyze_media as _analyze_media
from gemia.tools import arrange_timeline as _arrange_timeline
from gemia.tools import color_grade as _color_grade
from gemia.tools import composite as _composite
from gemia.tools import edit_image as _edit_image
from gemia.tools import edit_video as _edit_video
from gemia.tools import export as _export
from gemia.tools import extract_frame as _extract_frame
from gemia.tools import fetch as _fetch
from gemia.tools import generate_image as _generate_image
from gemia.tools import mix_audio as _mix_audio
from gemia.tools import run_shell as _run_shell
from gemia.tools import transform_geometry as _transform_geometry

Dispatcher = Callable[[dict[str, Any], ToolContext], Awaitable[dict[str, Any]]]


_STUB_REASONS: dict[str, str] = {
    "generate_video": "needs Veo 3.1 LRO + JobRegistry (doc 08 async); deferred to batch 2.2",
    "generate_audio": "needs Lyria 3 LRO + JobRegistry (doc 08 async); deferred to batch 2.3",
    "search_library": "needs an asset library + embedding index; deferred to batch 3",
}


def _make_stub(name: str) -> Dispatcher:
    reason = _STUB_REASONS.get(name, "not implemented yet")
    async def _stub(_args: dict[str, Any], _ctx: ToolContext) -> dict[str, Any]:
        raise NotImplementedError(f"tool '{name}' is not implemented: {reason}")

    _stub.__name__ = f"stub_{name}"
    return _stub


_REAL: dict[str, Dispatcher] = {
    "analyze_media":      _analyze_media.dispatch,
    "edit_video":         _edit_video.dispatch,
    "color_grade":        _color_grade.dispatch,
    "add_overlay":        _add_overlay.dispatch,
    "export":             _export.dispatch,
    "composite":          _composite.dispatch,
    "arrange_timeline":   _arrange_timeline.dispatch,
    "mix_audio":          _mix_audio.dispatch,
    "transform_geometry": _transform_geometry.dispatch,
    "edit_image":         _edit_image.dispatch,
    "extract_frame":      _extract_frame.dispatch,
    "generate_image":     _generate_image.dispatch,
    "fetch":              _fetch.dispatch,
    "run_shell":          _run_shell.dispatch,
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
