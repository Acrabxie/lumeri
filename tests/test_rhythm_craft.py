"""Tests for the rhythm point library — the taste floor is what we assert.

Covers the point-library contract: determinism, the structural taste-floor
invariants (cuts on the grid, phrasing respected, accents on strong beats,
density follows energy/drive, build_drop accelerates, minimum-shot floor), style
archetypes differing, the feedback loop, catalog anti-drift, and raising on
unknown input.
"""
from __future__ import annotations

import asyncio

import pytest

from lumenframe.craft import stable_digest
from lumenframe.craft.determinism import round_floats
from lumenframe.rhythm import BriefError, adjust, build, rhythm_catalog
from lumenframe.rhythm.rhythm import MIN_SHOT_SECONDS, SYNC
from lumenframe.rhythm.render import CutPlanError, plan_to_timeline_ops, validate_cut_plan
from lumenframe.rhythm.styles import BOOK
from lumenframe.rhythm.tool import dispatch


def _sig(result):
    return stable_digest(round_floats(result["score"], 4))


# ── determinism ─────────────────────────────────────────────────────────────

def test_determinism_byte_identical_same_seed():
    brief = {"bpm": 128, "style": "syncopated", "seed": 7,
             "sections": [{"name": "verse", "bars": 8}]}
    a, b = build(brief), build(brief)
    assert _sig(a) == _sig(b)
    assert a["score"]["cut_plan"] == b["score"]["cut_plan"]


def test_seed_only_varies_syncopation():
    # Non-syncopated patterns are exact from bpm: the seed must not matter.
    base = {"bpm": 120, "style": "on_beat", "sections": [{"name": "v", "bars": 8}]}
    assert _sig(build({**base, "seed": 1})) == _sig(build({**base, "seed": 999}))
    # Syncopated *does* depend on the seed (different displaced off-beats).
    syn = {"bpm": 120, "style": "syncopated", "sync": 0.2,
           "sections": [{"name": "v", "bars": 16}]}
    assert _sig(build({**syn, "seed": 1})) != _sig(build({**syn, "seed": 2}))


def test_beat_grid_times_exact_from_bpm():
    score = build({"bpm": 120, "sections": [{"name": "v", "bars": 2}]})["score"]
    assert score["seconds_per_beat"] == pytest.approx(0.5)  # 60/120
    assert [g["t"] for g in score["beat_grid"][:4]] == [0.0, 0.5, 1.0, 1.5]
    assert score["beat_grid"][0]["downbeat"] is True
    assert score["beat_grid"][1]["downbeat"] is False


# ── taste-floor invariants ──────────────────────────────────────────────────

def test_cuts_land_on_grid_except_syncopated():
    # on_beat / on_downbeat / on_phrase never leave the integer-beat grid.
    for style in ("on_beat", "on_downbeat", "on_phrase"):
        cuts = build({"bpm": 128, "style": style,
                      "sections": [{"name": "v", "bars": 16}]})["score"]["cut_plan"]
        assert cuts, style
        assert all(c["offbeat"] is False for c in cuts), style


def test_on_downbeat_only_hits_beat_one():
    cuts = build({"bpm": 128, "style": "on_downbeat",
                  "sections": [{"name": "v", "bars": 16}]})["score"]["cut_plan"]
    assert all(c["beat"] == 1 for c in cuts)
    assert all(c["accent"] for c in cuts)  # every downbeat is an accent


def test_on_phrase_respects_phrasing():
    result = build({"bpm": 128, "style": "on_phrase",
                    "sections": [{"name": "v", "bars": 16}]})
    phrase_bars = result["plan"]["phrase_bars"]
    for c in result["score"]["cut_plan"]:
        assert c["bar"] % phrase_bars == 0  # every cut on a real phrase boundary
        assert c["beat"] == 1


def test_syncopated_is_the_only_off_grid_style():
    cuts = build({"bpm": 128, "style": "syncopated", "sync": 0.1, "seed": 3,
                  "sections": [{"name": "v", "bars": 16}]})["score"]["cut_plan"]
    assert any(c["offbeat"] for c in cuts), "syncopated should displace some cuts off the grid"
    # …and even off-grid it never cuts faster than one beat.
    idxs = [c["beat_index"] for c in cuts]
    assert all((b - a) >= 1.0 - 1e-9 for a, b in zip(idxs, idxs[1:]))


def test_accents_land_on_strong_beats():
    cuts = build({"bpm": 128, "style": "on_beat", "drive": 0.9,
                  "sections": [{"name": "v", "bars": 8}]})["score"]["cut_plan"]
    for c in cuts:
        if c["accent"]:
            assert c["beat"] == 1 and c["offbeat"] is False


def test_density_follows_drive():
    sparse = build({"bpm": 128, "style": "on_beat", "drive": 0.05, "energy": 0.1,
                    "sections": [{"name": "v", "bars": 16}]})["score"]["cut_plan"]
    busy = build({"bpm": 128, "style": "on_beat", "drive": 0.95, "energy": 0.9,
                  "sections": [{"name": "v", "bars": 16}]})["score"]["cut_plan"]
    assert len(busy) > len(sparse)


def test_min_shot_floor_holds_even_double_time_fast():
    # 175bpm double-time: half-beats would be ~0.17s < floor; must be filtered.
    cuts = build({"bpm": 175, "style": "double_time", "drive": 1.0, "energy": 1.0,
                  "sections": [{"name": "v", "bars": 8}]})["score"]["cut_plan"]
    gaps = [b["t"] - a["t"] for a, b in zip(cuts, cuts[1:])]
    assert gaps and min(gaps) >= MIN_SHOT_SECONDS - 1e-9


def test_no_pattern_but_double_time_cuts_faster_than_a_beat():
    spb = 60.0 / 120
    for style in ("on_beat", "on_downbeat", "on_phrase", "half_time"):
        cuts = build({"bpm": 120, "style": style, "drive": 1.0, "energy": 1.0,
                      "sections": [{"name": "v", "bars": 16}]})["score"]["cut_plan"]
        gaps = [b["t"] - a["t"] for a, b in zip(cuts, cuts[1:])]
        assert all(g >= spb - 1e-6 for g in gaps), f"{style} cut faster than a beat"


def test_build_drop_accelerates_into_the_drop_and_sustains():
    score = build({"bpm": 128, "style": "build_drop", "build": 0.9,
                   "sections": [{"name": "intro", "bars": 8},
                                {"name": "drop", "bars": 8}]})["score"]
    plan_meta = build({"bpm": 128, "style": "build_drop", "build": 0.9,
                       "sections": [{"name": "intro", "bars": 8},
                                    {"name": "drop", "bars": 8}]})["plan"]
    drop_beat = plan_meta["drop_start_beat"]
    idxs = [c["beat_index"] for c in score["cut_plan"]]
    before = [b - a for a, b in zip(idxs, idxs[1:]) if a < drop_beat]
    after = [b - a for a, b in zip(idxs, idxs[1:]) if a >= drop_beat]
    # The build region's intervals are non-increasing (density accelerates)…
    assert all(x >= y - 1e-9 for x, y in zip(before, before[1:])), before
    # …and the sustained (post-drop) interval is at least as dense as the
    # tightest build-region interval.
    assert after and min(before or [99]) >= max(after) - 1e-9


def test_offset_shifts_the_whole_grid():
    a = build({"bpm": 120, "sections": [{"name": "v", "bars": 2}]})["score"]
    b = build({"bpm": 120, "offset_ms": 100,
               "sections": [{"name": "v", "bars": 2}]})["score"]
    assert b["cut_plan"][0]["t"] == pytest.approx(a["cut_plan"][0]["t"] + 0.1)


def test_clips_assigned_in_order_and_cycle():
    cuts = build({"bpm": 120, "style": "on_beat", "drive": 0.9,
                  "clips": [{"id": "A", "duration": 1}, {"id": "B", "duration": 1}],
                  "sections": [{"name": "v", "bars": 4}]})["score"]["cut_plan"]
    ids = [c["clip"] for c in cuts]
    assert ids[0] == "A" and ids[1] == "B" and ids[2] == "A"  # round-robin


# ── style archetypes differ ─────────────────────────────────────────────────

def test_style_archetypes_produce_distinct_plans():
    briefs = {s: build({"bpm": 128, "style": s,
                        "sections": [{"name": "v", "bars": 16}]})
              for s in ("on_beat", "on_downbeat", "on_phrase", "half_time",
                        "double_time")}
    sigs = {s: _sig(r) for s, r in briefs.items()}
    assert len(set(sigs.values())) == len(sigs)  # all distinct


def test_edm_alias_resolves_to_build_drop():
    assert BOOK.resolve_name("edm") == "build_drop"
    edm = build({"bpm": 128, "style": "edm",
                 "sections": [{"name": "drop", "bars": 8}]})["plan"]
    assert edm["style"] == "build_drop" and edm["pattern"] == "build_drop"


# ── feedback loop ───────────────────────────────────────────────────────────

def test_feedback_more_driving_increases_density():
    brief = {"bpm": 128, "style": "on_beat", "drive": 0.3, "energy": 0.3,
             "sections": [{"name": "v", "bars": 16}], "seed": 4}
    base = build(brief)
    res = adjust(brief, ["much more driving"])
    assert res["brief"]["params"]["drive"] > 0.3
    assert len(res["score"]["cut_plan"]) >= len(base["score"]["cut_plan"])


def test_feedback_tighter_moves_tightness_axis():
    brief = {"bpm": 128, "style": "syncopated", "sync": 0.5,
             "sections": [{"name": "v", "bars": 16}], "seed": 1}
    res = adjust(brief, ["more tight"])
    assert res["brief"]["params"]["tightness"] > 0.5


def test_feedback_unknown_phrase_reported():
    brief = {"bpm": 128, "sections": [{"name": "v", "bars": 8}]}
    res = adjust(brief, ["more wobblecore"])
    assert any("wobblecore" in n for n in res["notes"])


def test_adjust_keeps_seed_deterministic():
    brief = {"bpm": 128, "style": "syncopated", "seed": 11,
             "sections": [{"name": "v", "bars": 16}]}
    a = adjust(brief, ["more busy"])
    b = adjust(brief, ["more busy"])
    assert _sig(a) == _sig(b)


# ── catalog anti-drift ──────────────────────────────────────────────────────

def test_catalog_anti_drift():
    SYNC.check_catalog()  # registry catalog mirrors implementations
    cat = rhythm_catalog()
    sync_names = {e["name"] for e in cat["sync_patterns"]}
    assert sync_names == set(SYNC.names())
    # every style names a real, registered pattern
    for style in cat["styles"]["styles"]:
        pattern = BOOK.spec(style).hints["pattern"]
        assert pattern in SYNC.names(), style
    assert set(cat["axes"]) == {"energy", "tightness", "drive", "build"}
    assert "driving" in cat["feedback_vocabulary"]


# ── raising on unknown / unusable input ─────────────────────────────────────

def test_unknown_style_raises():
    # An unknown style is a structurally-unusable brief: build() maps StyleError
    # to the uniform BriefError so the tool boundary can return an err() dict
    # (the same convention used for malformed sections).
    with pytest.raises(BriefError):
        build({"bpm": 128, "style": "vaporwave-glitchhop"})


def test_missing_bpm_raises():
    with pytest.raises(BriefError):
        build({"style": "on_beat"})


def test_out_of_range_bpm_raises():
    with pytest.raises(BriefError):
        build({"bpm": 5})
    with pytest.raises(BriefError):
        build({"bpm": 5000})


def test_bad_section_raises():
    with pytest.raises(BriefError):
        build({"bpm": 128, "sections": [{"name": "x", "bars": 0}]})


def test_unknown_override_axis_raises():
    # Unknown override axis is also a bad brief → BriefError (a ValueError).
    with pytest.raises(BriefError):
        build({"bpm": 128, "params": {"loudness": 0.9}})


# ── render adapter ──────────────────────────────────────────────────────────

def test_plan_to_timeline_ops_shape():
    score = build({"bpm": 128, "style": "on_beat", "drive": 0.7,
                   "clips": [{"id": "c1", "duration": 1}],
                   "sections": [{"name": "v", "bars": 4}]})["score"]
    ops = plan_to_timeline_ops(score)
    assert ops and all(o["op"] == "cut" for o in ops)
    assert ops[0]["clip"] == "c1"
    assert all(a["t"] < b["t"] for a, b in zip(ops, ops[1:]))  # strictly increasing


def test_validate_rejects_backwards_and_seizure_plans():
    good = build({"bpm": 128, "sections": [{"name": "v", "bars": 4}]})["score"]
    validate_cut_plan(good)  # ok
    bad = {**good, "cut_plan": [{"t": 1.0, "bar": 0, "beat": 1},
                                {"t": 0.9, "bar": 0, "beat": 2}]}
    with pytest.raises(CutPlanError):
        validate_cut_plan(bad)
    seizure = {**good, "cut_plan": [{"t": 1.0, "bar": 0, "beat": 1},
                                    {"t": 1.01, "bar": 0, "beat": 2}]}
    with pytest.raises(CutPlanError):
        validate_cut_plan(seizure)


# ── the single tool ─────────────────────────────────────────────────────────

def test_tool_create_adjust_catalog_roundtrip():
    brief = {"bpm": 128, "style": "build_drop",
             "sections": [{"name": "intro", "bars": 8}, {"name": "drop", "bars": 8}]}
    create = asyncio.run(dispatch({"op": "create", "brief": brief}))
    assert create["applied"] and create["score"]["cut_plan"]
    assert create["timeline_ops"][0]["op"] == "cut"

    adj = asyncio.run(dispatch({"op": "adjust", "brief": brief, "feedback": ["more driving"]}))
    assert adj["applied"] and adj["brief"]["params"]["drive"] > 0

    cat = asyncio.run(dispatch({"op": "catalog"}))
    assert cat["applied"] and "styles" in cat["catalog"]


def test_tool_errors_are_uniform():
    bad = asyncio.run(dispatch({"op": "create", "brief": {"style": "on_beat"}}))
    assert bad["applied"] is False and bad["error_code"] == "E_ARG"
    unknown_op = asyncio.run(dispatch({"op": "frobnicate"}))
    assert unknown_op["applied"] is False and unknown_op["error_code"] == "E_ARG"


def test_tool_unknown_style_returns_err_not_crash():
    # The single most common agent mistake — a typo'd style — must come back as a
    # uniform err() dict, not an uncaught StyleError. (Finding [1] evidence.)
    for op in ("create", "adjust"):
        args = {"op": op, "brief": {"bpm": 128, "style": "vaporwave-glitchhop"}}
        if op == "adjust":
            args["feedback"] = ["more driving"]
        res = asyncio.run(dispatch(args))
        assert res["applied"] is False, op
        assert res["error_code"] == "E_ARG", op


def test_tool_unknown_override_axis_returns_err_not_crash():
    # A bogus override axis (e.g. 'loudness') used to raise an uncaught ValueError
    # through the create/adjust surface; it must now be a uniform err() dict.
    for op in ("create", "adjust"):
        args = {"op": op, "brief": {"bpm": 128, "params": {"loudness": 0.9}}}
        if op == "adjust":
            args["feedback"] = ["more driving"]
        res = asyncio.run(dispatch(args))
        assert res["applied"] is False, op
        assert res["error_code"] == "E_ARG", op
