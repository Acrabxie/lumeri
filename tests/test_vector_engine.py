"""Floor tests for the vector motion engine core.

Covers the five foundation modules of ``lumenframe.vector``:

* ``geometry``     — resampling / morph alignment / arc length / serialisation
* ``scene``        — VectorScene IR: tracks, validation, traversal, signature
* ``motion``       — ease tokens, duration bands, track builders
* ``params``       — semantic axis resolution + the derived mapping table
* ``choreography`` — phase windows, stagger patterns, focal roles

Pure-python, deterministic, no I/O, no rendering — mirrors the house style of
``test_lumen_time_tools.py`` (plain pytest, synthetic fixtures).
"""
from __future__ import annotations

import math
import random

import pytest

from lumenframe.vector import choreography as ch
from lumenframe.vector import geometry as geo
from lumenframe.vector import motion
from lumenframe.vector import params as pr
from lumenframe.vector import scene as sc
from lumenframe.vector.motion import MotionError
from lumenframe.vector.scene import SceneError
from lumenframe.vector.choreography import ChoreographyError


# ════════════════════════════════════════════════════════════════════════
# geometry
# ════════════════════════════════════════════════════════════════════════


def _cmds(path):
    return [seg[0] for seg in path]


def test_resample_open_path_structure_and_endpoints():
    line = geo.line((0.0, 0.0), (100.0, 0.0))
    out = geo.resample_path(line, 5)
    assert _cmds(out) == ["M"] + ["C"] * 5
    assert out[0][1:] == pytest.approx((0.0, 0.0))
    assert out[-1][-2:] == pytest.approx((100.0, 0.0))


def test_resample_closed_path_structure_and_endpoints():
    circle = geo.circle((0.0, 0.0), 50.0)  # starts/ends at (0, -50)
    out = geo.resample_path(circle, 8)
    assert _cmds(out) == ["M"] + ["C"] * 8 + ["Z"]
    assert out[0][1:] == pytest.approx((0.0, -50.0))
    # last drawable segment must land back on the start point
    assert out[-2][-2:] == pytest.approx((0.0, -50.0))


def test_resample_downsample_branch_structure():
    star = geo.star((0.0, 0.0), 50.0)  # 10 drawable segments, closed
    assert len(list(geo.iter_cubics(star))) == 10
    out = geo.resample_path(star, 4)
    assert _cmds(out) == ["M"] + ["C"] * 4 + ["Z"]


def test_resample_rejects_bad_input():
    with pytest.raises(ValueError):
        geo.resample_path(geo.line((0, 0), (1, 1)), 0)
    with pytest.raises(ValueError):
        geo.resample_path([], 3)


def test_align_for_morph_mixed_open_closed():
    closed = geo.circle((0.0, 0.0), 50.0)
    open_ = geo.line((0.0, 0.0), (100.0, 0.0))
    ra, rb = geo.align_for_morph(closed, open_)
    assert _cmds(ra) == _cmds(rb)
    # mixed closedness → both treated as open (no Z on either side)
    assert not geo.is_closed(ra) and not geo.is_closed(rb)


def test_align_for_morph_two_closed_shapes():
    a = geo.rect((0.0, 0.0), 100.0, 60.0)  # 4 drawable segments
    b = geo.star((0.0, 0.0), 50.0)         # 10 drawable segments
    ra, rb = geo.align_for_morph(a, b)
    assert _cmds(ra) == _cmds(rb) == ["M"] + ["C"] * 10 + ["Z"]


def test_path_length_of_circle_close_to_circumference():
    for r in (10.0, 50.0, 200.0):
        L = geo.path_length(geo.circle((3.0, -7.0), r))
        assert abs(L - 2.0 * math.pi * r) / (2.0 * math.pi * r) < 0.01


def test_point_at_hits_endpoints_of_open_path():
    p = geo.polyline([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)])
    assert geo.point_at(p, 0.0) == pytest.approx((0.0, 0.0))
    assert geo.point_at(p, 1.0) == pytest.approx((10.0, 10.0))
    # halfway by arc length: corner of the L-shaped polyline
    assert geo.point_at(p, 0.5) == pytest.approx((10.0, 0.0))


def test_to_svg_d_rounds_to_three_decimals():
    d = geo.to_svg_d([("M", 1.23456, 2.0), ("L", 0.1, 3.9999), ("Z",)])
    assert d == "M 1.235 2 L 0.1 4 Z"
    for token in d.replace("M", "").replace("L", "").replace("Z", "").split():
        _, _, frac = token.partition(".")
        assert len(frac) <= 3


def test_scatter_deterministic_per_seed():
    a = geo.scatter(12, random.Random(42), center=(5.0, 5.0), radius=30.0)
    b = geo.scatter(12, random.Random(42), center=(5.0, 5.0), radius=30.0)
    c = geo.scatter(12, random.Random(43), center=(5.0, 5.0), radius=30.0)
    assert a == b
    assert a != c
    assert len(a) == 12
    assert all(math.hypot(x - 5.0, y - 5.0) <= 30.0 + 1e-9 for x, y in a)


def test_blob_deterministic_per_seed():
    kw = dict(wobble=0.6, lobes=7)
    a = geo.blob((0.0, 0.0), 40.0, rng=random.Random(7), **kw)
    b = geo.blob((0.0, 0.0), 40.0, rng=random.Random(7), **kw)
    c = geo.blob((0.0, 0.0), 40.0, rng=random.Random(8), **kw)
    assert a == b
    assert a != c
    assert geo.is_closed(a)


# ════════════════════════════════════════════════════════════════════════
# scene
# ════════════════════════════════════════════════════════════════════════


def _simple_scene(duration=5.0):
    scene = sc.new_scene(duration=duration)
    node = sc.path_node(geo.circle((0, 0), 40), id="mark")
    sc.add_node(scene, node)
    return scene, node


def test_add_track_sorts_and_replaces_same_t():
    _, node = _simple_scene()
    sc.add_track(node, "opacity", [sc.kf(1.0, 0.5), sc.kf(0.0, 0.0, "enter")])
    track = node["tracks"]["opacity"]
    assert [p["t"] for p in track] == [0.0, 1.0]

    # a keyframe landing on an existing t replaces it (no duplicates)
    sc.add_track(node, "opacity", [sc.kf(1.0, 0.9)])
    track = node["tracks"]["opacity"]
    assert [p["t"] for p in track] == [0.0, 1.0]
    assert track[-1]["value"] == 0.9


def test_add_track_rejects_unknown_prop():
    _, node = _simple_scene()
    with pytest.raises(ValueError):
        sc.add_track(node, "wobble", [sc.kf(0.0, 1.0)])


def test_validate_scene_accepts_wellformed_scene():
    scene, node = _simple_scene()
    sc.add_track(node, "opacity", [sc.kf(0.0, 0.0), sc.kf(1.0, 1.0)])
    sc.validate_scene(scene)  # must not raise


def test_validate_scene_rejects_unknown_kind():
    scene, node = _simple_scene()
    node["kind"] = "widget"
    with pytest.raises(SceneError, match="unknown kind"):
        sc.validate_scene(scene)


def test_validate_scene_rejects_duplicate_ids():
    scene, _ = _simple_scene()
    sc.add_node(scene, sc.path_node(geo.rect((0, 0), 10, 10), id="mark"))
    with pytest.raises(SceneError, match="duplicate node id"):
        sc.validate_scene(scene)


def test_validate_scene_rejects_keyframe_outside_window():
    scene, node = _simple_scene(duration=5.0)
    sc.add_track(node, "opacity", [sc.kf(0.0, 0.0), sc.kf(99.0, 1.0)])
    with pytest.raises(SceneError, match="outside"):
        sc.validate_scene(scene)


def test_validate_scene_rejects_unknown_track_prop():
    scene, node = _simple_scene()
    node["tracks"]["bogus"] = [sc.kf(0.0, 1.0)]  # bypass add_track's guard
    with pytest.raises(SceneError, match="unknown track prop"):
        sc.validate_scene(scene)


def test_validate_scene_rejects_empty_track():
    scene, node = _simple_scene()
    node["tracks"]["opacity"] = []
    with pytest.raises(SceneError, match="empty track"):
        sc.validate_scene(scene)


def test_walk_covers_group_children():
    scene = sc.new_scene()
    leaf_a = sc.path_node(geo.circle((0, 0), 5), id="leaf_a")
    leaf_b = sc.text_node("hi", id="leaf_b")
    group = sc.group_node([leaf_a, leaf_b], id="grp")
    top = sc.path_node(geo.rect((0, 0), 10, 10), id="top")
    sc.add_node(scene, group)
    sc.add_node(scene, top)

    ids = [n["id"] for n in sc.walk(scene)]
    assert ids == ["grp", "leaf_a", "leaf_b", "top"]  # group before children
    assert sc.find_node(scene, "leaf_b") is leaf_b


def test_scene_signature_stable_across_key_order():
    scene, node = _simple_scene()
    sc.add_track(node, "opacity", [sc.kf(0.0, 0.0), sc.kf(1.0, 1.0)])

    def reorder(obj):
        if isinstance(obj, dict):
            return {k: reorder(obj[k]) for k in reversed(list(obj))}
        if isinstance(obj, list):
            return [reorder(v) for v in obj]
        return obj

    shuffled = reorder(scene)
    assert list(shuffled) != list(scene)  # genuinely different insertion order
    assert sc.scene_signature(shuffled) == sc.scene_signature(scene)


# ════════════════════════════════════════════════════════════════════════
# motion
# ════════════════════════════════════════════════════════════════════════


def test_ease_value_endpoints_and_monotonicity():
    for token in ("enter", "exit", "move", "soft", "swift", "linear"):
        assert motion.ease_value(token, 0.0) == pytest.approx(0.0, abs=1e-4)
        assert motion.ease_value(token, 1.0) == pytest.approx(1.0, abs=1e-4)
        samples = [motion.ease_value(token, i / 20) for i in range(21)]
        for a, b in zip(samples, samples[1:]):
            assert b >= a - 1e-6, f"{token} not monotone"
    # special cases
    assert motion.ease_value("linear", 0.37) == pytest.approx(0.37)
    assert motion.ease_value("hold", 0.999) == 0.0
    assert motion.ease_value("hold", 1.0) == 1.0


def test_ease_to_css_known_tokens():
    assert motion.ease_to_css("enter") == "cubic-bezier(0, 0, 0.2, 1)"
    assert motion.ease_to_css("exit") == "cubic-bezier(0.4, 0, 1, 1)"
    assert motion.ease_to_css("move") == "cubic-bezier(0.4, 0, 0.2, 1)"
    assert motion.ease_to_css("dramatic") == "cubic-bezier(0.34, 1.56, 0.64, 1)"
    assert motion.ease_to_css("linear") == "linear"
    assert motion.ease_to_css("hold") == "step-end"


def test_bezier_literal_parses_and_clamps_x():
    pts = motion.ease_control_points("bezier(1.5, .2, -0.3, 0.9)")
    assert pts == (1.0, 0.2, 0.0, 0.9)  # x clamped into [0,1], y untouched
    assert motion.ease_to_css("bezier(1.5,0.2,-0.3,0.9)") == "cubic-bezier(1, 0.2, 0, 0.9)"
    # y may overshoot (dramatic-style curves)
    assert motion.ease_control_points("bezier(0.3, 1.8, 0.6, 1)") == (0.3, 1.8, 0.6, 1.0)


def test_unknown_ease_token_raises():
    with pytest.raises(MotionError):
        motion.ease_control_points("bouncy")
    with pytest.raises(MotionError):
        motion.ease_value("bezier(oops)", 0.5)


def test_band_duration_clamps_to_band_and_available():
    spec = motion.DURATION_BANDS["enter"]
    assert motion.band_duration("enter") == pytest.approx(spec["rec"])
    assert motion.band_duration("enter", tempo=0.1) == pytest.approx(spec["min"])
    assert motion.band_duration("enter", tempo=10.0) == pytest.approx(spec["max"])
    # available caps the result inside the behaviour's window
    assert motion.band_duration("enter", tempo=10.0, available=0.5) == pytest.approx(0.5)
    # available floors at 0.05 so motion never degenerates to zero
    assert motion.band_duration("enter", available=0.01) == pytest.approx(0.05)


def test_unknown_band_raises():
    with pytest.raises(MotionError):
        motion.band_duration("warp")


def test_oscillate_ends_at_center_and_stays_in_window():
    _, node = _simple_scene()
    motion.oscillate(node, "y", 1.0, 3.0, center=10.0, amplitude=6.0, cycles=2)
    track = node["tracks"]["y"]
    times = [p["t"] for p in track]
    values = [p["value"] for p in track]
    assert times == sorted(times)
    assert min(times) == pytest.approx(1.0) and max(times) == pytest.approx(3.0)
    assert values[0] == pytest.approx(10.0)
    assert values[-1] == pytest.approx(10.0)
    assert all(10.0 - 6.0 - 1e-9 <= v <= 10.0 + 6.0 + 1e-9 for v in values)
    assert max(values) > 10.0  # it actually oscillates
    with pytest.raises(MotionError):
        motion.oscillate(node, "y", 0.0, 1.0, center=0.0, amplitude=1.0, cycles=0)


def test_scale_pop_has_overshoot_keyframe_above_one():
    _, node = _simple_scene()
    motion.scale_pop(node, 0.0, 1.0, overshoot=0.15)
    track = node["tracks"]["scale"]
    values = [p["value"] for p in track]
    assert values[0] == pytest.approx(0.0)
    assert max(values) == pytest.approx(1.15)
    assert max(values) > 1.0
    assert values[-1] == pytest.approx(1.0)


# ════════════════════════════════════════════════════════════════════════
# params
# ════════════════════════════════════════════════════════════════════════


def test_resolve_precedence_baseline_feelings_overrides():
    p = pr.resolve(
        baseline={"energy": 0.9},
        feelings=["calm"],           # energy -0.2, smoothness +0.15
        overrides={"energy": 0.1},   # explicit number wins
    )
    assert p.axes["energy"] == pytest.approx(0.1)
    assert p.axes["smoothness"] == pytest.approx(0.65)  # feeling nudge survives


def test_resolve_clamps_axes_to_unit_interval():
    p = pr.resolve(baseline={"energy": 5.0}, overrides={"density": -3.0})
    assert p.axes["energy"] == 1.0
    assert p.axes["density"] == 0.0
    stacked = pr.resolve(feelings=["organic", "organic", "organic"])
    assert stacked.axes["organicness"] == 1.0


def test_resolve_unknown_override_axis_raises():
    with pytest.raises(ValueError, match="unknown semantic axes"):
        pr.resolve(overrides={"sparkle": 0.5})
    with pytest.raises(ValueError, match="unknown baseline axes"):
        pr.resolve(baseline={"sparkle": 0.5})


def test_resolve_unknown_feeling_collected_not_raised():
    p = pr.resolve(feelings=["sparkly", "CALM", "shimmering"])
    assert p.unknown_feelings == ("sparkly", "shimmering")
    assert p.axes["energy"] == pytest.approx(0.3)  # "CALM" matched case-insensitively


def test_derived_overshoot_suppressed_by_elegance():
    playful = pr.resolve(overrides={"playfulness": 1.0})
    both = pr.resolve(overrides={"playfulness": 1.0, "elegance": 1.0})
    assert both.overshoot < playful.overshoot
    assert both.overshoot == pytest.approx(0.35 - 0.25)  # table: p*0.35 - g*0.25
    assert playful.ease_emphasis == "dramatic"
    # elegant + non-playful → soft emphasis, never dramatic
    assert pr.resolve(overrides={"elegance": 1.0, "playfulness": 0.0}).ease_emphasis == "soft"


def test_derived_hold_fraction_grows_with_elegance():
    lo = pr.resolve(overrides={"elegance": 0.0}).hold_fraction
    mid = pr.resolve(overrides={"elegance": 0.5}).hold_fraction
    hi = pr.resolve(overrides={"elegance": 1.0}).hold_fraction
    assert lo < mid < hi
    assert lo == pytest.approx(0.08)
    assert hi == pytest.approx(0.25)


def test_derived_particle_count_capped():
    maxed = pr.resolve(overrides={"density": 1.0, "complexity": 1.0})
    assert maxed.particle_count <= pr.PARTICLE_CAP
    assert maxed.particle_count == 404  # table: 24 + 260 + 120
    # the cap itself, exercised directly (resolve() clamps axes to [0,1])
    wild = pr.ResolvedParams(axes={**pr.NEUTRAL, "density": 2.0, "complexity": 2.0})
    assert wild.particle_count == pr.PARTICLE_CAP


def test_derived_tempo_energy_quickens_elegance_slows():
    fast = pr.resolve(overrides={"energy": 1.0}).tempo
    slow = pr.resolve(overrides={"energy": 0.0}).tempo
    assert fast < slow
    assert pr.resolve(overrides={"elegance": 1.0}).tempo > pr.resolve().tempo


# ════════════════════════════════════════════════════════════════════════
# choreography
# ════════════════════════════════════════════════════════════════════════


def test_phase_windows_partition_duration_for_every_intent():
    p = pr.resolve()
    for intent in ch.INTENT_ARCS:
        windows = ch.phase_windows(duration=6.0, intent=intent, params=p)
        assert windows[0]["t0"] == 0.0
        for cur, nxt in zip(windows, windows[1:]):
            assert cur["t1"] == nxt["t0"], f"{intent}: gap between phases"
        assert windows[-1]["t1"] == 6.0
        for w in windows:
            assert w["name"] in ch.PHASE_NAMES
            assert w["t0"] < w["t1"]


def test_phase_windows_hold_presence_by_intent():
    p = pr.resolve()
    # Only reveal/intro end in a negative-space hold. loop must NOT hold (a
    # freeze would pop on every repeat); transition/outro end in exit.
    for intent in ("reveal", "intro"):
        names = [w["name"] for w in ch.phase_windows(duration=5.0, intent=intent, params=p)]
        assert names[-1] == "hold", intent
    loop = [w["name"] for w in ch.phase_windows(duration=5.0, intent="loop", params=p)]
    assert "hold" not in loop
    assert loop[-1] == "cycle"
    for intent in ("outro", "transition"):
        names = [w["name"] for w in ch.phase_windows(duration=5.0, intent=intent, params=p)]
        assert "hold" not in names, intent
        assert names[-1] == "exit", intent


def test_phase_windows_rejects_bad_input():
    p = pr.resolve()
    with pytest.raises(ChoreographyError, match="unknown intent"):
        ch.phase_windows(duration=5.0, intent="flourish", params=p)
    with pytest.raises(ChoreographyError, match="duration"):
        ch.phase_windows(duration=0.0, intent="reveal", params=p)


def test_stagger_sequential_spans_unit_interval():
    assert ch.stagger_delays(5) == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert ch.stagger_delays(1) == [0.0]
    assert ch.stagger_delays(0) == []


def test_stagger_center_out_and_edges_in_respect_positions():
    positions = [(-10.0, 0.0), (-1.0, 0.0), (0.0, 0.0), (1.0, 0.0), (10.0, 0.0)]
    co = ch.stagger_delays(5, pattern="center_out", positions=positions)
    ei = ch.stagger_delays(5, pattern="edges_in", positions=positions)
    assert sorted(co) == [0.0, 0.25, 0.5, 0.75, 1.0]  # spans [0,1]
    assert co[2] == 0.0          # centre member leads
    assert max(co[0], co[4]) == 1.0  # a far edge lands last
    assert ei[2] == 1.0          # centre lands last
    assert min(ei[0], ei[4]) == 0.0  # an edge leads
    with pytest.raises(ChoreographyError, match="positions length"):
        ch.stagger_delays(3, pattern="center_out", positions=positions)


def test_stagger_random_requires_rng_and_is_seed_deterministic():
    with pytest.raises(ChoreographyError, match="rng"):
        ch.stagger_delays(5, pattern="random")
    a = ch.stagger_delays(5, pattern="random", rng=random.Random(3))
    b = ch.stagger_delays(5, pattern="random", rng=random.Random(3))
    assert a == b
    assert sorted(a) == [0.0, 0.25, 0.5, 0.75, 1.0]
    with pytest.raises(ChoreographyError, match="unknown stagger pattern"):
        ch.stagger_delays(5, pattern="spiral")


def _role_nodes():
    return [
        sc.path_node(geo.circle((0, 0), 30), id="shape"),
        sc.text_node("subtitle", id="small_text", font_size=48),
        sc.text_node("TITLE", id="big_text", font_size=120),
        sc.particles_node([{"x": 0, "y": 0, "r": 2, "shape": "dot", "delay": 0.0}], id="dust"),
    ]


def test_assign_roles_biggest_text_focal_particles_decoration():
    nodes = _role_nodes()
    ch.assign_roles(nodes)
    roles = {n["id"]: n["meta"]["role"] for n in nodes}
    assert roles["big_text"] == "focal"
    assert roles["small_text"] == "secondary"
    assert roles["shape"] == "secondary"
    assert roles["dust"] == "decoration"


def test_assign_roles_explicit_focal_and_prestamped_kept():
    nodes = _role_nodes()
    nodes[0]["meta"]["role"] = "background"  # pre-stamped survives
    ch.assign_roles(nodes, focal_id="small_text")
    roles = {n["id"]: n["meta"]["role"] for n in nodes}
    assert roles["small_text"] == "focal"
    assert roles["shape"] == "background"
    assert roles["big_text"] == "secondary"


def test_entrance_order_focal_lands_last():
    nodes = _role_nodes()
    nodes[0]["meta"]["role"] = "background"
    ch.assign_roles(nodes)
    ordered = ch.entrance_order(nodes)
    assert ordered[-1]["id"] == "big_text"           # focal LANDS last
    assert ordered[0]["meta"]["role"] == "background"  # context first
    roles = [n["meta"]["role"] for n in ordered]
    assert roles == sorted(roles, key=["background", "decoration", "secondary", "focal"].index)
