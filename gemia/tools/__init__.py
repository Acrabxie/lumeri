"""Lumeri v3 verb dispatch table.

15 creative-action tools the model can call. ``TOOL_SCHEMAS`` is the
function-calling schema list sent to Gemini. ``DISPATCHER`` maps a verb
name to an async coroutine that executes the call and returns a tool
result dict.

In milestone 1 only the following verbs are implemented:

    - analyze_media
    - edit_video
    - color_grade
    - add_overlay
    - export

The remaining 10 verbs are registered but raise ``NotImplementedError``
when invoked. This is intentional: the schemas are exposed to the model so
it can see the full action vocabulary, but the host will not pretend to
execute work it can't yet do.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from gemia.tools._schema import TOOL_NAMES, TOOL_SCHEMAS

Dispatcher = Callable[..., Awaitable[dict[str, Any]]]


def _make_stub(name: str) -> Dispatcher:
    async def _stub(**_kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError(
            f"tool '{name}' is not implemented in milestone 1; "
            f"only analyze_media, edit_video, color_grade, add_overlay, export are wired up."
        )

    _stub.__name__ = f"stub_{name}"
    return _stub


DISPATCHER: dict[str, Dispatcher] = {name: _make_stub(name) for name in TOOL_NAMES}


__all__ = ["TOOL_SCHEMAS", "TOOL_NAMES", "DISPATCHER", "Dispatcher"]
