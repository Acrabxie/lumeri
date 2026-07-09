"""deck_* verbs: the model's read/write interface to the deck IR.

Deck-driven authoring mirrors the shotlist flow (a deck is "structured,
interactive video" — see docs/deck-interactive-video-plan.md):

    1. The model turns the user's topic into a slide plan and calls
       ``set_deck`` (or scaffolds one with ``draft_deck``) — slides carrying
       semantic content blocks (text/stat/image/shape/group), speaker notes,
       build states, and interaction links.
    2. Per slide it refines wording/blocks/timing with ``update_slide``.
    3. Later phases materialize the deck (assemble_deck) — nothing renders
       here; drafting and revising the plan is free.

The deck lives inside project_state and every mutation flows through
``ctx.project.apply_ops`` — versioned, auditable, and undoable via
``timeline_undo`` exactly like timeline and shotlist edits. Mutations return
a compact text view of the post-state so the model does not need a
follow-up read. The "deck-op" label keeps deck patches distinguishable from
timeline ones (the hook for a future on_patch state_scope field).
"""
from __future__ import annotations

from typing import Any

from gemia.tools._context import ToolContext

DECK_OP_LABEL = "deck-op"


def _project(ctx: ToolContext):
    if ctx.project is None:
        raise ValueError(
            "deck verbs need a project-backed session (ctx.project is None)"
        )
    return ctx.project


def _blocks_summary(blocks: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for block in blocks:
        if isinstance(block, dict):
            kind = str(block.get("kind") or "?")
            counts[kind] = counts.get(kind, 0) + 1
    return "+".join(
        f"{kind}×{n}" if n > 1 else kind for kind, n in counts.items()
    ) or "empty"


def _slide_line(slide: dict[str, Any]) -> str:
    builds = [b for b in slide.get("builds") or [] if isinstance(b, dict)]
    dwell = sum(float(b.get("dwell_sec") or 0) for b in builds)
    bits = [
        f"  [{slide.get('id')}]",
        str(slide.get("layout") or "content"),
        _blocks_summary(slide.get("blocks") or []),
        f"{len(builds)} build ~{dwell:.1f}s",
    ]
    line = " ".join(bits)
    title = str(slide.get("title") or "").strip()
    if title:
        line += f' — "{title}"'
    notes = str(slide.get("notes") or "").strip()
    if notes:
        line += f'  vo:"{notes[:60]}{"…" if len(notes) > 60 else ""}"'
    for link in slide.get("links") or []:
        if isinstance(link, dict) and link.get("trigger") != "advance":
            line += f"  ⇢{link.get('trigger')}→{link.get('target')}"
    transition = slide.get("transition") or {}
    if isinstance(transition, dict) and transition.get("kind") not in (None, "cut"):
        line += f"  ⇥{transition['kind']}"
    return line


def render_deck_text(deck: dict[str, Any]) -> str:
    """One-screen human/model-readable view of the deck."""
    deck = deck or {}
    slides = deck.get("slides") or []
    if not slides:
        return "(deck empty — call draft_deck or set_deck to plan slides)"
    theme = deck.get("theme") or {}
    head_bits = []
    if theme.get("mood"):
        head_bits.append(f"mood: {theme['mood']}")
    if theme.get("aspect"):
        head_bits.append(f"aspect: {theme['aspect']}")
    lines = [" | ".join(head_bits)] if head_bits else []
    dwell = 0.0
    for slide in slides:
        if not isinstance(slide, dict):
            continue
        lines.append(_slide_line(slide))
        dwell += sum(
            float(b.get("dwell_sec") or 0)
            for b in slide.get("builds") or []
            if isinstance(b, dict)
        )
    path = deck.get("default_path") or []
    lines.append(f"— {len(slides)} slides, ~{dwell:.1f}s dwell, path {'→'.join(path)}")
    return "\n".join(lines)


def _summary(ctx: ToolContext, result: dict[str, Any], **extra: Any) -> dict[str, Any]:
    deck = (ctx.project.load() if ctx.project else {}).get("deck") or {}
    out = {
        "applied": True,
        "seq": result.get("patch_seq_end"),
        "slide_count": len(deck.get("slides") or []),
        "deck": render_deck_text(deck),
    }
    out.update(extra)
    return out


# ── write ───────────────────────────────────────────────────────────────
async def dispatch_set(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    deck = args.get("deck")
    if not isinstance(deck, dict):
        raise ValueError(
            "set_deck requires a 'deck' object with slides[] and default_path"
        )
    project = _project(ctx)
    result = project.apply_ops(
        [{"op": "set_deck", "deck": deck}], label=DECK_OP_LABEL
    )
    return _summary(ctx, result)


async def dispatch_update_slide(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    slide_id = str(args.get("slide_id") or "")
    if not slide_id:
        raise ValueError("update_slide requires 'slide_id'")
    fields = args.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise ValueError("update_slide requires a non-empty 'fields' object")
    project = _project(ctx)
    result = project.apply_ops(
        [{"op": "update_slide", "slide_id": slide_id, "fields": fields}],
        label=DECK_OP_LABEL,
    )
    return _summary(ctx, result, updated_slide=slide_id)


# ── read ────────────────────────────────────────────────────────────────
async def dispatch_get(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    deck = _project(ctx).load().get("deck") or {}
    return {
        "slide_count": len(deck.get("slides") or []),
        "deck_text": render_deck_text(deck),
        "deck": deck,
    }


__all__ = ["dispatch_set", "dispatch_update_slide", "dispatch_get", "render_deck_text"]
