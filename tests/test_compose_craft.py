"""Craft tests for the ``compose`` point library — the framing taste floor.

Covers the six contract areas: determinism, taste-floor invariants (anchor on a
third, headroom scales with tightness but never crops the head, lead room,
horizon on a third, aspect/containment), style archetypes differing, feedback
moving the intended axis + reporting unknowns, catalog anti-drift, and raising
on unknown input.
"""
from __future__ import annotations

import asyncio

import pytest

from lumenframe.craft import StyleError, stable_digest

from lumenframe.compose import compose_catalog, validate_overlay
from lumenframe.compose.api import BriefError, adjust_frame, build_frame
from lumenframe.compose.framing import GRIDS
from lumenframe.compose.params import compose_feedback
from lumenframe.compose.render import compose_overlay_svg
from lumenframe.compose.tool import dispatch


def brief(**over):
    """A small, un-clamped single-subject brief (subject ~30% tall)."""
    b = {
        "subjects": [{"bbox": [0.42, 0.15, 0.16, 0.30]}],
        "canvas": {"width": 1920, "height": 1080},
        "framing": "thirds",
        "seed": 7,
    }
    b.update(over)
    return b


def _crop(result):
    return result["reframe"]["crop"]


def _run(args):
    return asyncio.run(dispatch(args))


# ── 1. determinism ──────────────────────────────────────────────────────────

def test_build_is_byte_identical_per_seed():
    a = build_frame(brief())
    b = build_frame(brief())
    assert stable_digest(a) == stable_digest(b)
    assert a == b


def test_overlay_is_deterministic():
    r = build_frame(brief())
    s1 = compose_overlay_svg(r["reframe"], 1920, 1080)
    s2 = compose_overlay_svg(r["reframe"], 1920, 1080)
    assert s1 == s2
    validate_overlay(s1)


# ── 2. taste-floor invariants ───────────────────────────────────────────────

def test_thirds_places_subject_off_centre_on_a_third():
    r = build_frame(brief(framing="thirds"))
    sx = r["reframe"]["subject_anchor"][0]
    assert abs(sx - 0.5) > 0.1                     # not dead centre
    assert sx == pytest.approx(1 / 3, abs=0.02)    # on the left third


def test_centered_is_the_only_framing_that_centres():
    r = build_frame(brief(framing="centered"))
    sx = r["reframe"]["subject_anchor"][0]
    assert sx == pytest.approx(0.5, abs=0.03)


def test_headroom_scales_with_tightness_but_never_crops_the_head():
    subj = brief()["subjects"][0]["bbox"]
    top = subj[1]
    tight = build_frame(brief(framing="tight"))
    loose = build_frame(brief(framing="thirds"))

    def headroom(res):
        cy, ch = res["reframe"]["crop"][1], res["reframe"]["crop"][3]
        # head must be inside the crop (never cropped): crop top above subject top
        assert cy <= top + 1e-6
        return (top - cy) / ch

    assert headroom(tight) < headroom(loose)       # tighter → less headroom


def test_subject_stays_fully_inside_the_crop():
    r = build_frame(brief())
    cx, cy, cw, ch = r["reframe"]["crop"]
    x, y, w, h = brief()["subjects"][0]["bbox"]
    assert cx - 1e-6 <= x and cy - 1e-6 <= y
    assert x + w <= cx + cw + 1e-6 and y + h <= cy + ch + 1e-6
    # and the primary marker reports itself in-frame, within [0,1]
    prim = next(m for m in r["reframe"]["guides"]["subject_markers"] if m["primary"])
    assert prim["in_frame"]
    bx, by, bw, bh = prim["bbox"]
    assert bx >= -1e-6 and by >= -1e-6 and bx + bw <= 1 + 1e-6 and by + bh <= 1 + 1e-6


def test_lead_room_follows_facing():
    right = build_frame(brief(subjects=[{"bbox": [0.42, 0.15, 0.16, 0.30], "facing": "right"}]))
    left = build_frame(brief(subjects=[{"bbox": [0.42, 0.15, 0.16, 0.30], "facing": "left"}]))
    sx_right = right["reframe"]["subject_anchor"][0]
    sx_left = left["reframe"]["subject_anchor"][0]
    # facing right → subject on the LEFT (room opens to the right), and vice versa
    assert sx_right < 0.45 < 0.55 < sx_left


def test_crop_holds_target_aspect_and_stays_in_source():
    # square source, 9:16 target → crop w:h must equal 9/16 in source units
    r = build_frame(brief(canvas={"width": 1080, "height": 1920}, source_aspect=1.0))
    cx, cy, cw, ch = r["reframe"]["crop"]
    assert cw / ch == pytest.approx(1080 / 1920, abs=1e-3)
    assert 0 <= cx and 0 <= cy and cx + cw <= 1 + 1e-6 and cy + ch <= 1 + 1e-6


def _actual_horizon_fraction(r, horizon):
    """Where the horizon truly sits inside the crop, from the crop geometry."""
    cy, ch = r["reframe"]["crop"][1], r["reframe"]["crop"][3]
    return (horizon - cy) / ch


def test_horizon_snaps_to_a_third_never_the_middle():
    # A subject with vertical room so the horizon can reach its third without
    # cropping the subject (containment is the hard floor).
    r = build_frame(brief(horizon=0.6, subjects=[{"bbox": [0.4, 0.55, 0.12, 0.10]}]))
    line = r["reframe"]["guides"]["horizon_line"]
    assert line in (pytest.approx(1 / 3, abs=1e-6), pytest.approx(2 / 3, abs=1e-6))
    assert line == pytest.approx(2 / 3, abs=1e-6)       # 0.6 is nearer the lower third
    assert line != pytest.approx(0.5)
    # horizon_line must report where the crop ACTUALLY places the horizon, not a
    # faked third (finding 6): the reported line equals the in-crop fraction
    # (tolerance covers the 6-dp rounding of both horizon_line and the crop).
    assert line == pytest.approx(_actual_horizon_fraction(r, 0.6), abs=1e-4)


def test_horizon_tie_resolves_to_upper_third():
    r = build_frame(brief(horizon=0.5, subjects=[{"bbox": [0.4, 0.45, 0.12, 0.16]}]))
    line = r["reframe"]["guides"]["horizon_line"]
    assert line == pytest.approx(1 / 3, abs=1e-6)
    assert line == pytest.approx(_actual_horizon_fraction(r, 0.5), abs=1e-4)


def test_horizon_line_reports_actual_fraction_when_source_lacks_room():
    # Finding 6 adversarial brief: the source has no room to lift the crop up to
    # a genuine upper third, so the horizon lands ABOVE 1/3 — horizon_line must
    # report the true fraction, never the unreachable 1/3 the render would draw.
    r = build_frame({
        "subjects": [{"bbox": [0.45, 0.40, 0.10, 0.14]}],
        "canvas": {"width": 1920, "height": 1080},
        "source_aspect": 1.0, "framing": "wide", "horizon": 0.08, "seed": 7,
    })
    line = r["reframe"]["guides"]["horizon_line"]
    actual = _actual_horizon_fraction(r, 0.08)
    assert line == pytest.approx(actual, abs=1e-4)      # honest, not a faked 1/3
    assert line != pytest.approx(1 / 3, abs=1e-3)       # the third was NOT reached
    assert any("sits at" in n for n in r["notes"])      # and it says so


def test_horizon_never_crops_the_subject_head_or_pushes_it_out():
    # Findings 1 & 5 adversarial briefs: a person in a landscape (subjects +
    # horizon) must keep the head uncropped and the subject fully in frame — the
    # horizon yields to containment, never the other way round.
    briefs = [
        {"subjects": [{"bbox": [0.15, 0.60, 0.10, 0.30]}],
         "canvas": {"width": 1920, "height": 1080},
         "framing": "wide", "horizon": 0.35, "source_aspect": 1.0, "seed": 7},
        {"subjects": [{"bbox": [0.40, 0.05, 0.10, 0.12]}],
         "canvas": {"width": 1080, "height": 1920},
         "framing": "tight", "horizon": 0.9, "source_aspect": 1.0, "seed": 7},
    ]
    for b in briefs:
        r = build_frame(b)
        cx, cy, cw, ch = r["reframe"]["crop"]
        sx, sy, sw, sh = b["subjects"][0]["bbox"]
        assert cy <= sy + 1e-6                           # head never cropped
        assert sy + sh <= cy + ch + 1e-6                 # feet stay inside
        assert cx <= sx + 1e-6 and sx + sw <= cx + cw + 1e-6
        prim = next(m for m in r["reframe"]["guides"]["subject_markers"] if m["primary"])
        assert prim["in_frame"]                          # marker agrees it's inside
        bx, by, bw, bh = prim["bbox"]
        assert bx >= -1e-6 and by >= -1e-6
        assert bx + bw <= 1 + 1e-6 and by + bh <= 1 + 1e-6


def test_partially_clipped_subject_reports_not_in_frame():
    # Finding 3: a subject wider than the widest 9:16 crop that fits a square
    # source (0.5625) genuinely cannot be contained, so it IS clipped; the
    # in_frame flag must be False (full containment), not True (mere overlap),
    # so consumers can detect the clip.
    r = build_frame({
        "subjects": [{"bbox": [0.15, 0.40, 0.70, 0.20], "facing": "right"}],
        "canvas": {"width": 1080, "height": 1920},
        "source_aspect": 1.0, "framing": "tight", "params": {"tightness": 1.0},
        "seed": 7,
    })
    prim = next(m for m in r["reframe"]["guides"]["subject_markers"] if m["primary"])
    bx, by, bw, bh = prim["bbox"]
    clipped = bx < -1e-6 or by < -1e-6 or bx + bw > 1 + 1e-6 or by + bh > 1 + 1e-6
    assert clipped                                       # the tall subject IS clipped
    assert prim["in_frame"] is False                     # and in_frame says so


def test_balance_note_is_honest_when_crop_caps_to_source():
    # Finding 4: fill so low the crop caps to the full source → the subject lands
    # dead-centre and CANNOT be placed on a third; balance_note must not claim a
    # third it isn't on, and must match subject_anchor.
    r = build_frame({
        "subjects": [{"bbox": [0.42, 0.15, 0.16, 0.30], "facing": "right"}],
        "canvas": {"width": 1080, "height": 1920},
        "framing": "golden", "feeling": ["airy", "tense"], "seed": 7,
    })
    assert r["reframe"]["crop"] == [0.0, 0.0, 1.0, 1.0]
    sax = r["reframe"]["subject_anchor"][0]
    assert sax == pytest.approx(0.5, abs=1e-3)           # forced dead-centre
    note = r["reframe"]["balance_note"]
    assert "left third" not in note                      # no false third claim
    assert "too tight" in note or "not achievable" in note


def test_secondary_mass_balances_to_the_opposite_third():
    # primary (heavier) on the left, a lighter secondary to its right → primary
    # anchored left so the secondary weight falls toward the right third.
    b = brief(subjects=[
        {"bbox": [0.30, 0.30, 0.20, 0.34], "weight": 1.0},   # primary (bigger)
        {"bbox": [0.66, 0.40, 0.10, 0.16], "weight": 0.2},   # secondary, to the right
    ])
    r = build_frame(b)
    assert r["reframe"]["subject_anchor"][0] < 0.45
    assert "right" in r["reframe"]["balance_note"]


# ── 3. style archetypes differ ──────────────────────────────────────────────

def test_distinct_framings_produce_distinct_frames():
    seen = {}
    for name in ("thirds", "centered", "golden", "tight", "wide"):
        seen[name] = stable_digest(build_frame(brief(framing=name))["reframe"])
    assert len(set(seen.values())) == len(seen)


def test_golden_anchor_sits_on_a_phi_line():
    r = build_frame(brief(framing="golden"))
    sx = r["reframe"]["subject_anchor"][0]
    assert sx == pytest.approx(0.382, abs=0.02)
    assert r["reframe"]["guides"]["spiral"] is True


# ── 4. feedback ─────────────────────────────────────────────────────────────

def test_more_tension_moves_the_axis_and_the_frame():
    base = build_frame(brief())
    out = adjust_frame(brief(), ["more tension"])
    assert out["plan"]["axes"]["axes"]["tension"] > base["plan"]["axes"]["axes"]["tension"]
    assert out["reframe"]["crop"] != base["reframe"]["crop"]   # geometry actually moved


def test_tighter_increases_tightness():
    base = build_frame(brief())
    out = adjust_frame(brief(), ["tighter"])
    assert out["plan"]["axes"]["axes"]["tightness"] > base["plan"]["axes"]["axes"]["tightness"]


def test_unknown_feedback_phrase_is_reported():
    out = adjust_frame(brief(), ["more banana"])
    assert any("banana" in n for n in out["notes"])


# ── 5. catalog anti-drift ───────────────────────────────────────────────────

def test_grid_catalog_has_no_drift():
    GRIDS.check_catalog()
    cat_names = {e["name"] for e in compose_catalog()["grids"]}
    assert cat_names == set(GRIDS.names())


def test_catalog_lists_real_framings_and_vocab():
    cat = compose_catalog()
    assert set(cat["framings"]) >= {
        "thirds", "centered", "golden", "negative_space", "dynamic", "tight", "wide"}
    assert cat["framing_aliases"]["symmetry"] == "centered"
    assert cat["framing_aliases"]["phi"] == "golden"
    vocab = cat["feedback_vocabulary"]
    assert vocab == compose_feedback().vocabulary()
    assert "tension" in vocab and "tight" in vocab


# ── 6. raises on unknown / unusable input ───────────────────────────────────

def test_unknown_framing_raises():
    with pytest.raises(StyleError):
        build_frame(brief(framing="banana"))


def test_empty_subjects_raises():
    with pytest.raises(BriefError):
        build_frame(brief(subjects=[]))


def test_out_of_range_bbox_raises():
    with pytest.raises(BriefError):
        build_frame(brief(subjects=[{"bbox": [0.9, 0.2, 0.3, 0.3]}]))   # x+w > 1


def test_bad_facing_raises():
    with pytest.raises(BriefError):
        build_frame(brief(subjects=[{"bbox": [0.4, 0.2, 0.2, 0.3], "facing": "sideways"}]))


# ── tool surface ────────────────────────────────────────────────────────────

def test_tool_create_returns_recipe_and_safe_overlay():
    res = _run({"op": "create", "brief": brief()})
    assert res["applied"] is True
    assert "crop" in res["reframe"]
    validate_overlay(res["overlay_svg"])


def test_tool_catalog_and_bad_op():
    assert _run({"op": "catalog"})["catalog"]["default_framing"] == "thirds"
    assert _run({"op": "frobnicate"})["applied"] is False


def test_tool_adjust_roundtrips_brief():
    res = _run({"op": "adjust", "brief": brief(), "feedback": ["more tension"]})
    assert res["applied"] is True
    assert "brief" in res and res["brief"]["params"]["tension"] > 0.5
