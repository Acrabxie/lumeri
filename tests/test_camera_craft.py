"""Camera point-library tests — the taste floor, made executable.

Covers the contract the assignment enforces: determinism, the four taste-floor
invariants (ease in/out, motivated, keep-in-frame, subtlety ceiling + organic
handheld), style archetypes diverging, the feedback loop, catalog anti-drift,
and the raise-on-unknown behaviours.
"""
from __future__ import annotations

import math

import pytest

from lumenframe.camera import camera as cam
from lumenframe.camera.api import BriefError, adjust_track, build_track
from lumenframe.camera.catalog import camera_catalog, describe_camera
from lumenframe.camera.params import feedback_vocab
from lumenframe.camera.render import (
    compose_track,
    track_to_svg,
    track_to_transform_ops,
    validate_camera_svg,
)
from lumenframe.camera.styles import camera_styles
from lumenframe.craft import stable_digest
from lumenframe.craft.determinism import round_floats


def _brief(**over):
    b = {
        "move": "push_in",
        "subject": {"x": 0.32, "y": 0.6},
        "style": "cinematic",
        "duration": 6.0,
        "canvas": {"width": 1920, "height": 1080},
        "seed": 7,
    }
    b.update(over)
    return b


def _digest(track):
    return stable_digest(round_floats(track, 5))


# ── determinism ─────────────────────────────────────────────────────────────

def test_determinism_byte_identical():
    a = build_track(_brief())["track"]
    b = build_track(_brief())["track"]
    assert _digest(a) == _digest(b)
    # the handheld layer (the only stochastic part) is stable too
    assert a["handheld"]["samples"] == b["handheld"]["samples"]


def test_seed_changes_handheld():
    a = build_track(_brief(style="handheld", seed=1))["track"]
    b = build_track(_brief(style="handheld", seed=2))["track"]
    assert a["handheld"]["samples"] != b["handheld"]["samples"]


def test_svg_is_deterministic():
    s1 = track_to_svg(build_track(_brief())["track"])
    s2 = track_to_svg(build_track(_brief())["track"])
    assert s1 == s2


# ── taste floor: ease in and out (never linear) ─────────────────────────────

def test_main_moves_are_eased_never_linear():
    for move in cam.move_names():
        track = build_track(_brief(move=move))["track"]
        eases = [kf["ease"] for kf in track["keyframes"] if kf["ease"]]
        assert eases, f"{move} has no eased segment"
        for name in eases:
            assert name in cam.MAIN_EASE_NAMES
            assert cam.EASINGS[name] != (0.0, 0.0, 1.0, 1.0)  # not the linear diagonal


def test_easing_curves_bend():
    # a genuinely eased curve departs from the linear identity somewhere on the
    # unit interval (a symmetric S is legitimately 0.5 at t=0.5, so probe a grid)
    grid = [i / 20 for i in range(1, 20)]
    for name in cam.EASINGS:
        max_dev = max(abs(cam.cubic_bezier(name, t) - t) for t in grid)
        assert max_dev > 0.03, f"{name} looks linear"
    # monotonic and pinned at the ends
    for name in cam.EASINGS:
        assert cam.cubic_bezier(name, 0.0) == pytest.approx(0.0, abs=1e-3)
        assert cam.cubic_bezier(name, 1.0) == pytest.approx(1.0, abs=1e-3)


# ── taste floor: motivated (push toward the subject) ────────────────────────

def test_push_translates_toward_subject():
    # subject sits left-of-centre (x=0.2) → the push must translate the framing
    # to the right (positive tx) to bring it toward centre, and it must move.
    track = build_track(_brief(move="push_in", subject={"x": 0.2, "y": 0.5}))["track"]
    start, end = track["keyframes"][0], track["keyframes"][-1]
    assert end["scale"] > start["scale"]           # it is a push
    assert end["tx"] > start["tx"]                  # toward the subject side
    assert end["tx"] > 0                            # (0.5 - 0.2) > 0 → positive


def test_centered_subject_needs_little_translation():
    track = build_track(_brief(move="push_in", subject={"x": 0.5, "y": 0.5}))["track"]
    assert abs(track["keyframes"][-1]["tx"]) < 1.0
    assert abs(track["keyframes"][-1]["ty"]) < 1.0


# ── taste floor: keep the subject in frame ──────────────────────────────────

def test_composed_track_never_reveals_an_edge():
    # every move, incl. handheld wobble, must cover the frame at every sample —
    # AND at the hard case: corner subjects that pin the framing hard against
    # the budget, on cranked drift, on an extreme-aspect canvas where the fit
    # sits on the eps boundary with no headroom. This is where the emitted
    # (rounded) sample used to tip a corner across covers_frame(eps=0.75).
    subjects = ({"x": 0.08, "y": 0.9}, {"x": 0.0, "y": 0.0},
                {"x": 1.0, "y": 1.0}, {"x": 1.0, "y": 0.0}, {"x": 0.0, "y": 1.0})
    canvases = ({"width": 1920, "height": 1080}, {"width": 1920, "height": 400})
    overrides = (None, {"drift": 1.0}, {"drift": 1.0, "energy": 1.0, "drama": 1.0})
    for move in cam.move_names():
        for style in ("locked", "handheld", "energetic", "epic"):
            for subject in subjects:
                for canvas in canvases:
                    for params in overrides:
                        over = dict(move=move, style=style, subject=subject,
                                    canvas=canvas)
                        if params is not None:
                            over["params"] = params
                        track = build_track(_brief(**over))["track"]
                        w = track["canvas"]["width"]
                        h = track["canvas"]["height"]
                        for s in compose_track(track):
                            assert s["scale"] >= 1.0 - 1e-9
                            assert cam.covers_frame(
                                s["scale"], s["tx"], s["ty"], s["rot"], w, h), (
                                f"{move}/{style} subj={subject} canvas={canvas} "
                                f"params={params} revealed an edge at t={s['t']}")


def test_scale_never_below_one():
    for move in cam.move_names():
        track = build_track(_brief(move=move, style="epic"))["track"]
        assert min(kf["scale"] for kf in track["keyframes"]) >= 1.0


# ── taste floor: subtlety ceiling + organic handheld ────────────────────────

def test_default_push_delta_is_subtle():
    plan = build_track(_brief(move="push_in"))["plan"]
    assert 1.05 <= plan["push_delta"] <= 1.22


def test_push_delta_capped_even_when_cranked():
    plan = build_track(_brief(move="dolly", style="energetic",
                              params={"energy": 1.0, "drama": 1.0}))["plan"]
    lo, hi = plan["scale_range"]
    assert hi <= 1.7 + 1e-6            # hard subtlety cap holds


def test_handheld_is_band_limited_sines_not_noise():
    track = build_track(_brief(style="handheld", seed=3))["track"]
    hh = track["handheld"]
    assert hh is not None
    for chan in ("tx", "ty", "rot"):
        stack = hh["channels"][chan]
        assert len(stack) == 3                       # a few sines
        for sine in stack:
            assert sine["freq"] <= 5.0               # low frequency, not white noise
    # total translate amplitude is bounded by the reserved budget
    amp = hh["amp_px"]
    for s in hh["samples"]:
        assert abs(s["tx"]) <= amp + 1e-6
        assert abs(s["ty"]) <= amp + 1e-6


def test_locked_has_micro_drift_only():
    locked = build_track(_brief(style="locked"))["track"]
    energetic = build_track(_brief(style="energetic"))["track"]
    assert locked["handheld"]["amp_px"] < energetic["handheld"]["amp_px"]
    # locked is genuinely tiny (well under a percent of the short edge)
    assert locked["handheld"]["amp_px"] < 0.01 * 1080


def test_energy_raises_handheld_speed():
    slow = build_track(_brief(style="documentary", params={"energy": 0.1}))["track"]
    fast = build_track(_brief(style="documentary", params={"energy": 0.9}))["track"]
    slow_f = max(s["freq"] for s in slow["handheld"]["channels"]["tx"])
    fast_f = max(s["freq"] for s in fast["handheld"]["channels"]["tx"])
    assert fast_f > slow_f


# ── style archetypes diverge ────────────────────────────────────────────────

def test_distinct_styles_produce_distinct_tracks():
    locked = build_track(_brief(style="locked"))["track"]
    energetic = build_track(_brief(style="energetic"))["track"]
    assert _digest(locked) != _digest(energetic)


def test_energetic_moves_more_than_locked():
    def travel(track):
        s = compose_track(track)
        return max(abs(k["tx"]) + abs(k["ty"]) for k in s) + \
            (max(k["scale"] for k in s) - min(k["scale"] for k in s)) * 1000
    e = travel(build_track(_brief(move="pan_left", style="energetic"))["track"])
    l = travel(build_track(_brief(move="pan_left", style="locked"))["track"])
    assert e > l


def test_style_alias_resolves():
    doc = build_track(_brief(style="doc"))["plan"]["style"]
    still = build_track(_brief(style="still"))["plan"]["style"]
    assert doc == "documentary"
    assert still == "locked"


# ── feedback loop ───────────────────────────────────────────────────────────

def test_more_handheld_raises_drift():
    base = _brief(style="cinematic")
    before = build_track(base)["track"]["handheld"]["amp_px"]
    res = adjust_track(base, ["more handheld"])
    after = res["track"]["handheld"]["amp_px"]
    assert after > before
    assert res["brief"]["params"]["drift"] > 0


def test_steady_lowers_drift():
    base = _brief(style="handheld")
    before = build_track(base)["track"]["handheld"]["amp_px"]
    after = adjust_track(base, ["更稳"])["track"]["handheld"]["amp_px"]
    assert after < before


def test_unknown_feedback_reported():
    res = adjust_track(_brief(), ["more banana"])
    assert any("banana" in n for n in res["notes"])


def test_adjust_keeps_same_seed():
    res = adjust_track(_brief(seed=42), ["more punchy"])
    assert res["brief"]["seed"] == 42


# ── catalog anti-drift ──────────────────────────────────────────────────────

def test_registry_catalog_no_drift():
    cam.MOVES.check_catalog()


def test_catalog_lists_real_vocab():
    catalog = camera_catalog()
    cat_moves = {e["name"] for e in catalog["moves"]}
    assert cat_moves == set(cam.move_names())
    # all six archetypes + declared aliases are present
    assert set(catalog["styles"]) == set(camera_styles().names())
    assert catalog["style_aliases"]["doc"] == "documentary"
    assert "drift" in catalog["axes"]
    # every advertised feedback word actually moves a declared axis
    vocab = feedback_vocab()
    for word in catalog["feedback_vocabulary"]:
        deltas, unknown = vocab.parse([word])
        assert deltas and not unknown, f"{word} moves nothing"


def test_describe_is_prose():
    text = describe_camera()
    assert "push_in" in text and "Moves:" in text


# ── raise / reject on unusable input ────────────────────────────────────────

def test_unknown_move_raises():
    with pytest.raises(BriefError):
        build_track(_brief(move="teleport"))


def test_unknown_style_raises():
    from lumenframe.craft import StyleError
    with pytest.raises(StyleError):
        build_track(_brief(style="vaporwave"))


def test_bad_duration_raises():
    with pytest.raises(BriefError):
        build_track(_brief(duration=0))


def test_non_dict_brief_raises():
    with pytest.raises(BriefError):
        build_track(["not", "a", "brief"])


def test_unknown_override_axis_raises():
    with pytest.raises(ValueError):
        build_track(_brief(params={"zoominess": 0.9}))


# ── render safety ───────────────────────────────────────────────────────────

def test_preview_is_render_safe():
    svg = track_to_svg(build_track(_brief())["track"])
    assert validate_camera_svg(svg) == svg
    for bad in ("url(", "data:", "<script", "xlink"):
        assert bad not in svg


def test_validate_rejects_unsafe_svg():
    bad = '<svg viewBox="0 0 10 10"><image xlink:href="http://x/y.png"/></svg>'
    with pytest.raises(ValueError):
        validate_camera_svg(bad)


def test_transform_ops_adapter_shape():
    ops = track_to_transform_ops(build_track(_brief())["track"])
    assert ops["type"] == "transform_track"
    assert ops["keyframes"] and "translate" in ops["keyframes"][0]
    assert ops["source"]["move"] == "push_in"


# ── the tool surface ────────────────────────────────────────────────────────

def test_tool_dispatch_create_adjust_catalog():
    import asyncio

    from lumenframe.camera.tool import dispatch

    created = asyncio.run(dispatch({"op": "create", "brief": _brief()}))
    assert created["applied"] and created["preview_svg"].startswith("<svg")

    adjusted = asyncio.run(dispatch(
        {"op": "adjust", "brief": _brief(), "feedback": ["more epic"]}))
    assert adjusted["applied"] and "track" in adjusted

    catalog = asyncio.run(dispatch({"op": "catalog"}))
    assert catalog["applied"] and "moves" in catalog["catalog"]

    bad = asyncio.run(dispatch({"op": "explode"}))
    assert not bad["applied"] and bad["error_code"] == "E_ARG"
