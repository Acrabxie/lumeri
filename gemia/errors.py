"""Unified exception hierarchy for Gemia.

All public-facing errors should be one of these subclasses so that
server.py can produce consistent {error_code, user_message, detail} JSON.
"""
from __future__ import annotations

from typing import Any


class GemiaError(Exception):
    """Base class for all Gemia errors."""

    code: str = "E_GEMIA"

    def __init__(self, user_message: str, *, detail: str = "") -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail or user_message

    def to_payload(self) -> dict[str, Any]:
        """Structured form surfaced to the model and the SSE stream.

        The agent loop uses this so a raised error reaches the model with
        its ``error_code`` and ``detail`` intact instead of being flattened
        into a bare ``"TypeName: message"`` string.
        """
        payload: dict[str, Any] = {"error": self.user_message, "error_code": self.code}
        if self.detail and self.detail != self.user_message:
            payload["detail"] = self.detail
        return payload


class ConfigError(GemiaError):
    """API key missing, invalid, or config cannot be read."""
    code = "E_CONFIG"


class AIServiceError(GemiaError):
    """Remote AI service (Gemini / OpenRouter / Veo) request failed."""
    code = "E_AI"


class MediaProcessingError(GemiaError):
    """ffmpeg, cv2, or codec-level processing failure."""
    code = "E_MEDIA"


class PlanExecutionError(GemiaError):
    """PlanEngine step execution failed."""
    code = "E_PLAN"

    def __init__(self, user_message: str, *, step_id: str = "", detail: str = "") -> None:
        super().__init__(user_message, detail=detail)
        self.step_id = step_id


class UserInputError(GemiaError):
    """Invalid user input: unsupported file format, file not found, etc."""
    code = "E_INPUT"


class TaskCancelledError(GemiaError):
    """Task was cancelled by the user."""
    code = "E_CANCELLED"


# Recovery vocabulary. Both the model (to pick its next move) and the agent
# loop's circuit breaker (to tell self-debugging apart from flailing) read
# this field, so keep it a small, stable closed set.
RECOVERY_FIX_ARGS = "fix_args"            # same tool, corrected arguments
RECOVERY_SWITCH_TOOL = "switch_tool"      # this capability can't do it; use another
RECOVERY_TRANSIENT_RETRY = "transient_retry"  # flaky failure; the same call may work
RECOVERY_NONE = "none"                    # not recoverable in-turn; tell the user

_RECOVERY_VALUES = frozenset(
    {RECOVERY_FIX_ARGS, RECOVERY_SWITCH_TOOL, RECOVERY_TRANSIENT_RETRY, RECOVERY_NONE}
)


class ToolError(GemiaError):
    """A creative-action (verb) dispatch failed in a way the model can act on.

    Unlike a bare ``ValueError``, a ToolError carries:

      - ``code`` — machine-readable class of failure (E_UNSUPPORTED, E_BAD_ARG,
        E_NOT_FOUND, E_NOT_IMPLEMENTED, E_TRANSIENT, E_BUDGET).
      - ``recovery`` — what kind of next move makes sense (see RECOVERY_*).
      - ``valid_options`` / ``hint`` — concrete material for an accurate fix,
        e.g. the list of looks ``color_grade`` actually supports.

    The agent loop surfaces every field both in the model-facing tool_result
    and the ``tool_exec_error`` SSE event. This is the fuel that lets the model
    self-correct precisely instead of guessing, and lets the host show the
    user a real "caught it → fixed it" arc — without the host ever faking a
    result or verifying on the model's behalf.
    """

    code = "E_TOOL"

    def __init__(
        self,
        user_message: str,
        *,
        code: str = "E_TOOL",
        recovery: str = RECOVERY_FIX_ARGS,
        valid_options: list[Any] | None = None,
        hint: str = "",
        detail: str = "",
    ) -> None:
        super().__init__(user_message, detail=detail)
        self.code = code
        self.recovery = recovery if recovery in _RECOVERY_VALUES else RECOVERY_FIX_ARGS
        self.valid_options = list(valid_options) if valid_options else None
        self.hint = hint or ""

    def to_payload(self) -> dict[str, Any]:
        payload = super().to_payload()
        payload["recovery"] = self.recovery
        if self.valid_options:
            payload["valid_options"] = self.valid_options
        if self.hint:
            payload["hint"] = self.hint
        return payload
