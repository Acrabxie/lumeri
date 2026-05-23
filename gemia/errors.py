"""Unified exception hierarchy for Gemia.

All public-facing errors should be one of these subclasses so that
server.py can produce consistent {error_code, user_message, detail} JSON.
"""
from __future__ import annotations


class GemiaError(Exception):
    """Base class for all Gemia errors."""

    code: str = "E_GEMIA"

    def __init__(self, user_message: str, *, detail: str = "") -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail or user_message


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
