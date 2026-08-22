"""``edit_grammar`` — the cut-grammar engine as ONE agent tool.

Single tool, op discriminator (``create`` | ``adjust`` | ``catalog``), mirroring
``vector_motion``. Pure: it imports nothing from gemia and touches no live
session — ``create``/``adjust`` return the cut plan as plain dicts, and a thin
gemia adapter (a separate layer) is what would lower the plan onto a real
timeline via :func:`lumenframe.edit.render.plan_to_timeline_ops`.

* ``op:"create"``  — a clip sequence brief → a reasoned cut plan.
* ``op:"adjust"``  — feedback phrases ("more seamless", "更快") → the brief is
  re-edited and the plan re-derived with the same seed.
* ``op:"catalog"`` — the cut-grammar vocabulary to compose briefs from.
"""
from __future__ import annotations

from typing import Any

from lumenframe.craft import err, ok, with_state, tool_dispatch
from lumenframe.craft.styles import StyleError
from lumenframe.edit.api import EditBriefError, adjust_cut_plan, build_cut_plan
from lumenframe.edit.catalog import edit_catalog

TOOL = "edit_grammar"

# Every structurally-bad brief must leave the tool as a uniform E_ARG, never as a
# raw exception. EditBriefError covers the brief-shape checks; StyleError covers an
# unknown style/alias; ValueError/TypeError catch any residual coercion slip
# (e.g. an unknown axis override) so nothing escapes the op:create|adjust surface.
_ARG_ERRORS = (EditBriefError, StyleError, ValueError, TypeError)


def _create(args: dict[str, Any]) -> dict[str, Any]:
    brief = args.get("brief")
    if not isinstance(brief, dict):
        return err("E_ARG", f"{TOOL} create: 'brief' must be an object "
                            "(see op:'catalog' for the vocabulary)")
    try:
        result = build_cut_plan(brief)
    except _ARG_ERRORS as exc:
        return err("E_ARG", f"{TOOL} create: {exc}", recovery="fix_args")
    return with_state(ok(cut_plan=result["cut_plan"], plan=result["plan"], notes=result["notes"],
              next="lower with edit.render.plan_to_timeline_ops; "
                   "adjust with op:'adjust' + feedback phrases"), result)


def _adjust(args: dict[str, Any]) -> dict[str, Any]:
    brief = args.get("brief")
    feedback = args.get("feedback")
    if not isinstance(brief, dict):
        return err("E_ARG", f"{TOOL} adjust: 'brief' must be the object to re-edit")
    if not isinstance(feedback, list) or not feedback:
        return err("E_ARG", f"{TOOL} adjust: 'feedback' must be a non-empty list of "
                            "phrases like 'more seamless' / '更快'")
    try:
        result = adjust_cut_plan(
            brief, [str(p) for p in feedback], args.get("params"))
    except _ARG_ERRORS as exc:
        return err("E_ARG", f"{TOOL} adjust: {exc}", recovery="fix_args")
    if result.get("needs") == "degree":
        # Direction is settled; the size of the move is the agent's to decide,
        # from the current values and the conversation. Nothing was applied.
        return {"applied": False, **result,
                "next": "choose how far, then call op:'adjust' again with the "
                        "same brief plus params:{axis: 0..1}"}

    return with_state(ok(cut_plan=result["cut_plan"], plan=result["plan"], notes=result["notes"],
              brief=result["brief"],
              next="lower with edit.render.plan_to_timeline_ops to verify the new feel"), result)


async def dispatch(args: dict[str, Any], ctx: Any = None) -> dict[str, Any]:
    """Route one ``edit_grammar`` call by ``op`` (pure; ``ctx`` unused)."""
    return tool_dispatch(
        args, tool=TOOL, catalog_fn=edit_catalog, create=_create, adjust=_adjust)
