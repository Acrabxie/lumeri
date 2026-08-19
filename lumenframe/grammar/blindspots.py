"""What measuring the alpha cannot see.

Reading the rendered alpha makes a description necessarily true — it reports
what was actually painted. That same property creates two blind spots, and both
fail *silently*, which is the dangerous kind:

**Discarded intent.** The renderer reads ``scale_x`` and ignores ``scale_y`` and
``anchor`` entirely (declared in :mod:`lumenframe.compile`'s module docstring).
Verified behaviourally: a layer at ``scale_y=0.5`` renders pixel-identical to
the same layer at ``scale_y=1.5``, so alpha cannot tell the two apart. The
description stays correct while the user's intent vanishes without a word —
precisely what Foundation principle IV forbids ("SHALL warn, never silently
drop").

**Clipping.** Alpha only exists inside the canvas. A layer running off the edge
measures as the clipped remainder, so its reported size is wrong and — verified,
not assumed — translating it no longer moves its measured box at all. Any edit
computed from that box will be wrong.

Neither is recoverable from pixels, so both are detected from the document and
the canvas bounds instead. Cheap, and they are what makes the alpha measurement
safe to rely on.

When the renderer gains real ``anchor`` / ``scale_y`` support, the first table
here must shrink to match. :func:`renderer_limitation_fields` exists so a drift
test can pin it to the renderer's declared limitations instead of letting it go
stale silently.
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "clipping_status",
    "collect",
    "discarded_intent",
    "renderer_limitation_fields",
]

#: Transform fields the renderer never reads, and what it does instead. Mirrors
#: the "Known limitations" declared in :mod:`lumenframe.compile`.
_IGNORED_TRANSFORM: dict[str, str] = {
    "scale_y": "non-uniform scale is not supported; the renderer collapses it to scale_x",
    "anchor_x": "anchor is not supported; the renderer always pivots on the content centre",
    "anchor_y": "anchor is not supported; the renderer always pivots on the content centre",
}

#: Keyframe property paths the renderer silently drops (absent from its
#: property map, so the whole track is discarded).
_IGNORED_KEYFRAME_PROPS: frozenset[str] = frozenset({"transform.scale_y", "scale_y"})

_ANCHOR_DEFAULT = 0.5
_EPS = 1e-9


def renderer_limitation_fields() -> frozenset[str]:
    """The transform fields currently known to be discarded by the renderer.

    Exposed for the drift test that pins this table to the renderer's own
    declared limitations: when the renderer starts honouring a field, this set
    must shrink in the same change, or the description keeps warning about a
    limitation that no longer exists.
    """
    return frozenset(_IGNORED_TRANSFORM)


def discarded_intent(layer: dict[str, Any]) -> list[dict[str, Any]]:
    """Fields this layer sets that the renderer will throw away.

    Only reports fields actually set away from their default, so a document that
    never touches ``anchor`` produces no noise.
    """
    out: list[dict[str, Any]] = []
    transform = layer.get("transform") or {}

    try:
        scale_x = float(transform.get("scale_x", 1.0))
        scale_y = float(transform.get("scale_y", 1.0))
    except (TypeError, ValueError):
        scale_x = scale_y = 1.0
    if abs(scale_y - scale_x) > _EPS:
        out.append({
            "field": "transform.scale_y",
            "set_to": scale_y,
            "effective": scale_x,
            "detail": _IGNORED_TRANSFORM["scale_y"],
        })

    for axis in ("anchor_x", "anchor_y"):
        try:
            value = float(transform.get(axis, _ANCHOR_DEFAULT))
        except (TypeError, ValueError):
            continue
        if abs(value - _ANCHOR_DEFAULT) > _EPS:
            out.append({
                "field": f"transform.{axis}",
                "set_to": value,
                "effective": _ANCHOR_DEFAULT,
                "detail": _IGNORED_TRANSFORM[axis],
            })

    for prop in (layer.get("keyframes") or {}):
        if str(prop) in _IGNORED_KEYFRAME_PROPS:
            out.append({
                "field": f"keyframes.{prop}",
                "set_to": "keyframe track",
                "effective": None,
                "detail": (
                    "this property is absent from the renderer's keyframe map, "
                    "so the whole track is silently discarded"
                ),
            })

    return out


def clipping_status(
    bbox: tuple[int, int, int, int] | None, width: int, height: int
) -> dict[str, Any] | None:
    """Whether a measured box touches the canvas edge, and what that costs.

    Touching an edge does not *prove* the layer is clipped — a layer can sit
    flush against it — but the measurement can no longer tell the two apart, so
    this is reported as suspected rather than asserted.
    """
    if bbox is None:
        return None
    x, y, w, h = bbox
    edges: list[str] = []
    if x <= 0:
        edges.append("left")
    if y <= 0:
        edges.append("top")
    if x + w >= width:
        edges.append("right")
    if y + h >= height:
        edges.append("bottom")
    if not edges:
        return None
    return {
        "suspected_clipped_edges": edges,
        "consequence": (
            "this layer touches the canvas edge, so the measured box may be the "
            "clipped remainder rather than its true extent; if it is genuinely "
            "off-canvas its size is unreliable and a move cannot be derived from "
            "the box displacement"
        ),
    }


def collect(
    doc: dict[str, Any], measurements: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """All blind-spot warnings for a measured document.

    An empty list means the checks ran and found nothing — it never means the
    checks were skipped.
    """
    canvas = doc.get("canvas") or {}
    width = int(canvas.get("width") or 1920)
    height = int(canvas.get("height") or 1080)

    by_id: dict[Any, dict[str, Any]] = {}
    for layer in (doc.get("root") or {}).get("children") or []:
        by_id[layer.get("id")] = layer

    warnings: list[dict[str, Any]] = []
    for m in measurements:
        layer = by_id.get(m.get("id"))
        if layer is not None:
            for item in discarded_intent(layer):
                warnings.append({
                    "layer_id": m.get("id"),
                    "layer_name": m.get("name"),
                    "kind": "discarded_intent",
                    **item,
                })
        clip = clipping_status(m.get("bbox"), width, height)
        if clip is not None:
            warnings.append({
                "layer_id": m.get("id"),
                "layer_name": m.get("name"),
                "kind": "suspected_clipping",
                **clip,
            })

    return warnings
