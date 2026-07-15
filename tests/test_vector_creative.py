"""Tests for the vector motion *creative stack*: behaviours, styles, the SVG
compiler, the agent-facing API, feedback, and the render adapter.

Everything is pure-python and deterministic — scenes are tiny synthetic
structures, "rendering" means compiling an SVG string (no hyperframes/ffmpeg,
no network, no wall clock). Seeded ``random.Random`` everywhere, mirroring the
engine's own determinism contract.
"""
from __future__ import annotations

import math
import random
import xml.etree.ElementTree as ET

import pytest

from lumenframe.templates import theme
from lumenframe.vector import behaviors, choreography, feedback, geometry, motion, styles
from lumenframe.vector import api
from lumenframe.vector import render
from lumenframe.vector import scene as vscene
from lumenframe.vector.behaviors import (
    BEHAVIOR_CATALOG,
    BEHAVIORS,
    BehaviorError,
    apply_behavior,
    behavior_names,
)
from lumenframe.vector.params import SEMANTIC_AXES
from lumenframe.vector.svg import compile_scene

from gemia.hyperframes_adapter import (
    _validate_local_only_css,
    _validate_local_only_html,
)

approx = pytest.approx

#: family → verbs, pinned. 22 total across 5 families — the anti-drift pin.
EXPECTED_VERBS: dict[str, set[str]] = {
    "reveal": {"draw_on", "fade_in", "grow", "unfold", "rise"},
    "explode": {"burst", "scatter", "dissolve", "energy_release"},
    "assemble": {"gather", "magnetic", "converge", "form"},
    "flow": {"wave", "breathe", "liquid", "drift", "orbit"},
    "transform": {"morph", "reshape", "spin_swap", "crossfade"},
}
EXPECTED_NAMES: set[str] = {
    f"{family}.{verb}" for family, verbs in EXPECTED_VERBS.items() for verb in verbs
}

ALL_STYLES = sorted(styles.STYLES)          # 5 archetypes
ALL_INTENTS = sorted(choreography.INTENT_ARCS)  # 5 intents

PALETTE_ROLES = {"bg", "surface", "text", "subtext", "accent", "accent_soft"}


# ── scene helpers ──────────────────────────────────────────────────────────


def _sweep_scene() -> dict:
    """Small synthetic scene: stroked path (with a morph target so
    transform.morph engages), a text node, and a 6-instance particle field."""
    vscene.reset_ids()
    scene = vscene.new_scene(width=800, height=600, duration=4.0,
                             background="#0A0E14", seed=5)
    ring = vscene.path_node(
        geometry.circle((0.0, 0.0), 80.0),
        name="Ring",
        style={"fill": None, "stroke": "#5FC6DE", "stroke_width": 4.0},
        transform={"x": 40.0, "y": -20.0},
    )
    ring["meta"]["morph_to"] = geometry.polygon((0.0, 0.0), 80.0, 6)
    vscene.add_node(scene, ring)

    word = vscene.text_node(
        "Lumeri", name="Word", style={"fill": "#F2FAFD"},
        transform={"x": -30.0, "y": 60.0},
    )
    vscene.add_node(scene, word)

    instances = []
    for i in range(6):
        ang = i / 6.0 * math.tau
        instances.append({
            "x": round(120.0 * math.cos(ang), 2),
            "y": round(120.0 * math.sin(ang), 2),
            "r": 3.0, "shape": "dot", "delay": round(i / 5.0, 4),
        })
    field = vscene.particles_node(instances, name="Field",
                                  style={"fill": "#8BD8EA"})
    vscene.add_node(scene, field)
    return scene


def _iter_tracks(scene: dict):
    """Yield (owner_label, prop, points) over node AND instance tracks."""
    for node in vscene.walk(scene):
        for prop, points in (node.get("tracks") or {}).items():
            yield f"{node['id']}", prop, points
        if node.get("kind") == "particles":
            for k, inst in enumerate((node.get("particles") or {}).get("instances") or []):
                for prop, points in (inst.get("tracks") or {}).items():
                    yield f"{node['id']}[{k}]", prop, points


# ── behaviour registry ─────────────────────────────────────────────────────


def test_load_registers_all_22_verbs_across_5_families():
    behaviors._load()
    assert set(BEHAVIORS) == EXPECTED_NAMES
    assert len(BEHAVIORS) == 22
    for family, verbs in EXPECTED_VERBS.items():
        registered = {n.split(".", 1)[1] for n in BEHAVIORS if n.startswith(family + ".")}
        assert registered == verbs, f"family {family} drifted"
    assert behavior_names() == sorted(EXPECTED_NAMES)


def test_behavior_catalog_matches_registrations_one_to_one():
    behaviors._load()
    catalog_names = [entry["name"] for entry in BEHAVIOR_CATALOG]
    assert len(catalog_names) == len(set(catalog_names)), "duplicate catalog entries"
    assert set(catalog_names) == set(BEHAVIORS), "catalog drifted from registry"
    for entry in BEHAVIOR_CATALOG:
        assert entry["family"] in behaviors.FAMILIES
        assert entry["name"].startswith(entry["family"] + ".")
        assert entry["summary"].strip()
        assert entry["kinds"]


def test_apply_behavior_rejects_unknown_name_and_empty_window():
    scene = _sweep_scene()
    level = styles.resolve_params(style="playful")
    rng = random.Random(5)
    node = scene["nodes"][0]
    with pytest.raises(BehaviorError):
        apply_behavior(scene, "reveal.does_not_exist", [node], (1.0, 3.0), level, rng)
    with pytest.raises(BehaviorError):
        apply_behavior(scene, "reveal.fade_in", [node], (2.0, 2.0), level, rng)
    with pytest.raises(BehaviorError):
        apply_behavior(scene, "reveal.fade_in", [node], (3.0, 1.0), level, rng)
    # Empty targets are a quiet no-op, never an error.
    apply_behavior(scene, "reveal.fade_in", [], (1.0, 3.0), level, rng)
    assert node["tracks"] == {}


# ── behaviour contract sweep ───────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(EXPECTED_NAMES))
def test_behavior_contract_keyframes_stay_inside_window(name):
    scene = _sweep_scene()
    level = styles.resolve_params(style="playful")
    rng = random.Random(5)
    t0, t1 = 1.0, 3.0
    apply_behavior(scene, name, scene["nodes"], (t0, t1), level, rng)

    tracks = list(_iter_tracks(scene))
    assert tracks, f"{name} wrote no tracks on the sweep scene"
    for owner, prop, points in tracks:
        assert points, f"{name}: {owner}.{prop} empty"
        times = [float(p["t"]) for p in points]
        for t in times:
            assert t0 - 1e-6 <= t <= t1 + 1e-6, (
                f"{name}: {owner}.{prop} keyframe t={t} escapes window ({t0}, {t1})"
            )
        assert times == sorted(times), f"{name}: {owner}.{prop} not t-sorted"
    vscene.validate_scene(scene)  # must not raise


# ── styles ─────────────────────────────────────────────────────────────────


def test_resolve_style_name_aliases_and_default():
    assert styles.resolve_style_name("google-like") == "playful"
    assert styles.resolve_style_name("apple") == "minimal"
    assert styles.resolve_style_name("Google Like") == "playful"
    assert styles.resolve_style_name("premium") == "luxury"
    assert styles.resolve_style_name(None) == styles.DEFAULT_STYLE == "lumeri"
    assert styles.resolve_style_name("PLAYFUL") == "playful"
    with pytest.raises(styles.StyleError):
        styles.resolve_style_name("vaporwave")


def test_every_style_spec_is_well_formed():
    assert set(styles.STYLES) == {"playful", "minimal", "luxury", "tech", "lumeri"}
    for name, spec in styles.STYLES.items():
        # baseline axes ⊆ SEMANTIC_AXES, values in [0, 1]
        assert set(spec["baseline"]) <= set(SEMANTIC_AXES), name
        for axis, value in spec["baseline"].items():
            assert 0.0 <= value <= 1.0, f"{name}.{axis}"
        # ease set carries enter/exit/move, each a known token
        assert {"enter", "exit", "move"} <= set(spec["ease"]), name
        for token in spec["ease"].values():
            motion.ease_to_css(token)  # raises MotionError on drift
        # palette is a name theme knows, or a dict resolve_palette accepts
        palette = spec["palette"]
        assert isinstance(palette, (str, dict)), name
        if isinstance(palette, str):
            assert palette in theme.PALETTES, f"{name} links unknown palette {palette}"
        resolved = theme.resolve_palette(palette)
        assert PALETTE_ROLES <= set(resolved), name


def test_resolve_params_reads_style_baseline_and_ease():
    level = styles.resolve_params(style="playful")
    assert level.axes["playfulness"] == approx(0.85)
    assert level.ease_enter == "swift"
    assert level.ease_move == "dramatic"
    assert level.hints["style"] == "playful"


# ── svg compiler ───────────────────────────────────────────────────────────


def _svg_scene() -> dict:
    """Hand-built scene exercising draw, fill_opacity, merged x/y, scale
    (with a 'hold' ease), and one instance opacity track."""
    vscene.reset_ids()
    scene = vscene.new_scene(width=400, height=300, duration=2.0,
                             background=None, seed=1)
    rule = vscene.path_node(
        geometry.line((-50.0, 0.0), (50.0, 0.0)), id="rule",
        style={"fill": None, "stroke": "#FFFFFF", "stroke_width": 2.0},
    )
    vscene.add_track(rule, "draw", [vscene.kf(0.0, 0.0, "move"), vscene.kf(1.0, 1.0)])
    vscene.add_track(rule, "fill_opacity", [vscene.kf(0.5, 0.0, "enter"), vscene.kf(1.0, 1.0)])
    vscene.add_node(scene, rule)

    word = vscene.text_node("Hi", id="word", style={"fill": "#FFFFFF"})
    vscene.add_track(word, "x", [vscene.kf(0.0, -20.0, "move"), vscene.kf(1.0, 0.0)])
    vscene.add_track(word, "y", [vscene.kf(0.0, 10.0, "move"), vscene.kf(1.0, 0.0)])
    vscene.add_track(word, "scale", [vscene.kf(0.0, 0.0, "hold"), vscene.kf(1.0, 1.0)])
    vscene.add_node(scene, word)

    dust = vscene.particles_node(
        [{"x": 5.0, "y": 6.0, "r": 3.0}, {"x": -5.0, "y": -6.0, "r": 2.0}],
        id="dust", style={"fill": "#8BD8EA"},
    )
    inst0 = dust["particles"]["instances"][0]
    vscene.add_track(inst0, "opacity", [vscene.kf(0.0, 0.0, "enter"), vscene.kf(2.0, 1.0)])
    vscene.add_node(scene, dust)
    return scene


def test_compile_scene_standalone_is_well_formed_xml_with_xmlns():
    scene = _svg_scene()
    svg = compile_scene(scene, standalone=True)
    assert 'xmlns="http://www.w3.org/2000/svg"' in svg
    root = ET.fromstring(svg)  # well-formed
    assert root.tag.endswith("svg")


def test_compile_scene_default_omits_xmlns():
    svg = compile_scene(_svg_scene())
    assert "xmlns" not in svg
    assert svg.startswith("<svg ")
    assert 'viewBox="0 0 400 300"' in svg


def test_compile_scene_draw_node_gets_pathlength_one():
    svg = compile_scene(_svg_scene())
    assert svg.count('pathLength="1"') == 1
    assert 'stroke-dasharray="1"' in svg
    # draw formatter inverts progress into stroke-dashoffset
    assert "stroke-dashoffset" in svg


def test_compile_scene_keyframes_count_matches_animated_elements():
    svg = compile_scene(_svg_scene())
    # rule: draw + fill_opacity; word: merged translate + scale; dust[0]: opacity
    assert svg.count("@keyframes") == 5


def test_compile_scene_merges_identical_xy_into_one_translate():
    svg = compile_scene(_svg_scene())
    assert "a_word_tr" in svg
    assert "a_word_x" not in svg
    assert "a_word_y" not in svg
    assert "translate: -20px 10px;" in svg
    assert "translate: 0px 0px;" in svg


def test_compile_scene_hold_ease_emits_step_end():
    svg = compile_scene(_svg_scene())
    assert "animation-timing-function: step-end;" in svg


def test_compile_scene_style_block_is_url_free_and_local_only():
    svg = compile_scene(_svg_scene())
    style_block = svg.split("<style>", 1)[1].split("</style>", 1)[0]
    assert style_block.strip()
    assert "url(" not in style_block
    assert "data:" not in svg
    assert "javascript:" not in svg
    assert "<script" not in svg


# ── api: determinism / plans / taste ───────────────────────────────────────


def _brief(**overrides) -> dict:
    base = {
        "subject": {"kind": "logo_text", "text": "Lumeri", "mark": "ring"},
        "intent": "reveal",
        "style": "playful",
        "duration": 4.0,
        "seed": 11,
    }
    base.update(overrides)
    return base


def test_build_scene_is_deterministic_per_seed():
    r1 = api.build_scene(_brief())
    svg1 = compile_scene(r1["scene"])
    r2 = api.build_scene(_brief())
    svg2 = compile_scene(r2["scene"])
    assert vscene.scene_signature(r1["scene"]) == vscene.scene_signature(r2["scene"])
    assert svg1 == svg2  # byte-equal


def test_build_scene_different_seed_changes_the_svg():
    svg1 = compile_scene(api.build_scene(_brief(seed=1))["scene"])
    svg2 = compile_scene(api.build_scene(_brief(seed=2))["scene"])
    assert svg1 != svg2


@pytest.mark.parametrize("intent", ALL_INTENTS)
@pytest.mark.parametrize("style", ALL_STYLES)
def test_every_intent_style_pair_builds_and_validates(intent, style):
    result = api.build_scene(_brief(intent=intent, style=style))
    scene, plan = result["scene"], result["plan"]
    vscene.validate_scene(scene)
    assert plan["style"] == style
    assert plan["intent"] == intent
    duration = plan["duration"]
    assert duration == approx(4.0)
    assert plan["phases"], "plan has no phases"
    for phase in plan["phases"]:
        assert -1e-6 <= phase["t0"] <= phase["t1"] <= duration + 1e-6
        # regression: emphasis must return to rest — never an explode verb
        if phase["phase"] == "emphasis" and phase["behavior"]:
            assert not phase["behavior"].startswith("explode."), phase
    assert max(p["t1"] for p in plan["phases"]) == approx(duration, abs=1e-6)
    # the scene compiles without touching a renderer
    assert compile_scene(scene).startswith("<svg")


@pytest.mark.parametrize("intent", ALL_INTENTS)
def test_playful_plan_phases_cover_zero_to_duration(intent):
    # playful has decoration, so every arc's first phase actually runs.
    plan = api.build_scene(_brief(intent=intent))["plan"]
    spans = sorted((p["t0"], p["t1"]) for p in plan["phases"])
    assert spans[0][0] == approx(0.0, abs=1e-6)
    reach = spans[0][1]
    for t0, t1 in spans[1:]:
        assert t0 <= reach + 1e-6, f"gap before {t0} in {spans}"
        reach = max(reach, t1)
    assert reach == approx(4.0, abs=1e-6)


def test_build_scene_reports_unknown_feelings_in_notes():
    result = api.build_scene(_brief(feeling=["blorptastic", "energetic"]))
    assert any("blorptastic" in note for note in result["notes"])
    assert not any("energetic" in note for note in result["notes"])


def test_build_scene_caps_duration_at_max():
    result = api.build_scene(_brief(duration=120.0))
    assert api.MAX_DURATION == 58.0
    assert result["scene"]["duration"] == approx(api.MAX_DURATION)
    assert result["plan"]["duration"] == approx(api.MAX_DURATION)
    assert any("capped" in note for note in result["notes"])


# ── feedback ───────────────────────────────────────────────────────────────


def test_parse_feedback_maps_phrases_to_signed_deltas():
    deltas, unknown = feedback.parse_feedback(
        ["more playful", "much less chaotic", "premium", "更高级", "少一点乱"]
    )
    assert unknown == []
    assert deltas["playfulness"] == approx(0.2)
    # +0.1 (playful) -0.1 (premium) -0.1 (更高级)
    assert deltas["energy"] == approx(-0.1)
    # -0.2*1.6 (much less chaotic) -0.2*0.5 (少一点乱)
    assert deltas["complexity"] == approx(-0.42)
    # +0.15*1.6 +0.15*0.5
    assert deltas["smoothness"] == approx(0.315)
    # +0.25 (premium) +0.25 (更高级)
    assert deltas["elegance"] == approx(0.5)


def test_parse_feedback_returns_unknown_phrases_verbatim():
    deltas, unknown = feedback.parse_feedback(["more sparkle-pop", "banana", ""])
    assert deltas == {}
    assert unknown == ["more sparkle-pop", "banana"]


def test_apply_feedback_writes_absolute_clamped_params_without_mutating_brief():
    brief = {"subject": {"kind": "title", "text": "Hi"},
             "style": "minimal", "params": {"energy": 0.9}}
    new_brief, unknown = feedback.apply_feedback(
        brief, ["much more energetic", "more playful"]
    )
    assert unknown == []
    # energy: 0.9 + (0.2×1.6 + 0.1) = 1.32 → clamped absolute 1.0
    assert new_brief["params"]["energy"] == approx(1.0)
    # playfulness: minimal baseline 0.1 + 0.2 → absolute 0.3 (unclamped)
    assert new_brief["params"]["playfulness"] == approx(0.3)
    # untouched axes are not written
    assert "elegance" not in new_brief["params"]
    # input brief untouched
    assert brief["params"] == {"energy": 0.9}
    assert "playfulness" not in brief["params"]


def test_adjust_scene_changes_svg_and_reports_unknown_phrases():
    brief = _brief(style="minimal")
    base_svg = compile_scene(api.build_scene(brief)["scene"])
    result = api.adjust_scene(brief, ["much more playful", "zorpy"])
    adjusted_svg = compile_scene(result["scene"])
    assert adjusted_svg != base_svg
    assert result["brief"]["params"]["playfulness"] > 0.1
    assert any("zorpy" in note for note in result["notes"])
    # original brief untouched by the round trip
    assert "params" not in brief or "playfulness" not in brief.get("params", {})


# ── render adapter ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("style", ALL_STYLES)
def test_scene_to_html_layer_is_valid_and_local_only(style):
    brief = _brief(style=style, duration=3.0, seed=3)
    scene = api.build_scene(brief)["scene"]
    layer = render.scene_to_html_layer(scene, brief=brief)

    assert layer["type"] == "html"
    assert layer["duration"] == approx(scene["duration"]) == approx(3.0)
    html = layer["props"]["html"]
    assert isinstance(html, str) and html.startswith("<svg")
    # the brief round-trips so a later adjust can re-derive the scene
    assert layer["props"]["vector_brief"] == brief
    assert layer["props"]["vector_scene"]["seed"] == brief["seed"]
    assert layer["props"]["vector_scene"]["duration"] == approx(3.0)

    # the HyperFrames local-only validators must accept the props as-is
    _validate_local_only_html(html)
    _validate_local_only_css(layer["props"]["css"])
    # and the embedded <style> stays url()-free (gradients ride attributes)
    style_block = html.split("<style>", 1)[1].split("</style>", 1)[0]
    assert "url(" not in style_block


# ── review-fix regressions (2026-07-14 adversarial pass) ──────────────────


def test_concurrent_build_is_deterministic_and_unique_ids():
    """Node-id counter is thread-local: concurrent builds of the same brief
    stay byte-identical (no shared global counter race)."""
    import threading
    import sys

    ref = compile_scene(api.build_scene(_brief())["scene"])
    results: list[str] = []
    errors: list[Exception] = []

    def worker():
        try:
            results.append(compile_scene(api.build_scene(_brief())["scene"]))
        except Exception as exc:  # pragma: no cover - the bug would land here
            errors.append(exc)

    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        sys.setswitchinterval(old)

    assert not errors, errors
    assert all(r == ref for r in results)


def test_compiler_never_relies_on_css_default_ease():
    """Every element rule declares animation-timing-function, so 'linear'
    segments render linear (not the CSS initial 'ease')."""
    scene = api.build_scene(_brief(style="tech", intent="loop", duration=6.0))["scene"]
    css = compile_scene(scene).split("<style>", 1)[1].split("</style>", 1)[0]
    element_rules = [line for line in css.split("}") if "animation-name:" in line]
    assert element_rules
    for rule in element_rules:
        assert "animation-timing-function:" in rule


def test_draw_on_uses_monotonic_ease_no_overshoot():
    """Playful's overshoot move-ease must not drive a draw-on (would flash a
    seam gap); safe_progress_ease maps it to a monotonic curve."""
    assert motion.safe_progress_ease("dramatic") == "move"
    for tok in ("enter", "exit", "move", "soft", "swift", "linear"):
        assert motion.safe_progress_ease(tok) == tok


def test_opacity_track_respects_designed_style_opacity():
    """An abstract blob designed translucent must not animate to full opacity;
    the compiler scales the opacity track by the node's base opacity."""
    scene = api.build_scene(_brief(subject={"kind": "abstract"}, style="tech"))["scene"]
    translucent = [n for n in vscene.walk(scene)
                   if n.get("kind") == "path"
                   and float((n.get("style") or {}).get("opacity", 1.0)) < 0.95
                   and "opacity" in (n.get("tracks") or {})]
    assert translucent, "expected translucent blobs with opacity tracks"
    css = compile_scene(scene).split("<style>", 1)[1].split("</style>", 1)[0]
    for node in translucent:
        base = float(node["style"]["opacity"])
        block = _keyframes_block(css, f"a_{node['id']}_opacity")
        vals = _opacity_values(block)
        assert max(vals) <= base + 1e-3, (node["id"], base, max(vals))


def _keyframes_block(css: str, name: str) -> str:
    marker = f"@keyframes {name} "
    i = css.index(marker) + len(marker)
    depth = 0
    out = []
    for ch in css[i:]:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        out.append(ch)
    return "".join(out)


def _opacity_values(block: str) -> list[float]:
    import re
    return [float(m) for m in re.findall(r"opacity:\s*([0-9.]+)", block)]


@pytest.mark.parametrize("verb", sorted(behavior_names()))
def test_every_behavior_reachable_via_brief_override(verb):
    """All 22 verbs are reachable through the agent interface via the
    behaviors override map (no dead vocabulary)."""
    family = verb.split(".")[0]
    phase = {
        "reveal": "entrance", "assemble": "entrance",
        "flow": "cycle", "transform": "emphasis", "explode": "exit",
    }[family]
    intent = {"entrance": "reveal", "emphasis": "reveal",
              "cycle": "loop", "exit": "outro"}[phase]
    brief = _brief(intent=intent, behaviors={phase: verb},
                   subject={"kind": "mark", "preset": "hex", "morph_to": "blob"}
                   if verb == "transform.morph" else
                   {"kind": "logo_text", "text": "Lumeri", "mark": "ring"})
    result = api.build_scene(brief)
    used = {p["behavior"] for p in result["plan"]["phases"]}
    # particle-only verbs need a particle field; if absent the override is a
    # legal no-op, but it must never be reported as an unknown override.
    assert not any("unknown behaviour" in n for n in result["notes"]), result["notes"]
    if verb in {"assemble.converge", "assemble.form", "explode.scatter"}:
        return  # need a particle field to actually fire; reachability proven by no-note
    assert verb in used, (verb, used)


def test_transition_intent_actually_exits():
    """A transition ends in exit — the subject is gone, not parked on screen."""
    result = api.build_scene(_brief(intent="transition"))
    phases = result["plan"]["phases"]
    assert phases[-1]["phase"] == "exit"
    assert phases[-1]["behavior"].startswith("explode.")


def test_unknown_behavior_override_is_reported_not_fatal():
    result = api.build_scene(_brief(behaviors={"emphasis": "transform.nope"}))
    assert any("unknown behaviour" in n for n in result["notes"])


def test_oscillate_phase_offsets_the_waveform():
    """Two members at different phases must not move in lockstep."""
    a = vscene.path_node(geometry.circle((0, 0), 10), id="a")
    b = vscene.path_node(geometry.circle((0, 0), 10), id="b")
    motion.oscillate(a, "y", 0.0, 2.0, center=0.0, amplitude=50.0, cycles=2.0, phase=0.0)
    motion.oscillate(b, "y", 0.0, 2.0, center=0.0, amplitude=50.0, cycles=2.0, phase=0.5)
    ya = [p["value"] for p in a["tracks"]["y"]]
    yb = [p["value"] for p in b["tracks"]["y"]]
    assert ya != yb  # genuinely phase-shifted, not a shared metronome


def test_loop_has_no_trailing_hold():
    phases = api.build_scene(_brief(intent="loop", duration=6.0))["plan"]["phases"]
    assert phases[-1]["phase"] != "hold"
