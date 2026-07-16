"""Grade point-library tests — the contract + the taste floor, enforced.

Covers every clause of the point-library contract for :mod:`lumenframe.grade`:
determinism, the structural taste-floor invariants (protected S-curve, enforced
complementary split, saturation ceiling, skin-tone protection, safe clips), the
one-word style archetypes, the feedback loop, catalog anti-drift, and the
raises-on-unknown-input behaviour.
"""
from __future__ import annotations

import math

import pytest

from lumenframe.craft import stable_digest
from lumenframe.craft.styles import StyleError

from lumenframe.grade import grade as G
from lumenframe.grade.api import BriefError, adjust_grade, build_grade
from lumenframe.grade.catalog import describe_grade, grade_catalog
from lumenframe.grade.grade import (
    BLACK_POINT_SAFE_MAX,
    REGISTRY,
    SAT_CEILING,
    SKIN_TOLERANCE_DEG,
    WHITE_POINT_SAFE_MIN,
    tone_curve_samples,
)
from lumenframe.grade.render import (
    grade_ffmpeg_filter,
    grade_preview_svg,
    validate_grade_recipe,
    validate_grade_svg,
)
from lumenframe.grade.styles import STYLES

CINEMATIC_LOOKS = ["teal_orange", "film", "bleach_bypass", "pastel", "vintage", "clean"]
STYLISED_LOOKS = ["noir", "day_for_night", "cyberpunk"]
ALL_LOOKS = ["neutral"] + CINEMATIC_LOOKS + STYLISED_LOOKS


# ── 1. determinism ──────────────────────────────────────────────────────────


def test_same_brief_same_seed_is_byte_identical():
    brief = {"look": "teal_orange", "feeling": ["moody", "faded"],
             "intensity": 0.85, "seed": 42}
    a, b = build_grade(brief), build_grade(dict(brief))
    assert a["preview_svg"] == b["preview_svg"]
    assert a["ffmpeg_filter"] == b["ffmpeg_filter"]
    assert stable_digest(a["recipe"]) == stable_digest(b["recipe"])
    assert a["plan"]["digest"] == b["plan"]["digest"]


def test_seed_changes_only_the_grain_field():
    """A different seed reshuffles the grain field but nothing tonal."""
    r1 = build_grade({"look": "film", "seed": 1})["recipe"]
    r2 = build_grade({"look": "film", "seed": 2})["recipe"]
    assert r1["grain_field"] != r2["grain_field"]
    assert r1["contrast"] == r2["contrast"]
    assert r1["saturation"] == r2["saturation"]
    assert r1["lift"] == r2["lift"]


# ── 2. taste-floor invariants ───────────────────────────────────────────────


def test_tone_curve_is_monotonic_for_every_look():
    for look in ALL_LOOKS:
        recipe = build_grade({"look": look, "seed": 5})["recipe"]
        curve = tone_curve_samples(recipe, n=33)
        for lo, hi in zip(curve, curve[1:]):
            assert hi >= lo - 1e-9, f"{look}: tone curve not monotonic"


def test_scurve_protects_toe_and_shoulder():
    """A high-contrast look steepens the mid-tones but flattens the extremes,
    and never clips the endpoints (that is toe/shoulder protection)."""
    recipe = build_grade({"look": "clean", "feeling": ["punchy", "contrasty"],
                          "seed": 7})["recipe"]
    curve = tone_curve_samples(recipe, n=41)
    assert recipe["contrast"]["amount"] > 0.1  # genuinely contrasty
    # endpoints not crushed (clean is not an allow_clip look).
    assert curve[0] <= 0.02 and curve[-1] >= 0.98
    n = len(curve)
    toe = curve[1] - curve[0]
    shoulder = curve[-1] - curve[-2]
    mid = curve[n // 2 + 1] - curve[n // 2]
    assert mid > toe and mid > shoulder, "mid-slope must exceed toe/shoulder slope"


def test_allow_clip_look_may_crush_blacks():
    """Noir *demands* crushed shadows — it may exceed the safe black band."""
    recipe = build_grade({"look": "noir", "seed": 3})["recipe"]
    assert recipe["black_point"] > BLACK_POINT_SAFE_MAX
    assert recipe["white_point"] < WHITE_POINT_SAFE_MIN


def test_non_stylised_looks_keep_safe_black_and_white_points():
    for look in ["neutral"] + CINEMATIC_LOOKS:
        recipe = build_grade({"look": look, "feeling": ["punchy", "moody"],
                              "seed": 4})["recipe"]
        assert recipe["black_point"] <= BLACK_POINT_SAFE_MAX + 1e-6, look
        assert recipe["white_point"] >= WHITE_POINT_SAFE_MIN - 1e-6, look


def test_cinematic_split_is_complementary():
    """Every cinematic look must split shadows/highlights ~180° apart."""
    for look in CINEMATIC_LOOKS:
        plan = build_grade({"look": look, "seed": 2})["plan"]
        sh, hh = plan["split"]["shadow_hue"], plan["split"]["highlight_hue"]
        assert sh is not None and hh is not None, look
        delta = G._hue_delta(sh, hh)
        assert abs(delta - 180.0) <= 25.0, f"{look}: split {sh}/{hh} not complementary"
        assert plan["split"]["complementary"] is True


def test_declared_highlight_hue_influences_the_split():
    """A cinematic look's declared highlight_hue must not be inert: snapping the
    pair complementary is centred on the circular midpoint of BOTH declared hues,
    so editing highlight_hue moves the emitted split (regression for the dead
    highlight_hue constant)."""
    from lumenframe.grade import grade as GG

    base = GG.derive_recipe(
        {a: 0.5 for a in ("warmth", "contrast", "saturation", "lift", "drama", "filmic")},
        {"cinematic": True, "shadow_hue": 200.0, "highlight_hue": 30.0},
        intensity=1.0, rng=__import__("random").Random(1),
    )[0]
    moved = GG.derive_recipe(
        {a: 0.5 for a in ("warmth", "contrast", "saturation", "lift", "drama", "filmic")},
        {"cinematic": True, "shadow_hue": 200.0, "highlight_hue": 60.0},
        intensity=1.0, rng=__import__("random").Random(1),
    )[0]
    assert base["shadow_hue"] != moved["shadow_hue"], "highlight_hue must affect the snap"
    assert base["highlight_hue"] != moved["highlight_hue"]
    # the guarantee still holds: the snapped pair is EXACTLY complementary.
    for r in (base, moved):
        assert abs(G._hue_delta(r["shadow_hue"], r["highlight_hue"]) - 180.0) <= 1e-6


def test_vibrance_is_an_active_low_saturation_weighted_boost():
    """Vibrance is not a no-op: it lifts the chroma of muted colour while sparing
    already-saturated pixels, and it never rotates hue (skin-safe)."""
    recipe = build_grade({"look": "clean", "params": {"saturation": 1.0}, "seed": 1})["recipe"]
    assert recipe["vibrance"] > 0.0
    muted = (0.52, 0.5, 0.48)   # nearly grey → should gain chroma
    on = G.apply_recipe_rgb(recipe, muted)
    off_recipe = dict(recipe, vibrance=0.0)
    off = G.apply_recipe_rgb(off_recipe, muted)
    spread_on = max(on) - min(on)
    spread_off = max(off) - min(off)
    assert spread_on > spread_off + 1e-6, "vibrance must boost muted chroma"
    # hue is preserved (vibrance is a luma-uniform scale → skin-safe).
    assert G._rgb_hue(on) == pytest.approx(G._rgb_hue(off), abs=0.5)


def test_gamma_wheel_is_applied_when_set():
    """The gamma wheel defaults to identity (no axis drives it), but it is a real
    part of the tone path — when set it is honoured per channel and preserves the
    endpoints (so it is a genuine, if reserved, wheel, not dead code)."""
    assert build_grade({"look": "neutral", "seed": 1})["recipe"]["gamma"] == \
        {"r": 1.0, "g": 1.0, "b": 1.0}  # honest identity default
    recipe = G.neutral_recipe()  # identity tone path, no faded floor
    lit = dict(recipe, gamma={"r": 2.0, "g": 1.0, "b": 1.0})
    assert G.tone_channel(0.5, lit, "r") != G.tone_channel(0.5, recipe, "r")  # branch fires
    assert G.tone_channel(0.5, lit, "g") == G.tone_channel(0.5, recipe, "g")  # other channels identity
    assert G.tone_channel(0.0, lit, "r") == pytest.approx(0.0, abs=1e-9)  # endpoints pinned
    assert G.tone_channel(1.0, lit, "r") == pytest.approx(1.0, abs=1e-9)


def test_saturation_ceiling_cannot_be_exceeded():
    """No stack of feelings/overrides can produce radioactive colour."""
    recipe = build_grade({
        "look": "cyberpunk",
        "feeling": ["vibrant", "punchy", "vibrant"],
        "params": {"saturation": 1.0},
        "seed": 1,
    })["recipe"]
    assert recipe["saturation"] <= SAT_CEILING
    validate_grade_recipe(recipe)  # physical-limit gate agrees


def test_monochrome_look_is_fully_desaturated():
    recipe = build_grade({"look": "noir", "feeling": ["vibrant"], "seed": 1})["recipe"]
    assert recipe["saturation"] == 0.0
    assert recipe["shadow_hue"] is None and recipe["highlight_hue"] is None


def test_skin_tone_protected_for_all_non_stylised_looks():
    """Skin hue may not drift past tolerance for any non-stylised look, even
    when the brief pushes hard toward a colour cast — including via the tone
    dials (contrast/saturation/drama), which rotate skin hue on their own and
    which earlier protection never touched (regression for the faked floor)."""
    for look in ["neutral"] + CINEMATIC_LOOKS:
        # warmth/teal cast only (the dials protection can trivially correct)
        cast = build_grade({
            "look": look,
            "feeling": ["warm", "teal", "moody"],
            "params": {"warmth": 0.95},
            "seed": 8,
        })["recipe"]
        assert G.skin_drift(cast) <= SKIN_TOLERANCE_DEG + 1e-6, look
        # EXACT adversarial brief from the blocker: max out every dial that
        # rotates skin hue (contrast + saturation + warmth + drama at 1.0).
        hard = build_grade({
            "look": look,
            "feeling": ["warm", "teal", "moody"],
            "params": {"contrast": 1.0, "saturation": 1.0, "warmth": 1.0, "drama": 1.0},
            "seed": 8,
        })
        assert hard["plan"]["stylised"] is False, look
        assert G.skin_drift(hard["recipe"]) <= SKIN_TOLERANCE_DEG + 1e-6, (
            f"{look}: contrast/saturation-driven skin drift breaches the floor"
        )
        # the reported drift and the note must not contradict each other.
        assert hard["plan"]["skin_drift_deg"] <= SKIN_TOLERANCE_DEG + 1e-6, look


def test_skin_protection_blocker_briefs_are_under_tolerance():
    """The three verbatim briefs the reviewer reproduced (bleach_bypass, pastel,
    teal_orange) each drifted 13–21° past the 10° floor while reporting
    skin_protected=True. The floor is now enforced in the math."""
    briefs = [
        {"look": "bleach_bypass", "params": {"contrast": 1.0, "saturation": 1.0,
                                             "warmth": 1.0, "lift": 0.0}, "seed": 1},
        {"look": "pastel", "feeling": ["warm", "teal", "moody"],
         "params": {"contrast": 1.0, "saturation": 1.0, "warmth": 1.0, "drama": 1.0}, "seed": 8},
        {"look": "teal_orange", "feeling": ["warm", "teal", "moody"],
         "params": {"warmth": 0.95}, "seed": 8},
    ]
    for brief in briefs:
        out = build_grade(brief)
        drift = out["plan"]["skin_drift_deg"]
        assert out["plan"]["stylised"] is False
        assert drift <= SKIN_TOLERANCE_DEG + 1e-6, f"{brief['look']}: drift {drift}"
        # if the reassuring note fired, the payload must actually back it up.
        reassure = [n for n in out["notes"] if "keep drift" in n]
        if reassure:
            assert drift <= SKIN_TOLERANCE_DEG + 1e-6


def test_stylised_looks_are_allowed_to_drift_skin():
    """The SAME aggressive cool/teal cast is clamped under tolerance on a
    non-stylised look, but runs free on a stylised one (blue moonlit faces are
    the whole point of day-for-night) — protection is off there by design."""
    cast = {"feeling": ["cool", "teal", "moody"], "params": {"warmth": 0.02}, "seed": 1}
    non_stylised = build_grade({"look": "neutral", **cast})["recipe"]
    stylised = build_grade({"look": "day_for_night", **cast})["recipe"]
    assert G.skin_drift(non_stylised) <= SKIN_TOLERANCE_DEG + 1e-6
    assert G.skin_drift(stylised) > SKIN_TOLERANCE_DEG
    assert build_grade({"look": "day_for_night", **cast})["plan"]["skin_protected"] is False


def test_intensity_scales_toward_neutral():
    """intensity=0 collapses the grade to (near) identity; 1 is the full look."""
    zero = build_grade({"look": "teal_orange", "intensity": 0.0, "seed": 1})["recipe"]
    full = build_grade({"look": "teal_orange", "intensity": 1.0, "seed": 1})["recipe"]
    assert zero["temperature"] == pytest.approx(0.0, abs=1e-6)
    assert zero["saturation"] == pytest.approx(1.0, abs=1e-6)
    assert zero["vignette"] == pytest.approx(0.0, abs=1e-6)
    assert zero["contrast"]["amount"] == pytest.approx(0.0, abs=1e-6)
    # the full grade is genuinely stronger on multiple dials.
    assert abs(full["contrast"]["amount"]) > abs(zero["contrast"]["amount"])
    assert full["vignette"] > zero["vignette"]


def test_intensity_out_of_range_is_clamped_and_noted():
    result = build_grade({"look": "film", "intensity": 5.0, "seed": 1})
    assert result["plan"]["intensity"] == 1.0
    assert any("intensity clamped" in n for n in result["notes"])


# ── 3. style archetypes reshape everything ──────────────────────────────────


def test_distinct_looks_produce_distinct_output():
    seen: dict[str, str] = {}
    for look in ALL_LOOKS:
        digest = build_grade({"look": look, "seed": 1})["plan"]["digest"]
        assert digest not in seen.values(), f"{look} collided with {seen}"
        seen[look] = digest


def test_alias_resolves_to_its_look():
    assert build_grade({"look": "kodak", "seed": 1})["plan"]["look"] == "film"
    assert build_grade({"look": "bw", "seed": 1})["plan"]["look"] == "noir"
    assert build_grade({"look": "blockbuster", "seed": 1})["plan"]["look"] == "teal_orange"


def test_style_key_accepts_look_or_style_field():
    a = build_grade({"look": "vintage", "seed": 1})["plan"]["digest"]
    b = build_grade({"style": "vintage", "seed": 1})["plan"]["digest"]
    assert a == b


# ── 4. feedback loop ────────────────────────────────────────────────────────


def test_more_teal_cools_the_grade():
    base = build_grade({"look": "neutral", "seed": 1})["recipe"]
    adjusted = adjust_grade({"look": "neutral", "seed": 1}, ["more teal", "more moody"])
    assert adjusted["brief"]["params"]["warmth"] < 0.5          # cooled
    assert adjusted["recipe"]["temperature"] < base["temperature"]
    assert adjusted["recipe"]["vignette"] > base["vignette"]    # moodier
    assert "warmth" in adjusted["brief"]["params"]


def test_more_filmic_adds_grain_and_halation():
    base = build_grade({"look": "clean", "seed": 1})["recipe"]
    adjusted = adjust_grade({"look": "clean", "seed": 1}, ["much more filmic"])
    assert adjusted["recipe"]["grain"] > base["grain"]
    assert adjusted["recipe"]["halation"] > base["halation"]


def test_unknown_feedback_is_reported_not_fatal():
    result = adjust_grade({"look": "neutral", "seed": 1}, ["more banana", "more teal"])
    assert any("banana" in n for n in result["notes"])
    # the recognised half still applied.
    assert result["brief"]["params"].get("warmth", 0.5) < 0.5


def test_adjust_reuses_the_same_seed():
    result = adjust_grade({"look": "film", "seed": 99}, ["more warm"])
    assert result["brief"]["seed"] == 99


# ── 5. catalog anti-drift ───────────────────────────────────────────────────


def test_registry_catalog_has_no_drift():
    REGISTRY.check_catalog()


def test_catalog_lists_the_real_vocabulary():
    cat = grade_catalog()
    assert set(cat["looks"]) == set(STYLES.names())
    op_names = {e["name"] for e in cat["ops"]}
    assert op_names == set(REGISTRY.names())
    # every advertised op is on the actual pipeline.
    assert set(cat["pipeline"]) == op_names
    # feedback vocabulary is real (every word moves a declared axis).
    assert "teal" in cat["feedback_vocabulary"]
    assert isinstance(describe_grade(), str) and "grade looks" in describe_grade()


# ── 6. raises on unknown input ──────────────────────────────────────────────


def test_unknown_look_raises():
    with pytest.raises(StyleError):
        build_grade({"look": "chartreuse_dream", "seed": 1})


def test_unknown_override_axis_raises():
    with pytest.raises(ValueError):
        build_grade({"look": "neutral", "params": {"sharpness": 0.5}, "seed": 1})


def test_malformed_brief_raises_brieferror():
    with pytest.raises(BriefError):
        build_grade({"look": "neutral", "params": ["not", "a", "dict"]})
    with pytest.raises(BriefError):
        build_grade({"look": "neutral", "feeling": "not-a-list"})


# ── 7. render safety (riding the effect layer) ──────────────────────────────


def test_preview_svg_is_render_safe_for_every_look():
    for look in ALL_LOOKS:
        svg = build_grade({"look": look, "seed": 1})["preview_svg"]
        validate_grade_svg(svg)  # raises on anything unsafe/oversized
        assert svg.startswith("<svg") and svg.endswith("</svg>")
        low = svg.lower()
        assert "data:" not in low and "//" not in low and "<script" not in low
        assert "xlink" not in low
        assert "url(#" in low  # internal fragment refs are the only url()


def test_validate_grade_svg_rejects_unsafe_tokens():
    for bad in [
        '<svg><image href="http://x/y.png"/></svg>',
        '<svg><rect fill="url(data:image/png;base64,AAAA)"/></svg>',
        '<svg><script>steal()</script></svg>',
        '<html><svg></svg></html>',
    ]:
        with pytest.raises(ValueError):
            validate_grade_svg(bad)


def test_validate_grade_svg_rejects_oversized():
    with pytest.raises(ValueError):
        validate_grade_svg("<svg>" + "x" * 100_000 + "</svg>")


def test_ffmpeg_filter_is_a_stable_string():
    a = grade_ffmpeg_filter(build_grade({"look": "vintage", "seed": 1})["recipe"])
    b = grade_ffmpeg_filter(build_grade({"look": "vintage", "seed": 1})["recipe"])
    assert a == b
    assert a.startswith("eq=contrast=") and "colorbalance=" in a


def test_recipe_validation_catches_out_of_bounds():
    bad = build_grade({"look": "neutral", "seed": 1})["recipe"]
    bad["saturation"] = 9.0
    with pytest.raises(ValueError):
        validate_grade_recipe(bad)


# ── 8. the single tool surface ──────────────────────────────────────────────


def test_tool_dispatch_create_adjust_catalog():
    import asyncio

    from lumenframe.grade.tool import dispatch

    created = asyncio.run(dispatch({"op": "create", "brief": {"look": "film", "seed": 1}}))
    assert created["applied"] is True
    assert "recipe" in created and "preview_svg" in created

    adjusted = asyncio.run(dispatch({
        "op": "adjust", "brief": {"look": "film", "seed": 1}, "feedback": ["more warm"],
    }))
    assert adjusted["applied"] is True and "brief" in adjusted

    cat = asyncio.run(dispatch({"op": "catalog"}))
    assert cat["applied"] is True and "looks" in cat["catalog"]

    bad = asyncio.run(dispatch({"op": "nonsense"}))
    assert bad["applied"] is False and bad["error_code"] == "E_ARG"

    missing = asyncio.run(dispatch({"op": "create", "brief": "not-a-dict"}))
    assert missing["applied"] is False and missing["error_code"] == "E_ARG"
