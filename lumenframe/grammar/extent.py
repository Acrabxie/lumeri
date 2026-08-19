"""Where each layer actually lands on the canvas, measured from its alpha.

The obvious way to answer "how big is this layer" is to read the document. That
does not work: content resolvers hand back canvas-sized frames with the content
drawn into them (text is centred onto a full-canvas transparent frame), so the
document knows a text layer's font size but not the pixel box its glyphs occupy.
Asset layers are worse — the size lives in the file.

So this module measures the rendered alpha instead. Rendering one layer in
isolation and taking its alpha channel gives, in one step:

* the true content box, including glyph metrics and asset dimensions, with no
  per-type extent solver to write and keep in sync with the renderer;
* real coverage for occlusion, rather than a bounding-box guess that reports a
  mostly-transparent layer as "covering" a face (measured: a hollow frame's box
  claims 100% coverage of a title its pixels never touch);
* a description that is necessarily the *renderer's* truth, which is what
  Foundation principle IV asks for — a layer whose ``anchor`` the renderer
  ignores is described where it actually lands, not where the maths says.

The cost is a render per layer, so this is **not** a pixel-free path and is not
claimed to be one.

Two things alpha structurally cannot see — fields the renderer discarded, and
anything outside the canvas — are detected from the document instead, in
:mod:`lumenframe.grammar.blindspots`. Never report an extent without also
running those checks; alone, this module is silently incomplete.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lumenframe import model, timebase
from lumenframe.compile import _lane_ordered_children
from lumenframe.preview import preview_frames

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np

__all__ = [
    "ALPHA_THRESHOLD",
    "COVERAGE_DOWNSAMPLE",
    "canvas_ratio",
    "coverage_ratio",
    "frame_for_time",
    "measure_layers",
]

#: Alpha above this counts as painted. Anti-aliased edges fall below it.
ALPHA_THRESHOLD = 0.01

#: Downsample factor for the retained coverage mask. Occlusion ratios do not
#: need pixel precision and a 1/4 mask is 16x cheaper to intersect. Reported to
#: the caller as ``occlusion_method`` so the estimate is never mistaken for exact.
COVERAGE_DOWNSAMPLE = 4

#: Layer types whose solo render cannot represent their real contribution:
#: an adjustment layer acts on what is *below* it, and a null never renders.
_CONTEXT_DEPENDENT = frozenset({"adjustment", "null"})


def frame_for_time(doc: dict[str, Any], seconds: float) -> int:
    """Frame index for ``seconds``, quantised the way the compiler does it."""
    fps = float((doc.get("canvas") or {}).get("fps") or 30.0)
    return int(timebase.to_frame(seconds, fps))


def _alpha_bbox(alpha: "np.ndarray") -> tuple[int, int, int, int] | None:
    """Tight ``(x, y, w, h)`` box of alpha above threshold, or ``None`` if empty."""
    import numpy as np

    rows = np.any(alpha > ALPHA_THRESHOLD, axis=1)
    cols = np.any(alpha > ALPHA_THRESHOLD, axis=0)
    if not rows.any() or not cols.any():
        return None
    y0 = int(np.argmax(rows))
    y1 = int(len(rows) - np.argmax(rows[::-1]) - 1)
    x0 = int(np.argmax(cols))
    x1 = int(len(cols) - np.argmax(cols[::-1]) - 1)
    return (x0, y0, x1 - x0 + 1, y1 - y0 + 1)


def _solo_doc(doc: dict[str, Any], layer: dict[str, Any]) -> dict[str, Any]:
    """A copy of ``doc`` containing only ``layer``, on the same canvas."""
    root = {**(doc.get("root") or {}), "children": [layer]}
    return model.normalize_doc({**doc, "root": root})


def measure_layers(
    doc: dict[str, Any],
    seconds: float,
    *,
    resolver: Any = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Measure every top-level layer of ``doc`` at ``seconds``.

    Layers are returned in the compositor's own order (``_lane_ordered_children``,
    lane-aware) so ``z`` reads directly as "who is on top" and the list is stable
    enough to diff between calls.

    Args:
        doc: A lumenframe document.
        seconds: The moment to measure, quantised to a frame.
        resolver: Optional content resolver forwarded to the render.

    Returns:
        ``(measurements, degradations)``. Each measurement carries the layer's
        identity, its measured ``bbox`` (``None`` when the layer paints nothing
        at this time), and a downsampled boolean ``coverage`` mask shared by
        occlusion and region-span so the two can never disagree.

        Degradations are recorded rather than swallowed: a layer that could not
        be measured says so, and why.
    """
    canvas = doc.get("canvas") or {}
    frame_idx = frame_for_time(doc, seconds)

    measurements: list[dict[str, Any]] = []
    degradations: list[dict[str, Any]] = []

    children = _lane_ordered_children((doc.get("root") or {}).get("children") or [])
    for z, layer in enumerate(children):
        entry: dict[str, Any] = {
            "id": layer.get("id"),
            "name": layer.get("name"),
            "type": layer.get("type"),
            "z": z,
            "bbox": None,
            "coverage": None,
            "extent_source": "alpha_raster",
            "opacity": layer.get("opacity", 1.0),
        }

        if layer.get("type") in _CONTEXT_DEPENDENT:
            entry["extent_source"] = "unavailable"
            degradations.append({
                "layer_id": layer.get("id"),
                "kind": "context_dependent_layer",
                "detail": (
                    f"{layer.get('type')} layers act on other layers, so a solo "
                    "render cannot represent their contribution; extent not measured"
                ),
            })
            measurements.append(entry)
            continue

        try:
            _, rgba = preview_frames(
                _solo_doc(doc, layer), [frame_idx], resolver=resolver
            )[0]
        except Exception as exc:  # noqa: BLE001 - a degradation must stay visible
            entry["extent_source"] = "unavailable"
            degradations.append({
                "layer_id": layer.get("id"),
                "kind": "render_failed",
                "detail": f"{type(exc).__name__}: {exc}",
            })
            measurements.append(entry)
            continue

        alpha = rgba[:, :, 3]
        entry["bbox"] = _alpha_bbox(alpha)
        if entry["bbox"] is None:
            entry["extent_source"] = "empty_at_time"
        else:
            step = COVERAGE_DOWNSAMPLE
            entry["coverage"] = alpha[::step, ::step] > ALPHA_THRESHOLD
        measurements.append(entry)

    return measurements, degradations


def coverage_ratio(a: "np.ndarray | None", b: "np.ndarray | None") -> float:
    """Fraction of ``a``'s painted pixels that ``b`` also paints.

    Asymmetric on purpose: "how much of the title is behind the subject" and
    "how much of the subject is behind the title" are different questions, and a
    caller describing a frame wants both.
    """
    if a is None or b is None:
        return 0.0
    a_area = int(a.sum())
    if a_area == 0:
        return 0.0
    return round(float((a & b).sum()) / a_area, 4)


def canvas_ratio(coverage: "np.ndarray | None") -> float:
    """Fraction of the whole canvas this layer actually paints."""
    if coverage is None:
        return 0.0
    return round(float(coverage.sum()) / float(coverage.size), 4)
