"""The shared creative lexicon: spoken forms, one table, honest unknowns.

These lock the three defects that made vague direction feel unheard:

1. Chinese speech attaches its degree markers *after* the adjective
   (「快一点」) and its direction words *mid-sentence* (「节奏再快一点」). A
   prefix-only matcher read none of them, so the most ordinary phrasing in the
   product's primary language fell through to "unrecognised".
2. The brief path and the adjust path kept separate tables that drifted, so
   "cinematic" pushed one way when creating and another when adjusting.
3. Unreadable wording was reported as "ignored" and the run continued on
   defaults — the model looked confident while having understood nothing.
"""
from __future__ import annotations

import pytest

from lumenframe.craft.feedback import FeedbackVocab, unread_note
from lumenframe.craft.lexicon import ANCHORS, candidates
from lumenframe.craft.params import axis_space

ALL_AXES = (
    "energy", "elegance", "smoothness", "complexity", "density", "drama",
    "warmth", "playfulness", "contrast", "saturation", "lift", "filmic",
    "weight", "pace", "tension", "balance", "negative_space", "tightness",
    "drift",
)


def _read(phrase: str) -> tuple[float, str] | None:
    """First reading of ``phrase`` that lands on a real anchor."""
    for reading in candidates(phrase):
        if reading.word in ANCHORS:
            return reading.magnitude, reading.word
    return None


# ── 1. spoken Chinese, the way people actually type it ────────────────────


@pytest.mark.parametrize(
    "phrase,expected_word,positive",
    [
        # trailing degree markers — the whole class that used to fail
        ("快一点", "快", True),
        ("慢一点", "慢", True),
        ("高级一点", "高级", True),
        ("再干净一点", "干净", True),
        ("色调再暖一点", "暖", True),
        ("大气点", "大气", True),
        ("有质感一点", "质感", True),
        ("稍微暖一些", "暖", True),
        # direction word mid-sentence, adjective after
        ("节奏再快一点", "快", True),
        ("更高级一点", "高级", True),
        # direction word trailing, adjective before
        ("留白多一点", "留白", True),
        ("戏剧少一点", "戏剧", False),
        ("张力再强些", "张力", True),
        # negation reads as *less*, never as *more*
        ("别那么花", "花", False),
        ("别太密", "密", False),
        ("不冷", "冷", False),
        ("少一点戏剧", "戏剧", False),
        # degree complements after a noun-ish core
        ("氛围感强一点", "氛围", True),
        ("张力弱一些", "张力", False),
        ("戏剧性强点", "戏剧", True),
        ("质感不够", "质感", False),
        ("饱和度低一点", "饱和", False),
        ("对比度高一点", "对比", True),
        # English shapes
        ("warmer", "warm", True),
        ("much more cinematic", "cinematic", True),
        ("make it feel more premium", "premium", True),
        ("too busy", "busy", False),
        ("a bit less dramatic", "dramatic", False),
    ],
)
def test_spoken_forms_resolve_with_the_right_direction(phrase, expected_word, positive):
    reading = _read(phrase)
    assert reading is not None, f"{phrase!r} should be readable"
    magnitude, word = reading
    assert word == expected_word
    assert (magnitude > 0) is positive, f"{phrase!r} read in the wrong direction"


@pytest.mark.parametrize(
    "phrase",
    ["灵感", "感觉", "一点", "很强", "感性", "态度", "难度高", "banana", "赛博朋克风"],
)
def test_peeling_never_invents_a_meaning(phrase):
    """Trimming is only ever an attempt — 「灵感」must not be read as 「灵」."""
    assert _read(phrase) is None


def test_hedged_phrases_stay_a_real_but_small_move():
    magnitude, word = _read("稍微暖一些")
    assert word == "暖"
    assert 0 < magnitude < 1.0


# ── 2. one table for both paths ───────────────────────────────────────────

LIBRARIES = ("grade", "kinetic", "edit", "camera", "compose", "rhythm")


def _library_pair(name: str):
    """(space, feedback vocab) for a point library."""
    import importlib

    params = importlib.import_module(f"lumenframe.{name}.params")
    space = getattr(params, "SPACE", None) or getattr(params, "COMPOSE_SPACE")
    for attr in ("FEEDBACK", "feedback_vocab", "kinetic_feedback", "edit_feedback",
                 "compose_feedback", "rhythm_vocab", "grade_feedback"):
        vocab = getattr(params, attr, None)
        if vocab is None:
            continue
        return space, (vocab if isinstance(vocab, FeedbackVocab) else vocab())
    raise AssertionError(f"no feedback vocab found for {name}")


@pytest.mark.parametrize("name", LIBRARIES)
def test_adjust_vocabulary_covers_every_brief_word(name):
    """A word the brief path understands must not be a stranger to adjust."""
    space, vocab = _library_pair(name)
    missing = sorted(set(space.vocabulary()) - set(vocab.adjustments))
    assert not missing, f"{name}: adjust path cannot read brief words {missing}"


@pytest.mark.parametrize("name", LIBRARIES)
def test_shared_words_agree_on_direction(name):
    """The drift that made 「更电影感」steer sideways instead of deeper."""
    space, vocab = _library_pair(name)
    axes = set(space.axes)
    for word, brief_nudges in space.feelings.items():
        adjust_nudges = vocab.adjustments.get(word)
        if adjust_nudges is None:
            continue
        for axis in set(brief_nudges) & set(adjust_nudges) & axes:
            a, b = brief_nudges[axis], adjust_nudges[axis]
            assert a * b > 0, (
                f"{name}: {word!r} moves {axis} by {a} when creating and {b} "
                f"when adjusting — the two paths disagree"
            )


def test_vector_brief_and_adjust_share_one_table():
    from lumenframe.vector import feedback as vfeedback
    from lumenframe.vector import params as vparams

    assert vfeedback.ADJUSTMENTS is vparams.FEELINGS


def test_cinematic_means_the_same_thing_in_both_paths():
    space = axis_space(ALL_AXES)
    vocab = FeedbackVocab(space=space)
    brief = space.lookup("cinematic")
    adjust = vocab._lookup("more cinematic")
    assert brief is not None and adjust is not None
    assert brief.nudges == adjust.nudges


# ── 3. unknown wording asks for help instead of proceeding ────────────────


def test_unread_note_hands_back_anchors_and_axis_meanings():
    space = axis_space(("energy", "elegance", "warmth"))
    vocab = FeedbackVocab(space=space)
    note = unread_note(["赛博朋克", "vaporwave"], vocab)

    assert "赛博朋克" in note and "vaporwave" in note
    assert "ignored" not in note.lower(), "must not tell the model to move on"
    assert "energy" in note and "warmth" in note      # axis meanings offered
    assert "premium" in note or "warm" in note        # anchors offered
    assert "params" in note                           # the direct escape hatch


def test_guidance_lists_only_axes_the_domain_declares():
    space = axis_space(("energy", "warmth"))
    guidance = FeedbackVocab(space=space).guidance()
    assert set(guidance["axes"]) == {"energy", "warmth"}
    assert guidance["anchors"]


def test_unreadable_phrases_are_reported_not_silently_dropped():
    space = axis_space(("energy", "elegance"))
    deltas, unknown = FeedbackVocab(space=space).parse(["更高级", "赛博朋克"])
    assert deltas, "the readable phrase still applies"
    assert unknown == ["赛博朋克"]


# ── 4. the agent can actually see enough to read a degree ─────────────────
#
# A lookup table can match "一点" but never measure it: how much "a bit warmer"
# means depends on where warmth already sits and what the user said two turns
# ago. Only the agent sees that. So the engine's job is to expose the state and
# be explicit about its own guess — if these fields fall off the wire (they used
# to, filtered out by the tool layer's whitelist), the agent is blind again and
# is back to shouting phrases and hoping.

import asyncio


TOOLS = [
    ("lumenframe.grade.tool", {"look": "film", "seed": 7}, ["再暖一点"]),
    ("lumenframe.camera.tool", {"move": "push_in", "seed": 7}, ["slower"]),
    ("lumenframe.rhythm.tool", {"bpm": 120, "seed": 7}, ["tighter"]),
]


@pytest.mark.parametrize("module,brief,feedback", TOOLS)
def test_tool_replies_expose_current_axis_values(module, brief, feedback):
    import importlib

    dispatch = importlib.import_module(module).dispatch
    reply = asyncio.run(dispatch({"op": "create", "brief": brief}))
    assert reply.get("applied") is not False, reply
    axes = reply.get("axes")
    assert isinstance(axes, dict) and axes, f"{module}: create hides its axis values"
    assert all(0.0 <= v <= 1.0 for v in axes.values())


@pytest.mark.parametrize("module,brief,feedback", TOOLS)
def test_adjust_hands_the_degree_back_instead_of_guessing(module, brief, feedback):
    """Wording fixes direction; it never fixes distance.

    So `adjust` with only a phrase applies nothing. It resolves what it honestly
    can — current values, which axis, which way, how much of a move registers —
    and stops there. Deciding "a bit" is the agent's job, because only the agent
    can see where the value sits relative to what this user has been asking for.
    """
    import importlib

    dispatch = importlib.import_module(module).dispatch
    reply = asyncio.run(
        dispatch({"op": "adjust", "brief": brief, "feedback": feedback})
    )
    assert reply["applied"] is False
    assert reply["needs"] == "degree"
    assert reply["axes"], "the agent cannot size a move without the current values"
    assert reply["axis_meanings"], "nor without knowing what the axes mean"
    assert reply["calibration"]["anchor_move"]

    reading = reply["readings"][0]
    assert reading["phrase"] == feedback[0]
    assert reading["means"] in ("more", "less")
    target = next(iter(reading["targets"].values()))
    assert target["direction"] in ("up", "down")
    assert 0.0 <= target["current"] <= 1.0
    assert "headroom" in target
    # nothing here pretends to be a size
    assert "step" not in reading


@pytest.mark.parametrize("module,brief,feedback", TOOLS)
def test_adjust_applies_the_degree_the_agent_chose(module, brief, feedback):
    """With params, the engine executes exactly that and reports what moved."""
    import importlib

    dispatch = importlib.import_module(module).dispatch
    proposal = asyncio.run(
        dispatch({"op": "adjust", "brief": brief, "feedback": feedback})
    )
    axis = next(iter(next(iter(proposal["readings"]))["targets"]))
    chosen = 0.77

    reply = asyncio.run(dispatch({
        "op": "adjust", "brief": brief, "feedback": feedback,
        "params": {axis: chosen},
    }))
    assert reply["applied"] is True
    assert reply["brief"]["params"][axis] == pytest.approx(chosen)

    reading = next(r for r in reply["readings"] if axis in (r.get("axes") or {}))
    assert reading["degree_set_by"] == "agent"
    move = reading["axes"][axis]
    assert move["to"] == pytest.approx(chosen)
    assert {"from", "to", "delta"} <= set(move)
    assert reply["axes"][axis] == pytest.approx(chosen)


@pytest.mark.parametrize("module,brief,feedback", TOOLS)
def test_adjust_rejects_axes_the_domain_does_not_have(module, brief, feedback):
    import importlib

    dispatch = importlib.import_module(module).dispatch
    reply = asyncio.run(dispatch({
        "op": "adjust", "brief": brief, "feedback": feedback,
        "params": {"not_an_axis": 0.5},
    }))
    assert reply["applied"] is False
    assert reply.get("error_code") == "E_ARG"
