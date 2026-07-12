"""Shared deterministic text primitives for Lumeri product surfaces."""

from gemia.text.layout import (
    TextLayoutError,
    TextLayoutResult,
    TextMetrics,
    autofit_text,
    break_lines,
    measure_text,
    wrap_text,
)

__all__ = [
    "TextLayoutError",
    "TextLayoutResult",
    "TextMetrics",
    "autofit_text",
    "break_lines",
    "measure_text",
    "wrap_text",
]
