"""The quantised bridge between language and numbers.

This is what makes "move it left a bit" executable. Without a vocabulary, "a
bit" has no landing point: the model can only guess a pixel value, render, look,
and guess again.

The lattice itself is **not** redefined here. It comes from
:data:`lumenframe.compose.framing.GRIDS`, the existing composition-grid registry,
so placement maths and this description can never silently diverge — one grid
vocabulary, one source of truth.

Every token emitted is a **language-neutral machine token**. Translation belongs
to the presentation layer; nothing here is user-facing display text.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lumenframe.compose.framing import grid_for

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np

__all__ = [
    "GRID_KIND",
    "NUDGE_STEPS",
    "REGIONS",
    "SAFE_AREAS",
    "SIZE_STEPS",
    "describe_vocabulary",
    "nudge_px",
    "region_for_point",
    "regions_covered",
    "safe_area_status",
]

#: Video frames are composed on the rule of thirds, and thirds is what people
#: reach for when describing a shot out loud. A 12-column layout grid is a
#: print/screen-design idea and reads as static for moving pictures.
GRID_KIND = "thirds"

_ROW_TOKENS = ("upper", "mid", "lower")
_COL_TOKENS = ("left", "center", "right")

#: The nine region tokens, row-major from the top-left.
REGIONS: tuple[str, ...] = tuple(
    f"{row}_{col}" for row in _ROW_TOKENS for col in _COL_TOKENS
)


def _cut_lines() -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Normalised (vertical, horizontal) cut lines from the shared grid registry."""
    spec = grid_for(GRID_KIND)
    return spec.v, spec.h


def region_for_point(x: float, y: float, width: int, height: int) -> str:
    """Region token containing the pixel point ``(x, y)``.

    Points outside the canvas clamp to the nearest region rather than raising —
    an off-screen layer still has a meaningful "which way did it go".
    """
    v, h = _cut_lines()
    nx = x / width if width else 0.0
    ny = y / height if height else 0.0
    col = 0 if nx < v[0] else (1 if nx < v[-1] else 2)
    row = 0 if ny < h[0] else (1 if ny < h[-1] else 2)
    return f"{_ROW_TOKENS[row]}_{_COL_TOKENS[col]}"


def regions_covered(coverage: "np.ndarray | None") -> list[str]:
    """Every region token the layer actually paints into, row-major.

    Driven by the coverage mask, never the bounding box. An outlined frame's box
    spans the whole canvas while its pixels only touch a thin border; reporting
    the box would tell the model the frame occupies every cell — the same
    bounding-box lie that is rejected for occlusion. The two must agree, or the
    description contradicts itself.
    """
    if coverage is None:
        return []
    v, h = _cut_lines()
    rows, cols = coverage.shape
    v_bounds = (0.0, *v, 1.0)
    h_bounds = (0.0, *h, 1.0)

    out: list[str] = []
    for row_i, row in enumerate(_ROW_TOKENS):
        y0 = int(rows * h_bounds[row_i])
        y1 = int(rows * h_bounds[row_i + 1])
        for col_i, col in enumerate(_COL_TOKENS):
            x0 = int(cols * v_bounds[col_i])
            x1 = int(cols * v_bounds[col_i + 1])
            if coverage[y0:y1, x0:x1].any():
                out.append(f"{row}_{col}")
    return out


#: Fraction of the canvas kept inside each safe area (per side, symmetric),
#: following the standard broadcast 90% / 93% conventions.
#:
#: NOTE: ``gemia/tools/safe_areas.py`` holds richer per-platform presets. Those
#: live above this package in the dependency order, so importing them here would
#: invert the layering. Moving that data down into lumenframe is planned as its
#: own behaviour-preserving step; until then this is a deliberate narrow subset,
#: not a second competing definition.
SAFE_AREAS: dict[str, float] = {
    "title": 0.90,
    "action": 0.93,
}


def safe_area_status(
    bbox: tuple[int, int, int, int], width: int, height: int
) -> dict[str, Any]:
    """Classify a bbox against the canvas edge and the named safe areas."""
    x, y, w, h = bbox
    x1, y1 = x + w, y + h

    if x1 <= 0 or y1 <= 0 or x >= width or y >= height:
        status = "offscreen"
    elif x < 0 or y < 0 or x1 > width or y1 > height:
        status = "bleeding"
    elif x == 0 or y == 0 or x1 == width or y1 == height:
        status = "touching_edge"
    else:
        status = "inside"

    violated: list[str] = []
    for name, keep in SAFE_AREAS.items():
        margin_x = width * (1.0 - keep) / 2.0
        margin_y = height * (1.0 - keep) / 2.0
        if x < margin_x or y < margin_y or x1 > width - margin_x or y1 > height - margin_y:
            violated.append(name)

    return {"status": status, "violated_zones": violated}


#: How far "a bit" moves, as a fraction of canvas width — canvas-relative so the
#: same phrase behaves consistently at 1080p and 4K.
#:
#: First-pass values, chosen to be visible without being violent. The point of
#: this table is that the number is *declared and inspectable*, not that it is
#: already perfect; expect to tune it against real editing feel.
NUDGE_STEPS: dict[str, float] = {
    "slight": 0.03,
    "moderate": 0.08,
    "large": 0.18,
}

#: Multiplicative steps for "make it bigger / smaller".
SIZE_STEPS: dict[str, float] = {
    "slight": 1.10,
    "moderate": 1.25,
    "large": 1.60,
}


def nudge_px(magnitude: str, width: int) -> float:
    """Pixel distance for a named nudge magnitude on a canvas ``width`` wide."""
    if magnitude not in NUDGE_STEPS:
        raise ValueError(
            f"unknown nudge magnitude {magnitude!r}; "
            f"expected one of {sorted(NUDGE_STEPS)}"
        )
    return round(width * NUDGE_STEPS[magnitude], 3)


def describe_vocabulary(width: int, height: int) -> dict[str, Any]:
    """The full vocabulary resolved against a concrete canvas.

    Emitted alongside a stage report so a reader can always see what "a bit" was
    taken to mean, instead of having to trust an unexplained number.
    """
    v, h = _cut_lines()
    return {
        "grid_kind": GRID_KIND,
        "regions": list(REGIONS),
        "cut_lines": {
            "vertical_px": [round(width * value, 3) for value in v],
            "horizontal_px": [round(height * value, 3) for value in h],
        },
        "safe_areas": {
            name: {
                "keep_fraction": keep,
                "margin_x_px": round(width * (1.0 - keep) / 2.0, 3),
                "margin_y_px": round(height * (1.0 - keep) / 2.0, 3),
            }
            for name, keep in SAFE_AREAS.items()
        },
        "nudge_steps_px": {name: nudge_px(name, width) for name in NUDGE_STEPS},
        "size_steps": dict(SIZE_STEPS),
    }
