"""Turn a vague spatial phrase into a concrete, inspectable edit proposal.

"Move it left a bit" is unexecutable until "a bit" has a number. This module
resolves the phrase against the grid vocabulary and returns a LayerPatch
**proposal** — it never writes. Application goes through
:func:`lumenframe.ops.apply_layer_patch`, so the document has exactly one way to
change.

Every proposal carries ``reasoning``: the starting value, the step used, and
where the step came from. Without it the caller is back to trusting an
unexplained number, which is the problem this layer exists to remove.
"""
from __future__ import annotations

from typing import Any

from lumenframe.grammar import vocabulary

__all__ = ["PhraseError", "propose"]

#: Direction words -> (transform axis, sign). Screen coordinates, so "up" is -y.
_DIRECTIONS: dict[str, tuple[str, float]] = {
    "左": ("x", -1.0), "left": ("x", -1.0),
    "右": ("x", 1.0), "right": ("x", 1.0),
    "上": ("y", -1.0), "up": ("y", -1.0),
    "下": ("y", 1.0), "down": ("y", 1.0),
}

#: Magnitude words -> vocabulary step name.
_MAGNITUDES: dict[str, str] = {
    "一点点": "slight", "一点": "slight", "a bit": "slight", "slightly": "slight",
    "一些": "moderate", "some": "moderate",
    "很多": "large", "a lot": "large", "much": "large",
}

#: Scale words -> direction (grow / shrink).
_SCALE_WORDS: dict[str, float] = {
    "放大": 1.0, "变大": 1.0, "bigger": 1.0, "larger": 1.0, "scale up": 1.0,
    "缩小": -1.0, "变小": -1.0, "smaller": -1.0, "scale down": -1.0,
}


class PhraseError(ValueError):
    """Raised when a phrase carries no resolvable spatial intent."""


def _magnitude(phrase: str) -> str:
    """Step name for the phrase; an unqualified move reads as the default nudge."""
    for word, magnitude in _MAGNITUDES.items():
        if word in phrase:
            return magnitude
    return "slight"


def propose(
    phrase: str,
    *,
    layer_id: str,
    current_transform: dict[str, Any] | None,
    canvas_width: int,
) -> dict[str, Any]:
    """Resolve ``phrase`` into a LayerPatch proposal for ``layer_id``.

    Args:
        phrase: A spatial instruction, Chinese or English.
        layer_id: The layer the instruction is about.
        current_transform: That layer's transform, used as the starting value.
        canvas_width: Canvas width, so steps stay canvas-relative.

    Returns:
        ``{"patch": <LayerPatch>, "reasoning": {...}, "applied": False}``. The
        patch is shaped for :func:`lumenframe.ops.apply_layer_patch` and is
        **not** applied here.

    Raises:
        PhraseError: if no direction or scale intent is present. Guessing an
            edit out of an unparseable phrase would be worse than refusing.
    """
    transform = current_transform or {}
    text = phrase.strip().lower()
    magnitude = _magnitude(text)

    for word, sign in _SCALE_WORDS.items():
        if word in text:
            factor = vocabulary.SIZE_STEPS[magnitude]
            if sign < 0:
                factor = round(1.0 / factor, 6)
            current = float(transform.get("scale_x", 1.0) or 1.0)
            target = round(current * factor, 6)
            # ``scale`` is the op's convenience key: it sets both axes at once,
            # which also keeps the edit inside what the renderer honours today.
            return _proposal(
                {"op": "set_transform", "layer_id": layer_id, "scale": target},
                {
                    "phrase": phrase,
                    "intent": "scale",
                    "magnitude": magnitude,
                    "from_scale": current,
                    "factor": factor,
                    "to_scale": target,
                    "step_source": f"SIZE_STEPS[{magnitude}]",
                },
            )

    for word, (axis, direction) in _DIRECTIONS.items():
        if word in text:
            step = vocabulary.nudge_px(magnitude, canvas_width)
            current = float(transform.get(axis, 0.0) or 0.0)
            target = round(current + direction * step, 3)
            return _proposal(
                {"op": "set_transform", "layer_id": layer_id, axis: target},
                {
                    "phrase": phrase,
                    "intent": "move",
                    "axis": axis,
                    "magnitude": magnitude,
                    "step_px": step,
                    "step_source": (
                        f"canvas_width x NUDGE_STEPS[{magnitude}]"
                        f"={vocabulary.NUDGE_STEPS[magnitude]}"
                    ),
                    "from": current,
                    "to": target,
                },
            )

    raise PhraseError(
        f"no spatial intent found in {phrase!r}; expected a direction "
        f"({'/'.join(sorted(_DIRECTIONS))}) or a scale word "
        f"({'/'.join(sorted(_SCALE_WORDS))})"
    )


def _proposal(op: dict[str, Any], reasoning: dict[str, Any]) -> dict[str, Any]:
    return {
        "patch": {"version": 1, "ops": [op]},
        "reasoning": reasoning,
        "applied": False,
    }
