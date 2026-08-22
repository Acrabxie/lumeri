"""Human feedback loop — creative phrases become semantic deltas.

The adjustment contract every point library shares: feedback **edits the brief**
and the result re-derives with the same seed. We never patch the output file — a
"more cinematic" grade is a *re-derived* grade, not a nudged LUT.

Phrases are ``more/less X`` (or 更/再/少一点 X); each adjective maps to axis
deltas. Unknown phrases are returned, not raised — the agent decides whether to
ask or ignore. Generalises :mod:`lumenframe.vector.feedback`: the parser is
shared; a library supplies its own adjective→delta table (extending the base).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from lumenframe.craft.lexicon import (
    ANCHORS, DEFAULT_STEP_DOC, Interpretation, axis_glossary, interpret,
)
from lumenframe.craft.params import AxisSpace, clamp01

#: What a move of a given size tends to amount to. This is domain calibration
#: — an observation about the axes themselves — not a reading of anyone's
#: wording, which is the distinction that matters: the first is knowable from
#: in here, the second never is.
CALIBRATION: dict[str, Any] = {
    "anchor_move": [0.15, 0.25],
    "note": (
        "A named anchor ('premium', '暖') is calibrated to move an axis by "
        "0.15–0.25. Below ~0.05 the change will not read on screen; above ~0.4 "
        "it reads as a different look rather than an adjustment. These bound "
        "the axis, not the user's intent — 'a bit' and 'much' are yours to size."
    ),
}

#: The adjust path's base table *is* the create path's table — one lexicon,
#: so "cinematic" cannot mean elegance-first in a brief and drama-first in
#: feedback the way it used to. Domain words still layer on via ``extend``.
BASE_ADJUSTMENTS: dict[str, dict[str, float]] = ANCHORS


@dataclass
class FeedbackVocab:
    """A library's adjective→delta table over its axis space."""

    space: AxisSpace
    #: Defaults to the space's *own* feeling table (shared anchors + whatever
    #: domain words the library registered), so a word a library taught the
    #: create path is understood by the adjust path for free. Passing a table
    #: explicitly still overrides, and ``extend`` layers adjust-only words.
    adjustments: dict[str, dict[str, float]] | None = None

    def __post_init__(self) -> None:
        if self.adjustments is None:
            self.adjustments = {k: dict(v) for k, v in self.space.feelings.items()}

    def extend(self, extra: Mapping[str, dict[str, float]]) -> "FeedbackVocab":
        self.adjustments.update({k: dict(v) for k, v in extra.items()})
        return self

    def vocabulary(self) -> list[str]:
        """Recognised adjectives that actually move a declared axis."""
        axes = set(self.space.axes)
        return sorted(w for w, d in self.adjustments.items() if set(d) & axes)

    def _lookup(self, phrase: str) -> Interpretation | None:
        """Read a phrase into an :class:`Interpretation`, or None.

        A reading only counts once it lands on a word this vocabulary really
        holds, so peeling can never invent a meaning.
        """
        return interpret(phrase, self.adjustments)

    def parse(self, phrases: list[str]) -> tuple[dict[str, float], list[str]]:
        """Phrases → accumulated axis deltas + unrecognised phrases.

        Accepts "more playful", "much less chaotic", bare adjectives ("premium"
        == "more premium"), and Chinese equivalents (更暖 / 少一点戏剧). A phrase
        whose adjective is known but touches no declared axis is treated as
        unrecognised for *this* library (so the caller can honestly report it).
        """
        axes = set(self.space.axes)
        deltas: dict[str, float] = {}
        unknown: list[str] = []
        for phrase in phrases or []:
            if not str(phrase).strip():
                continue
            hit = self._lookup(phrase)
            if hit is None or not set(hit.nudges) & axes:
                unknown.append(str(phrase))
                continue
            for axis, delta in hit.nudges.items():
                if axis in axes:
                    deltas[axis] = deltas.get(axis, 0.0) + delta * hit.magnitude
        return deltas, unknown

    def read(self, phrases: list[str]) -> tuple[list[Interpretation], list[str]]:
        """Interpretations for ``phrases``, plus the ones nothing could read."""
        axes = set(self.space.axes)
        readings: list[Interpretation] = []
        unknown: list[str] = []
        for phrase in phrases or []:
            if not str(phrase).strip():
                continue
            hit = self._lookup(phrase)
            if hit is None or not set(hit.nudges) & axes:
                unknown.append(str(phrase))
            else:
                readings.append(hit)
        return readings, unknown

    def propose(
        self,
        brief: dict[str, Any],
        phrases: list[str],
        resolve_axes: "callable",
    ) -> dict[str, Any]:
        """Read the *direction* of each phrase and hand the degree back.

        Wording fixes which way to move; it never fixes how far. How far depends
        on where the axis already sits, what the user pushed back on earlier,
        and what the footage looks like — none of which is visible from in here.
        So this resolves everything it honestly can and stops: current values,
        which axis each phrase points at, which way, and how big a move tends to
        register in this domain. The agent picks the number and calls back with
        ``params``.
        """
        interpretations, unknown = self.read(phrases)
        current = resolve_axes(brief)
        axes = set(self.space.axes)
        readings: list[dict[str, Any]] = []
        for hit in interpretations:
            targets = {}
            for axis, delta in hit.nudges.items():
                if axis in axes:
                    value = round(current.axis(axis), 4)
                    up = (delta * hit.magnitude) > 0
                    targets[axis] = {
                        "current": value,
                        "direction": "up" if up else "down",
                        "headroom": round((1.0 - value) if up else value, 4),
                    }
            if targets:
                readings.append({
                    "phrase": hit.phrase,
                    "read_as": hit.word,
                    "means": "more" if hit.magnitude > 0 else "less",
                    "degree_word": hit.degree or None,
                    "targets": targets,
                })
        return {
            "needs": "degree",
            "readings": readings,
            "unknown": unknown,
            "axes": {a: round(current.axis(a), 4) for a in self.space.axes},
            "axis_meanings": axis_glossary(self.space.axes),
            "calibration": CALIBRATION,
            "how_to_answer": (
                "Decide how far each axis should move — from these current "
                "values, the user's wording, and what they have already pushed "
                "back on — then call op:'adjust' again with the same brief plus "
                "params:{axis: absolute 0..1}. Keep the original phrase in "
                "feedback so the reply can quote what the user actually said."
            ),
        }

    def guidance(self) -> dict[str, Any]:
        """What to tell the agent when a phrase could not be read.

        Rather than a bare "unknown", hand back the calibration anchors this
        domain responds to *and* what its axes mean — enough for the agent to
        re-express the user's own words as anchors or as direct ``params``,
        instead of silently proceeding with defaults.
        """
        return {
            "anchors": self.vocabulary(),
            "axes": axis_glossary(self.space.axes),
            "how_to_use": (
                "Translate the user's wording into these anchors (optionally "
                "'more X' / 'less X'), or set params axis values in 0..1 "
                "directly. Say which reading you chose."
            ),
        }

    def apply(
        self,
        brief: dict[str, Any],
        phrases: list[str],
        resolve_axes: "callable",
        params: Mapping[str, float] | None = None,
    ) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
        """Fold an agent-chosen degree into a NEW brief's ``params``.

        ``params`` are absolute 0..1 axis values the agent decided on after
        seeing the current state — this method does not size the move itself.
        ``phrases`` are kept for the record: they say what the user asked for,
        so the readings can quote the original wording next to what was done.

        Returns ``(new_brief, unknown, readings)``. The original brief is never
        mutated. ``resolve_axes(brief) -> ResolvedAxes`` supplies the "before"
        values the readings report against.
        """
        interpretations, unknown = self.read(phrases)
        current = resolve_axes(brief)
        axes = set(self.space.axes)

        bad = set(params or {}) - axes
        if bad:
            raise ValueError(f"unknown axes {sorted(bad)} (use {self.space.axes})")

        new_brief = {**brief, "params": dict(brief.get("params") or {})}
        for axis, value in (params or {}).items():
            new_brief["params"][axis] = round(clamp01(float(value)), 4)

        touched = set(params or {})
        readings: list[dict[str, Any]] = []
        for hit in interpretations:
            moved = {}
            for axis in hit.nudges:
                if axis in axes and axis in touched:
                    before = round(current.axis(axis), 4)
                    after = new_brief["params"][axis]
                    moved[axis] = {"from": before, "to": after,
                                   "delta": round(after - before, 4)}
            readings.append({
                "phrase": hit.phrase,
                "read_as": hit.word,
                "direction": "more" if hit.magnitude > 0 else "less",
                "degree_word": hit.degree or None,
                "axes": moved,
                "degree_set_by": "agent",
            })

        # Axes the agent moved that no phrase accounts for — reported anyway, so
        # nothing changes without showing up in the record.
        accounted = {a for r in readings for a in r["axes"]}
        for axis in sorted(touched - accounted):
            before = round(current.axis(axis), 4)
            readings.append({
                "phrase": None, "read_as": None, "direction": None,
                "degree_word": None, "degree_set_by": "agent",
                "axes": {axis: {"from": before,
                                "to": new_brief["params"][axis],
                                "delta": round(new_brief["params"][axis] - before, 4)}},
            })
        return new_brief, unknown, readings


def unread_note(phrases: "list[str] | tuple[str, ...]", vocab: "FeedbackVocab") -> str:
    """The note to emit for wording the lexicon could not read.

    Deliberately *not* phrased as "ignored". A word we could not read is a
    request we have not answered, and the honest move is to say so and hand
    back enough to answer it: this domain's anchors and what its axes mean. The
    agent re-expresses the user's words and says which reading it chose —
    quietly proceeding on defaults is what made vague briefs feel unheard.
    """
    g = vocab.guidance()
    anchors = g["anchors"]
    sample = ", ".join(anchors[:12])
    axes = "; ".join(f"{a} — {m}" for a, m in g["axes"].items())
    return (
        f"could not read: {', '.join(str(p) for p in phrases)} — nothing was "
        f"applied for these. This domain hears: {sample}"
        f"{f', … ({len(anchors)} anchors, op=\'catalog\' for all)' if len(anchors) > 12 else ''}. "
        f"Axes: {axes}. Re-express the user's wording as anchors "
        f"(optionally 'more X' / 'less X') or set params values in 0..1 "
        f"directly, then tell the user which reading you used — do not proceed "
        f"on defaults as if the wording had been understood."
    )


def reading_note(readings: list[dict[str, Any]]) -> str | None:
    """State plainly what each phrase was taken to mean and what moved.

    The degree came from the agent, so this records a decision rather than
    disclosing a guess — but it still has to be said out loud, because the
    user's "a bit" is the thing being interpreted and they are entitled to see
    the interpretation.
    """
    if not readings:
        return None
    parts = []
    for r in readings:
        moves = ", ".join(
            f"{axis} {m['from']}→{m['to']}" for axis, m in (r.get("axes") or {}).items()
        )
        if not moves:
            continue
        if r.get("phrase"):
            parts.append(f"{r['phrase']!r} → {r['direction']} {r['read_as']}: {moves}")
        else:
            parts.append(moves)
    if not parts:
        return None
    return (
        "read as — " + "; ".join(parts)
        + ". Tell the user which reading you used; if it overshot or "
        "undershot, adjust again with a different params value."
    )
