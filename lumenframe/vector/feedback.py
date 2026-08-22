"""Human feedback loop — creative phrases become semantic deltas.

The adjustment contract: feedback **edits the brief**, the scene re-derives
with the same seed. We never patch SVG text — a "more playful" scene is a
*re-choreographed* scene, not a tweaked file.

Phrases are ``more/less X`` (or 更/再/少一点 X); each adjective maps to axis
deltas. Unknown phrases are returned, not raised — the agent decides whether
to ask or ignore.
"""
from __future__ import annotations

from typing import Any

from lumenframe.craft.feedback import CALIBRATION
from lumenframe.craft.lexicon import axis_glossary
from lumenframe.vector.params import (
    FEELINGS, SEMANTIC_AXES, clamp01, lookup_feeling,
)

#: The adjust table *is* the brief table — see
#: :data:`lumenframe.vector.params.FEELINGS`. They used to be two hand-kept
#: copies that disagreed ("cinematic" existed in one and not the other).
ADJUSTMENTS: dict[str, dict[str, float]] = FEELINGS

def parse_feedback(phrases: list[str]) -> tuple[dict[str, float], list[str]]:
    """Feedback phrases → accumulated axis deltas + unrecognised phrases.

    Accepts "more playful", "much less chaotic", bare adjectives ("premium"
    == "more premium"), and the Chinese equivalents (更俏皮 / 少一点乱).
    """
    deltas: dict[str, float] = {}
    unknown: list[str] = []
    for phrase in phrases or []:
        if not str(phrase).strip():
            continue
        hit = lookup_feeling(phrase)
        if hit is None:
            unknown.append(str(phrase))
            continue
        sign, table = hit.magnitude, hit.nudges
        for axis, delta in table.items():
            if axis in SEMANTIC_AXES:
                deltas[axis] = deltas.get(axis, 0.0) + delta * sign
    return deltas, unknown


def propose_feedback(brief: dict[str, Any], phrases: list[str]) -> dict[str, Any]:
    """Read the direction of each phrase and hand the degree back to the agent.

    Vector's counterpart to :meth:`lumenframe.craft.feedback.FeedbackVocab.propose`
    — wording fixes which way to move, never how far.
    """
    from lumenframe.vector.styles import resolve_params

    current = resolve_params(
        style=brief.get("style"),
        feelings=list(brief.get("feeling") or []),
        overrides=dict(brief.get("params") or {}),
    )
    readings, unknown = [], []
    for phrase in phrases or []:
        if not str(phrase).strip():
            continue
        hit = lookup_feeling(phrase)
        if hit is None or not set(hit.nudges) & set(SEMANTIC_AXES):
            unknown.append(str(phrase))
            continue
        targets = {}
        for axis, delta in hit.nudges.items():
            if axis in SEMANTIC_AXES:
                value = round(current.axes[axis], 4)
                up = (delta * hit.magnitude) > 0
                targets[axis] = {
                    "current": value,
                    "direction": "up" if up else "down",
                    "headroom": round((1.0 - value) if up else value, 4),
                }
        readings.append({
            "phrase": hit.phrase, "read_as": hit.word,
            "means": "more" if hit.magnitude > 0 else "less",
            "degree_word": hit.degree or None, "targets": targets,
        })
    return {
        "needs": "degree",
        "readings": readings,
        "unknown": unknown,
        "axes": {a: round(current.axes[a], 4) for a in SEMANTIC_AXES},
        "axis_meanings": axis_glossary(SEMANTIC_AXES),
        "calibration": CALIBRATION,
        "how_to_answer": (
            "Decide how far each axis should move — from these current values, "
            "the user's wording, and what they have already pushed back on — "
            "then call op:'adjust' again with the same brief plus "
            "params:{axis: absolute 0..1}. Keep the original phrase in feedback "
            "so the reply can quote what the user actually said."
        ),
    }


def apply_feedback(
    brief: dict[str, Any], phrases: list[str],
    params: "dict[str, float] | None" = None,
) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    """A new brief with feedback folded into ``params`` overrides.

    Returns ``(new_brief, unknown, readings)``. ``readings`` spells out how each
    phrase was read and which axis moved from what to what — step sizes come
    from wording alone, so they are defaults the agent should override with
    explicit ``params`` whenever the conversation says otherwise.

    The returned brief carries absolute axis values in ``params`` (current
    resolved axes + deltas, clamped) so repeated adjustments accumulate
    predictably. The original brief is not mutated.
    """
    from lumenframe.vector.styles import resolve_params

    interpretations: list = []
    unknown: list[str] = []
    for phrase in phrases or []:
        if not str(phrase).strip():
            continue
        hit = lookup_feeling(phrase)
        if hit is None or not set(hit.nudges) & set(SEMANTIC_AXES):
            unknown.append(str(phrase))
        else:
            interpretations.append(hit)

    current = resolve_params(
        style=brief.get("style"),
        feelings=list(brief.get("feeling") or []),
        overrides=dict(brief.get("params") or {}),
    )
    bad = set(params or {}) - set(SEMANTIC_AXES)
    if bad:
        raise ValueError(f"unknown semantic axes: {sorted(bad)} (use {SEMANTIC_AXES})")
    new_brief = {**brief, "params": dict(brief.get("params") or {})}
    for axis, value in (params or {}).items():
        new_brief["params"][axis] = round(clamp01(float(value)), 4)
    touched = set(params or {})

    readings: list[dict[str, Any]] = []
    for hit in interpretations:
        moved = {}
        for axis in hit.nudges:
            if axis in SEMANTIC_AXES and axis in touched:
                before = round(current.axes[axis], 4)
                after = new_brief["params"][axis]
                moved[axis] = {"from": before, "to": after,
                               "delta": round(after - before, 4)}
        readings.append({
            "phrase": hit.phrase, "read_as": hit.word,
            "direction": "more" if hit.magnitude > 0 else "less",
            "degree_word": hit.degree or None, "axes": moved,
            "degree_set_by": "agent",
        })
    accounted = {a for r in readings for a in r["axes"]}
    for axis in sorted(touched - accounted):
        before = round(current.axes[axis], 4)
        readings.append({
            "phrase": None, "read_as": None, "direction": None,
            "degree_word": None, "degree_set_by": "agent",
            "axes": {axis: {"from": before, "to": new_brief["params"][axis],
                            "delta": round(new_brief["params"][axis] - before, 4)}},
        })
    return new_brief, unknown, readings


def feedback_vocabulary() -> list[str]:
    """The recognised adjectives (agent-facing catalog)."""
    return sorted(w for w, d in ADJUSTMENTS.items() if set(d) & set(SEMANTIC_AXES))


def feedback_guidance() -> dict[str, Any]:
    """Anchors + axis meanings to hand back when a phrase could not be read."""
    return {
        "anchors": feedback_vocabulary(),
        "axes": axis_glossary(SEMANTIC_AXES),
        "how_to_use": (
            "Translate the user's wording into these anchors (optionally "
            "'more X' / 'less X'), or set params axis values in 0..1 directly. "
            "Say which reading you chose."
        ),
    }
