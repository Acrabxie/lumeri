"""Edit point-library tests — the cut-grammar contract and its taste floor.

Covers determinism, the structural invariants (straight-cuts-default budget, min
shot floor, dissolve-scales-with-pace, J/L splits, cut-on-action, jump-cut
avoidance, montage acceleration), style archetypes diverging, feedback, catalog
anti-drift, and unknown-input raising.
"""
from __future__ import annotations

import asyncio

import pytest

from lumenframe.craft import stable_digest
from lumenframe.craft.styles import StyleError
from lumenframe.edit import build_cut_plan, adjust_cut_plan, edit_catalog
from lumenframe.edit.api import EditBriefError
from lumenframe.edit.grammar import (
    TRANSITIONS,
    dissolve_ms,
    is_straight,
    min_shot_ms,
    transition_budget,
)
from lumenframe.edit.render import (
    EditRenderError,
    plan_to_timeline_ops,
    validate_cut_plan,
)
from lumenframe.edit.tool import dispatch


def _clips(n=4, **over):
    """n plain same-length clips in distinct scenes (default: transition-worthy)."""
    out = []
    for i in range(n):
        out.append({"id": f"c{i}", "duration": 3.0, "scene": f"s{i}", **over})
    return out


def _brief(**over):
    b = {"clips": _clips(), "style": "commercial", "seed": 7}
    b.update(over)
    return b


# ── determinism ─────────────────────────────────────────────────────────────

def test_determinism_byte_identical():
    b = _brief(style="commercial", feeling=["punchy", "varied"])
    r1 = build_cut_plan(b)
    r2 = build_cut_plan(dict(b))
    assert r1["cut_plan"] == r2["cut_plan"]
    assert r1["plan"]["digest"] == r2["plan"]["digest"]
    assert stable_digest(r1["cut_plan"]) == stable_digest(r2["cut_plan"])


def test_seed_is_the_only_nondeterminism():
    # Different seeds may reorder tie-broken transition slots; same seed cannot.
    a = build_cut_plan(_brief(seed=1))
    b = build_cut_plan(_brief(seed=2))
    a2 = build_cut_plan(_brief(seed=1))
    assert a["plan"]["digest"] == a2["plan"]["digest"]
    # (not asserting a != b — they may coincide — just that same seed is stable)


# ── taste floor: straight cuts are the default / budget cap ─────────────────

def test_straight_cuts_are_the_default_for_invisible():
    # Invisible style: cut_frac 0.10 → a handful of scene-change joins still
    # yield (almost) all straight cuts.
    r = build_cut_plan(_brief(style="invisible", clips=_clips(6)))
    straight = sum(1 for e in r["cut_plan"] if is_straight(e["transition"]))
    assert straight >= len(r["cut_plan"]) - 1     # at most one seasoning transition
    assert r["plan"]["transition_budget"] <= 1


def test_transition_budget_cap_scales_with_style():
    # Same worthy sequence: invisible gets far fewer transitions than dreamy.
    inv = build_cut_plan(_brief(style="invisible", clips=_clips(8)))
    dreamy = build_cut_plan(_brief(style="dreamy", clips=_clips(8)))
    assert inv["plan"]["transitions_used"] < dreamy["plan"]["transitions_used"]
    assert dreamy["plan"]["transitions_used"] >= 2


def test_transition_budget_never_exceeds_ceiling():
    # Direct maths: the count can never exceed round(n * cut_frac).
    assert transition_budget(10, 0.10, drama=1.0) <= 1
    assert transition_budget(10, 0.60, drama=1.0) <= 6
    assert transition_budget(10, 0.60, drama=0.0) <= transition_budget(10, 0.60, drama=1.0)


def _net_lengths(brief_clips, cut_plan):
    """Net picture length of every clip after ALL its edge-trims are applied.

    An interior clip receives two independent trims — an incoming trim as the
    to_clip of the preceding join and an outgoing trim as the from_clip of the
    next join — which stack on the same shot.
    """
    dur = {c["id"]: int(round(float(c["duration"]) * 1000)) for c in brief_clips}
    trimmed = {cid: 0 for cid in dur}
    for e in cut_plan:
        trimmed[e["from_clip"]] += abs(e["trim_out_adjust"])
        trimmed[e["to_clip"]] += abs(e["trim_in_adjust"])
    return {cid: dur[cid] - trimmed[cid] for cid in dur}


def test_min_shot_floor_and_trims_respect_it():
    r = build_cut_plan(_brief(style="energetic", feeling=["fast"], clips=_clips(4)))
    floor = r["plan"]["min_shot_ms"]
    assert floor >= 300
    # No trim may shave a 3000ms clip below the floor.
    for e in r["cut_plan"]:
        assert 3000 + e["trim_out_adjust"] >= floor
        assert 3000 - abs(e["trim_in_adjust"]) >= floor


def test_interior_clip_stacked_trims_respect_floor():
    # Finding [4] EXACT adversarial brief: a middle clip receives BOTH an incoming
    # trim (from the join before it) and an outgoing trim (from the join after it);
    # independently each stayed above the floor yet stacked below it (400-40-40=320
    # < 360). The two edge-trims must now share one per-clip slack budget.
    clips = [{"id": "a", "duration": 3.0, "scene": "s0", "has_action": True},
             {"id": "mid", "duration": 0.40, "scene": "s1", "has_action": True},
             {"id": "c", "duration": 3.0, "scene": "s2", "has_action": True}]
    r = build_cut_plan(_brief(style="documentary", params={"pace": 0.9},
                              clips=clips, seed=7))
    floor = r["plan"]["min_shot_ms"]
    nets = _net_lengths(clips, r["cut_plan"])
    assert nets["mid"] >= floor            # was 320 < 360 before the fix
    for cid, net in nets.items():
        assert net >= floor, f"{cid} net {net} < floor {floor}"


def test_short_clip_below_floor_is_noted():
    clips = [{"id": "a", "duration": 0.1, "scene": "x"},
             {"id": "b", "duration": 3.0, "scene": "y"}]
    r = build_cut_plan(_brief(clips=clips))
    assert any("minimum-shot floor" in n for n in r["notes"])


def test_dissolve_length_scales_with_pace():
    slow = build_cut_plan(_brief(style="dreamy", params={"pace": 0.1}, clips=_clips(6)))
    fast = build_cut_plan(_brief(style="dreamy", params={"pace": 0.9}, clips=_clips(6)))

    def longest_dissolve(res):
        return max((e["duration_ms"] for e in res["cut_plan"]
                    if e["transition"] in ("dissolve", "fade")), default=0)

    assert longest_dissolve(slow) > longest_dissolve(fast) > 0
    # And the pure function agrees.
    assert dissolve_ms(800, pace=0.1) > dissolve_ms(800, pace=0.9)


def test_cut_on_action_nudges_trims():
    clips = [{"id": "a", "duration": 3.0, "scene": "s", "has_action": True},
             {"id": "b", "duration": 3.0, "scene": "s", "has_action": True}]
    r = build_cut_plan(_brief(style="invisible", clips=clips))
    e = r["cut_plan"][0]
    # Continuous action across a same-scene join → a match cut, cut on movement.
    assert e["transition"] == "match_cut"
    assert "action" in e["reason"]


def test_jl_audio_split_for_documentary():
    # Documentary leans hard on J/L splits; at least one join carries an offset.
    r = build_cut_plan(_brief(style="documentary", clips=_clips(5)))
    assert any(e["j_cut_ms"] or e["l_cut_ms"] for e in r["cut_plan"])
    # Energetic barely splits audio.
    e2 = build_cut_plan(_brief(style="energetic", clips=_clips(5)))
    assert sum(x["j_cut_ms"] + x["l_cut_ms"] for x in e2["cut_plan"]) <= \
        sum(x["j_cut_ms"] + x["l_cut_ms"] for x in r["cut_plan"])


def test_jump_cut_is_avoided():
    # Two same-scene, similar, static shots must not cut straight.
    clips = [{"id": "a", "duration": 3.0, "scene": "room", "tags": ["wide"]},
             {"id": "b", "duration": 3.0, "scene": "room", "tags": ["wide"]}]
    r = build_cut_plan(_brief(style="invisible", clips=clips))
    e = r["cut_plan"][0]
    covered = (not is_straight(e["transition"])) or e["cutaway"]
    assert covered
    assert any("jump" in n.lower() for n in r["notes"]) or e["cutaway"]


def test_montage_accelerates_cadence():
    # Same-scene static clips so joins stay straight; trims tighten toward the end.
    clips = [{"id": f"c{i}", "duration": 5.0, "scene": "s", "tags": [f"t{i}"]}
             for i in range(5)]
    r = build_cut_plan(_brief(style="montage", params={"drama": 0.1}, clips=clips))
    straight = [e for e in r["cut_plan"] if is_straight(e["transition"])]
    mags = [-e["trim_out_adjust"] for e in straight]     # positive magnitudes
    assert mags == sorted(mags)          # monotonic non-decreasing (equal lengths)
    assert mags[-1] > mags[0]            # genuinely accelerates
    assert any("accelerat" in e["reason"] for e in straight)


def test_montage_short_interior_clip_respects_floor():
    # Findings [3]/[6]: with a short interior clip the applied accel trim CANNOT be
    # strictly monotonic without shoving that shot below the floor — the floor wins.
    # The honest contract is: every clip stays >= the floor even when action trims
    # and montage accel stack on the same short shot.
    clips = [{"id": f"c{i}", "duration": (0.5 if i == 4 else 5.0),
              "scene": "s", "has_action": True} for i in range(8)]
    r = build_cut_plan(_brief(style="montage", params={"drama": 0.1}, clips=clips, seed=0))
    floor = r["plan"]["min_shot_ms"]
    nets = _net_lengths(clips, r["cut_plan"])
    for cid, net in nets.items():
        assert net >= floor, f"{cid} net {net} < floor {floor}"


# ── jump-cut cover must not bypass the transition budget ────────────────────

def test_static_run_does_not_salt_every_join_energetic():
    # Findings [2]/[5] EXACT adversarial brief: a run of same-scene, static,
    # same-tag clips in a dramatic style used to promote EVERY out-of-budget join
    # to a seasoning whip_pan, so transitions_used blew past transition_budget.
    clips = [{"id": f"c{i}", "duration": 3.0, "scene": "room", "tags": ["wide"]}
             for i in range(10)]
    r = build_cut_plan(_brief(style="energetic", params={"drama": 0.9}, clips=clips, seed=7))
    budget = r["plan"]["transition_budget"]
    assert r["plan"]["transitions_used"] <= budget
    # Any whip_pan must be a budget-charged slot join, not a free salt on every
    # static jump risk — so the number of whip_pans is bounded by the budget, and
    # the leftover jump risks hold as cutaways rather than showy covers.
    whips = sum(1 for e in r["cut_plan"] if e["transition"] == "whip_pan")
    assert whips <= budget
    assert whips < len(r["cut_plan"])          # NOT salted onto every join
    assert any(e["cutaway"] for e in r["cut_plan"])


def test_static_run_budget_holds_across_dramatic_styles():
    clips = [{"id": f"c{i}", "duration": 3.0, "scene": "room", "tags": ["wide"]}
             for i in range(8)]
    for style in ("energetic", "commercial", "montage"):
        r = build_cut_plan(_brief(style=style, params={"drama": 0.9}, clips=clips, seed=7))
        assert r["plan"]["transitions_used"] <= r["plan"]["transition_budget"], style


# ── style archetypes differ ─────────────────────────────────────────────────

def test_styles_produce_distinct_plans():
    seq = _clips(6)
    digests = {
        s: build_cut_plan(_brief(style=s, clips=seq))["plan"]["digest"]
        for s in ("invisible", "energetic", "dreamy", "documentary", "montage", "commercial")
    }
    assert len(set(digests.values())) == len(digests)   # all six distinct


def test_style_alias_resolves():
    a = build_cut_plan(_brief(style="mtv", clips=_clips(6)))
    b = build_cut_plan(_brief(style="energetic", clips=_clips(6)))
    assert a["plan"]["style"] == b["plan"]["style"] == "energetic"
    assert a["plan"]["digest"] == b["plan"]["digest"]


# ── feedback ────────────────────────────────────────────────────────────────

def test_feedback_moves_intended_axis():
    base = build_cut_plan(_brief(style="documentary", clips=_clips(6)))
    res = adjust_cut_plan(_brief(style="documentary", clips=_clips(6)), ["more dramatic"])
    assert res["plan"]["axes"]["drama"] > base["plan"]["axes"]["drama"]
    assert "params" in res["brief"]


def test_feedback_more_seamless_raises_invisibility():
    base = build_cut_plan(_brief(style="commercial", clips=_clips(6)))
    res = adjust_cut_plan(_brief(style="commercial", clips=_clips(6)), ["more seamless"])
    assert res["plan"]["axes"]["invisibility"] > base["plan"]["axes"]["invisibility"]


def test_unknown_feedback_is_reported():
    res = adjust_cut_plan(_brief(clips=_clips(6)), ["more zorble", "更快"])
    assert any("zorble" in n for n in res["notes"])
    # "更快" is recognised (faster) so it is NOT in the unrecognised note.
    assert not any("更快" in n and "unrecognised" in n for n in res["notes"])


# ── catalog anti-drift ──────────────────────────────────────────────────────

def test_catalog_anti_drift():
    TRANSITIONS.check_catalog()
    cat = edit_catalog()
    names = {e["name"] for e in cat["transitions"]}
    assert names == set(TRANSITIONS.names())
    assert set(cat["axes"]) == {"pace", "invisibility", "drama", "variety"}
    # every advertised style really resolves
    for style in cat["styles"]:
        build_cut_plan(_brief(style=style, clips=_clips(4)))


def test_catalog_lists_real_feedback_vocab():
    cat = edit_catalog()
    assert "seamless" in cat["feedback_vocabulary"]
    assert "dramatic" in cat["feedback_vocabulary"]


# ── raising on unusable input ───────────────────────────────────────────────

def test_unknown_style_raises():
    with pytest.raises(StyleError):
        build_cut_plan(_brief(style="nope", clips=_clips(4)))


def test_too_few_clips_raises():
    with pytest.raises(EditBriefError):
        build_cut_plan({"clips": [{"id": "a", "duration": 3.0}]})


def test_bad_clip_raises():
    with pytest.raises(EditBriefError):
        build_cut_plan({"clips": [{"id": "a", "duration": -1}, {"id": "b", "duration": 2}]})
    with pytest.raises(EditBriefError):
        build_cut_plan({"clips": [{"id": "a", "duration": 2}, {"id": "a", "duration": 2}]})


def test_bad_seed_and_params_raise_edit_brief_error():
    # Finding [1]: malformed seed/params must funnel through EditBriefError, not
    # leak a raw ValueError/TypeError out of build_cut_plan.
    for seed in ("abc", None, [1]):
        with pytest.raises(EditBriefError):
            build_cut_plan(_brief(clips=_clips(3), seed=seed))
    for params in ({"pace": "fast"}, [1], {"pace": None}):
        with pytest.raises(EditBriefError):
            build_cut_plan(_brief(clips=_clips(3), params=params))


def test_tool_returns_e_arg_for_bad_seed_params_and_style():
    # Finding [1] error contract: the tool boundary must return a uniform E_ARG
    # for every structurally-bad brief, never propagate an exception.
    bad_briefs = [
        _brief(clips=_clips(3), seed="abc"),
        _brief(clips=_clips(3), seed=None),
        _brief(clips=_clips(3), seed=[1]),
        _brief(clips=_clips(3), params={"pace": "fast"}),
        _brief(clips=_clips(3), params=[1]),
        _brief(clips=_clips(3), style="nope"),          # StyleError → E_ARG
        _brief(clips=_clips(3), params={"nonaxis": 0.5}),  # unknown axis → E_ARG
    ]
    for b in bad_briefs:
        res = asyncio.run(dispatch({"op": "create", "brief": b}))
        assert not res["applied"] and res["error_code"] == "E_ARG", b
    # adjust boundary is guarded too.
    res = asyncio.run(dispatch({
        "op": "adjust", "brief": _brief(clips=_clips(3), seed="abc"),
        "feedback": ["more seamless"]}))
    assert not res["applied"] and res["error_code"] == "E_ARG"


# ── render adapter spec ─────────────────────────────────────────────────────

def test_render_validate_and_lower():
    r = build_cut_plan(_brief(style="commercial", clips=_clips(6)))
    validate_cut_plan(r["cut_plan"])
    ids = {f"c{i}": f"layer_{i}" for i in range(6)}
    ops = plan_to_timeline_ops(r["cut_plan"], ids)
    for op in ops:
        assert op["op"] in ("add_transition", "trim")
        if op["op"] == "add_transition":
            assert op["kind"] in ("fade", "dissolve", "wipe_l", "slide")
            assert op["duration"] > 0


def test_validate_rejects_straight_with_duration():
    bad = [{"from_clip": "a", "to_clip": "b", "transition": "cut", "duration_ms": 500}]
    with pytest.raises(EditRenderError):
        validate_cut_plan(bad)


def test_validate_rejects_unknown_transition():
    bad = [{"from_clip": "a", "to_clip": "b", "transition": "star_wipe", "duration_ms": 500}]
    with pytest.raises(EditRenderError):
        validate_cut_plan(bad)


# ── the single tool ─────────────────────────────────────────────────────────

def test_tool_create_adjust_catalog():
    create = asyncio.run(dispatch({"op": "create", "brief": _brief(clips=_clips(5))}))
    assert create["applied"] and create["cut_plan"]
    cat = asyncio.run(dispatch({"op": "catalog"}))
    assert cat["applied"] and "transitions" in cat["catalog"]
    adj = asyncio.run(dispatch({
        "op": "adjust", "brief": _brief(clips=_clips(5)), "feedback": ["more seamless"]}))
    assert adj["applied"] and "brief" in adj
    bad = asyncio.run(dispatch({"op": "frobnicate"}))
    assert not bad["applied"] and bad["error_code"] == "E_ARG"
    bad2 = asyncio.run(dispatch({"op": "create", "brief": "not-an-object"}))
    assert not bad2["applied"]
