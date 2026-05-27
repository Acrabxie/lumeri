"""Lumeri v3 verb dispatch table.

15 creative-action tools the model can call. ``TOOL_SCHEMAS`` is the
function-calling schema list sent to Gemini. ``DISPATCHER`` maps a verb
name to an async coroutine ``async def(args: dict, ctx: ToolContext)``
that executes the call and returns a tool-result dict.

In milestone 1 only these verbs are implemented:

    - analyze_media
    - edit_video
    - color_grade
    - add_overlay
    - export

The remaining 10 verbs are registered but raise ``NotImplementedError``
when invoked. Schemas stay exposed so the model can see the full action
vocabulary; the host will not pretend to execute work it can't do.

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
from gemia.tools import color_grade as _color_grade
from gemia.tools import edit_video as _edit_video
from gemia.tools import export as _export

Dispatcher = Callable[[dict[str, Any], ToolContext], Awaitable[dict[str, Any]]]


def _make_stub(name: str) -> Dispatcher:
    async def _stub(_args: dict[str, Any], _ctx: ToolContext) -> dict[str, Any]:
        raise NotImplementedError(
            f"tool '{name}' is not implemented in milestone 1; "
            f"only analyze_media, edit_video, color_grade, add_overlay, export are wired up."
        )

    _stub.__name__ = f"stub_{name}"
    return _stub


_REAL: dict[str, Dispatcher] = {
    "analyze_media": _analyze_media.dispatch,
    "edit_video":    _edit_video.dispatch,
    "color_grade":   _color_grade.dispatch,
    "add_overlay":   _add_overlay.dispatch,
    "export":        _export.dispatch,
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
