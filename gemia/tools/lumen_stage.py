"""lumen_stage: describe where everything is, without spending a render on it.

``lumen_seek`` renders a frame so the model can *look*. Looking is not enough to
edit: a picture shows that the title sits on the subject's face, but it cannot
say which layer to move or by how much. This verb answers the other half —
every layer's real box, the region it occupies, what it covers and what covers
it — keyed by layer id, so an observation becomes an edit.

It also resolves a spatial phrase ("move it left a bit") into a LayerPatch
proposal with the arithmetic shown, so "a bit" stops being a guess.

ADD-ONLY and read-only: this module never edits the document. The proposal it
returns is handed back to the caller, who applies it through the normal patch
verb — one write path, no side door.
"""
from __future__ import annotations

from typing import Any

from gemia.tools import layer as _layer
from gemia.tools._context import ToolContext

try:
    from lumenframe.grammar import PhraseError, propose, render_text, stage_report
except ImportError:  # pragma: no cover - graceful fallback if lumenframe missing
    stage_report = None  # type: ignore[assignment]


async def dispatch(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Describe the spatial layout of the current lumenframe document.

    Reports, for a single moment, every layer's measured position and size, the
    thirds-grid region it falls in, which regions it actually paints into, its
    safe-area status, and its real overlaps with other layers. Overlap is
    measured from actual alpha coverage, so a hollow or mostly-transparent layer
    is not reported as covering what sits inside its bounding box.

    Optionally resolves a spatial instruction into an edit proposal.

    Args:
        seconds: The moment to describe, in seconds. Defaults to 0.
        detail: ``brief`` | ``normal`` | ``full``. ``full`` adds the resolved
            grid vocabulary (cut lines, safe-area margins, step sizes).
        phrase: Optional spatial instruction such as "往左一点" / "move it left
            a bit" / "放大一些". Requires ``layer_id``.
        layer_id: The layer ``phrase`` applies to.

    Returns:
        On success: ``{applied: False, report, text, proposal?}``. ``report`` is
        the structured description, ``text`` its compact rendering, and
        ``proposal`` (when a phrase was given) is
        ``{patch, reasoning, applied: False}`` — a LayerPatch to be applied via
        the patch verb, never applied here.

        Two fields deserve attention rather than being skimmed past:
        ``report.warnings`` lists things the measurement structurally cannot see
        (a ``scale_y`` or ``anchor`` the renderer discards, a layer running off
        canvas whose size is therefore unreliable), and ``report.degradations``
        lists layers that could not be measured at all. Both are empty only when
        the checks ran and found nothing.

        On failure: ``{applied: False, error_code, error_message}``.
    """
    if stage_report is None:
        return {
            "applied": False,
            "error_code": "E_NOT_AVAILABLE",
            "error_message": "lumenframe.grammar module not available",
        }

    doc = _layer._lumendoc(ctx)
    if not doc:
        return {
            "applied": False,
            "error_code": "E_NO_DOC",
            "error_message": "lumen_stage: no lumenframe document in this session",
        }

    try:
        seconds = float(args.get("seconds") or 0.0)
    except (TypeError, ValueError):
        return {
            "applied": False,
            "error_code": "E_ARG",
            "error_message": f"lumen_stage: 'seconds' must be a number, got {args.get('seconds')!r}",
        }

    detail = str(args.get("detail") or "normal")
    try:
        report = stage_report(doc, seconds, detail=detail)
    except ValueError as exc:
        return {"applied": False, "error_code": "E_ARG", "error_message": str(exc)}

    result: dict[str, Any] = {
        "applied": False,
        "report": report,
        "text": render_text(report),
    }

    phrase = args.get("phrase")
    if phrase:
        layer_id = args.get("layer_id")
        if not layer_id:
            return {
                "applied": False,
                "error_code": "E_ARG",
                "error_message": "lumen_stage: 'phrase' requires 'layer_id'",
            }
        target = next((L for L in report["layers"] if L["id"] == layer_id), None)
        if target is None:
            known = ", ".join(f"{L['name']}({L['id']})" for L in report["layers"])
            return {
                "applied": False,
                "error_code": "E_LAYER",
                "error_message": f"lumen_stage: no layer {layer_id!r}; have: {known}",
            }
        raw = next(
            (L for L in (doc.get("root") or {}).get("children") or []
             if L.get("id") == layer_id),
            {},
        )
        try:
            result["proposal"] = propose(
                str(phrase),
                layer_id=str(layer_id),
                current_transform=raw.get("transform"),
                canvas_width=report["canvas"]["width"],
            )
        except PhraseError as exc:
            return {"applied": False, "error_code": "E_PHRASE", "error_message": str(exc)}

    return result
