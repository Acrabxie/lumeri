"""Kinetic typography point-library tests — the contract + the taste floor.

Covers: determinism (byte-identical per seed), the structural taste-floor
invariants (modular scale, three-channel hierarchy, TV title-safe margins,
optical leading, pace-derived stagger, no linear easing), style archetypes
diverging, feedback moving the right axis, catalog anti-drift, and the
unknown-input raisers. Render-safety of the SVG is asserted directly.
"""
from __future__ import annotations

import asyncio

import pytest

from lumenframe.craft import StyleError
from lumenframe.kinetic.api import BriefError, adjust, build
from lumenframe.kinetic.catalog import describe_kinetic, kinetic_catalog
from lumenframe.kinetic.render import RenderError, scene_to_svg, validate_svg
from lumenframe.kinetic.styles import STYLES
from lumenframe.kinetic.typography import (
    LAYOUTS,
    REVEALS,
    advance_ratio,
    leading_ratio,
    stagger_seconds,
)

ALL_LAYOUTS = ["title_card", "lower_third", "quote", "kinetic_lyric",
               "list_reveal", "caption", "credits_roll"]


def _brief(**over):
    b = {
        "text": "The Future, Today",
        "lines": ["The Future, Today", "Built in a single weekend"],
        "style": "title_hero",
        "layout": "title_card",
        "duration": 5.0,
        "canvas": {"width": 1920, "height": 1080},
        "seed": 7,
    }
    b.update(over)
    return b


# ── determinism ───────────────────────────────────────────────────────────────


def test_determinism_byte_identical_per_seed():
    a = build(_brief(feeling=["bold", "fast"], emphasis=["Future"]))
    b = build(_brief(feeling=["bold", "fast"], emphasis=["Future"]))
    assert a["svg"] == b["svg"]
    assert a["scene"]["digest"] == b["scene"]["digest"]


@pytest.mark.parametrize("layout", ALL_LAYOUTS)
def test_determinism_all_layouts(layout):
    kw = dict(layout=layout, style="broadcast",
              lines=["Ada Lovelace — Engineer", "Grace Hopper — Admiral"])
    assert build(_brief(**kw))["svg"] == build(_brief(**kw))["svg"]


def test_different_seed_same_layout_still_valid():
    # Seed only breaks ties; output stays render-safe and structured either way.
    for seed in (1, 42, 9001):
        r = build(_brief(seed=seed))
        validate_svg(r["svg"])


# ── taste floor: modular scale + three-channel hierarchy ──────────────────────


def test_modular_type_scale_and_hierarchy_contrast():
    r = build(_brief(style="title_hero"))
    runs = r["scene"]["runs"]
    title = next(x for x in runs if x["role"] == "title")
    subtitle = next(x for x in runs if x["role"] == "subtitle")
    ratio = r["scene"]["grid"]["ratio"]

    # size: at least one full modular ratio apart
    assert title["size"] / subtitle["size"] >= ratio - 1e-6
    # weight: a legible ladder (>= 100 apart)
    assert title["weight"] - subtitle["weight"] >= 100
    # colour / opacity: the third channel really differs
    assert (title["color"] != subtitle["color"]
            or abs(title["fill_opacity"] - subtitle["fill_opacity"]) >= 0.1)


def test_sizes_lie_on_the_modular_scale():
    # Every run size is base * ratio**k for some integer k (within fit-scale) —
    # i.e. log_ratio(size/base) lands near an integer OR the block was uniformly
    # fit-scaled (a single global factor preserves the *ratios* between tiers).
    r = build(_brief(style="editorial", layout="quote",
                     lines=["We shape our tools", "— M. McLuhan"]))
    runs = r["scene"]["runs"]
    sizes = sorted({x["size"] for x in runs}, reverse=True)
    ratio = r["scene"]["grid"]["ratio"]
    for big, small in zip(sizes, sizes[1:]):
        assert big / small >= ratio - 1e-6  # neighbouring tiers ≥ one ratio apart


# ── taste floor: TV title-safe margins + alignment grid ───────────────────────


def _line_extent(run, family, condensed):
    """Approximate [x0, x1] a run occupies given its anchor + estimated width."""
    adv = advance_ratio(family, condensed)
    w = len(run["text"]) * run["size"] * adv * (1.0 + max(0.0, run["tracking"]))
    if run["align"] == "middle":
        return run["x"] - w / 2, run["x"] + w / 2
    if run["align"] == "end":
        return run["x"] - w, run["x"]
    return run["x"], run["x"] + w


@pytest.mark.parametrize("layout", ALL_LAYOUTS)
def test_title_safe_margins_are_hard(layout):
    style = "broadcast"
    hints = STYLES.spec(style).hints
    r = build(_brief(layout=layout, style=style,
                     lines=["Jonathan Archer — Captain",
                            "T'Pol — Science Officer", "Trip — Engineer"],
                     text="The quick brown fox jumps"))
    scene = r["scene"]
    safe = scene["safe"]
    inset = min(scene["canvas"]["width"], scene["canvas"]["height"]) * 0.09
    assert safe["x"] >= inset - 1 and safe["y"] >= inset - 1  # ≥ 9% inset

    scrolling = scene["scroll"] is not None
    for run in scene["runs"]:
        x0, x1 = _line_extent(run, run["family"], hints["condensed"])
        assert x0 >= safe["left"] - 1.5, (layout, run["role"], x0, safe["left"])
        assert x1 <= safe["right"] + 1.5, (layout, run["role"], x1, safe["right"])
        if not scrolling:  # scrolling credits legitimately start off-frame
            assert safe["top"] - 1 <= run["y"] <= safe["bottom"] + 1


def test_alignment_grid_is_consistent():
    # A centred layout shares one x anchor; a left layout shares the safe left.
    centred = build(_brief(layout="title_card"))["scene"]
    xs = {x["x"] for x in centred["runs"]}
    assert len(xs) == 1 and abs(next(iter(xs)) - (centred["safe"]["left"]
           + centred["safe"]["right"]) / 2) < 1.0

    left = build(_brief(layout="lower_third", style="broadcast",
                        lines=["Name Here", "Some Role"]))["scene"]
    assert {x["x"] for x in left["runs"]} == {left["safe"]["left"]}


def test_long_copy_scales_to_fit_never_overflows():
    long_line = "Extraordinarily verbose headline that would obviously overflow"
    r = build(_brief(text=long_line, lines=[long_line], layout="title_card",
                     canvas={"width": 1280, "height": 720}))
    scene = r["scene"]
    hints = STYLES.spec(scene["style"]).hints
    for run in scene["runs"]:
        _, x1 = _line_extent(run, run["family"], hints["condensed"])
        x0, _ = _line_extent(run, run["family"], hints["condensed"])
        assert x0 >= scene["safe"]["left"] - 1.5
        assert x1 <= scene["safe"]["right"] + 1.5


def test_credits_roll_long_pair_stays_in_safe_box():
    # Finding [1]: an off-centre credit label/value anchored at center ± gap must
    # be scaled down before it crosses a safe edge — not just measured against the
    # full safe width. Exact adversarial pair from the finding's evidence.
    long_pair = ("Executive Producer and Chief Financial Officer",
                 "Alexandra Featherstonehaugh")
    r = build(_brief(layout="credits_roll", style="broadcast",
                     text=None, lines=None, pairs=[long_pair]))
    scene = r["scene"]
    hints = STYLES.spec(scene["style"]).hints
    safe = scene["safe"]
    assert len(scene["runs"]) == 2
    for run in scene["runs"]:
        x0, x1 = _line_extent(run, run["family"], hints["condensed"])
        assert x0 >= safe["left"] - 1.5, (run["role"], x0, safe["left"])
        assert x1 <= safe["right"] + 1.5, (run["role"], x1, safe["right"])


def test_role_key_only_briefs_are_valid_copy():
    # Finding [2]: bullets (list_reveal) / pairs (credits_roll) are copy sources;
    # a brief whose only copy is the role key must build, not raise "no copy".
    lr = build({"bullets": ["Alpha", "Beta"], "layout": "list_reveal",
                "style": "title_hero"})
    assert [r for r in lr["scene"]["runs"] if r["role"] == "bullet"]
    cr = build({"pairs": [("A", "B")], "layout": "credits_roll",
                "style": "broadcast"})
    assert [r for r in cr["scene"]["runs"] if r["role"] == "credit_label"]
    # a genuinely empty brief still raises
    with pytest.raises(BriefError):
        build({"layout": "list_reveal", "style": "title_hero"})


def test_svg_injection_neutralised_and_legit_tokens_allowed():
    # Finding [3]: a hostile palette/background string cannot smuggle an event
    # handler past validate_svg — it is escaped and the on*= blocklist catches it.
    with pytest.raises(RenderError):
        build(_brief(palette={"text": '#fff" onload="alert(1)'}))
    with pytest.raises(RenderError):
        build(_brief(background='#000" onload="evil()'))
    # Finding [4]: legit titles carrying forbidden-looking letters in COPY (only
    # ever in escaped text nodes) are NOT rejected — the scan inspects structure.
    for t in ["Best price url(x) deal", "Release data: Q3",
              "Click href below", "Follow us xlink now"]:
        r = build(_brief(text=t, lines=[t]))
        validate_svg(r["svg"])  # does not raise
    # and a direct on*= handler in real markup is still caught
    with pytest.raises(RenderError):
        validate_svg('<svg viewBox="0 0 1 1"><rect onload="x()"/></svg>')


def test_inter_tier_pad_survives_to_final_layout():
    # Finding [5]: the inter-tier _pad (open air above a new tier) must reach the
    # authoritative final layout, not be dropped by the first _stack.
    from lumenframe.kinetic.typography import BASELINE_FRAC, leading_ratio
    r = build(_brief(layout="title_card", style="title_hero"))
    scene = r["scene"]
    title = next(x for x in scene["runs"] if x["role"] == "title")
    sub = next(x for x in scene["runs"] if x["role"] == "subtitle")
    h = scene["canvas"]["height"]
    density = r["plan"]["axes"]["axes"]["density"]
    lh_title = title["size"] * leading_ratio(title["size"], h, density)
    gap = sub["y"] - title["y"]
    recovered_pad = gap - lh_title - BASELINE_FRAC * (sub["size"] - title["size"])
    expected_pad = round(sub["size"] * 0.55, 2)
    assert expected_pad > 1.0                       # a real, non-trivial gap
    assert abs(recovered_pad - expected_pad) < 1.0, (recovered_pad, expected_pad)


def test_tier_ratio_survives_min_size_floor_on_tiny_canvas():
    # Finding [6]: on a tiny canvas the MIN_SIZE floor must not collapse the
    # title/subtitle modular ratio to 1.0 — the floor is applied to the block.
    for cw, ch in [(64, 64), (80, 80), (128, 72), (120, 120), (200, 120)]:
        r = build(_brief(layout="title_card", style="title_hero",
                         canvas={"width": cw, "height": ch}))
        runs = r["scene"]["runs"]
        title = next(x for x in runs if x["role"] == "title")
        sub = next(x for x in runs if x["role"] == "subtitle")
        ratio = r["scene"]["grid"]["ratio"]
        assert sub["size"] >= 14.0 - 1e-6                     # legibility floor
        assert title["size"] / sub["size"] >= ratio - 1e-6    # ratio survives


def test_cjk_wraps_and_stays_safe():
    r = build(_brief(text="未来将在一个周末之内被重新发明并且变得触手可及",
                     lines=None, layout="title_card", style="minimal"))
    validate_svg(r["svg"])
    scene = r["scene"]
    for run in scene["runs"]:
        assert scene["safe"]["top"] - 1 <= run["y"] <= scene["safe"]["bottom"] + 1


# ── taste floor: optical leading ──────────────────────────────────────────────


def test_optical_leading_shrinks_with_size():
    big = leading_ratio(180, 1080, 0.5)
    small = leading_ratio(40, 1080, 0.5)
    assert big < small  # larger type ⇒ tighter line-height ratio
    # and a denser brief tightens leading at a fixed size
    assert leading_ratio(80, 1080, 0.9) < leading_ratio(80, 1080, 0.2)


# ── taste floor: reveal rhythm from pace + non-linear easing ──────────────────


def test_stagger_derived_from_pace_not_per_call():
    slow = build(_brief(params={"pace": 0.15}))["plan"]["rhythm"]["stagger"]
    fast = build(_brief(params={"pace": 0.9}))["plan"]["rhythm"]["stagger"]
    assert slow > fast                       # faster pace ⇒ tighter stagger
    assert stagger_seconds(0.9) < stagger_seconds(0.15)


def test_reveal_easing_is_never_linear():
    r = build(_brief(style="title_hero", reveal="rise_fade"))
    assert "cubic-bezier" in r["svg"]
    assert "linear" not in r["svg"]          # a title never eases linearly


def test_per_word_reveal_cascades_with_increasing_delays():
    r = build(_brief(style="kinetic", reveal="per_word",
                     text="One Two Three Four", lines=["One Two Three Four"]))
    # each word is its own animated tspan; a title with 4 words has ≥ 4 units
    assert r["svg"].count("ktFade") >= 4
    run = r["scene"]["runs"][0]
    assert run["reveal"]["unit"] == "word" and run["reveal"]["unit_stagger"] > 0


# ── style archetypes reshape everything ───────────────────────────────────────


def test_style_archetypes_produce_distinct_output():
    editorial = build(_brief(style="editorial"))
    kinetic = build(_brief(style="kinetic"))
    assert editorial["svg"] != kinetic["svg"]
    assert 'font-family="serif"' in editorial["svg"]      # editorial ⇒ serif
    assert 'font-family="sans-serif"' in kinetic["svg"]
    # kinetic upper-cases its headings; editorial keeps the given case
    assert "TODAY" in kinetic["svg"]        # per-word tspan, upper-cased
    assert "Today" in editorial["svg"] and "TODAY" not in editorial["svg"]


def test_aliases_resolve():
    assert STYLES.resolve_name("apple") == "minimal"
    assert STYLES.resolve_name("news") == "broadcast"
    assert STYLES.resolve_name("google") == "kinetic"


# ── feedback moves the intended axis ──────────────────────────────────────────


def test_feedback_bolder_increases_weight_and_reports_unknown():
    base = build(_brief(style="minimal"))
    before_w = next(x for x in base["scene"]["runs"] if x["role"] == "title")["weight"]
    r = adjust(_brief(style="minimal"), ["bolder", "zzznope"])
    after_w = next(x for x in r["scene"]["runs"] if x["role"] == "title")["weight"]
    assert after_w >= before_w
    assert r["brief"]["params"]["weight"] > 0.4
    assert any("zzznope" in n for n in r["notes"])


def test_feedback_tighter_increases_density_bilingual():
    r = adjust(_brief(), ["更紧凑"])
    assert r["brief"]["params"]["density"] > 0.45  # 更紧凑 = tighter


def test_adjust_is_deterministic_same_seed():
    a = adjust(_brief(), ["bolder", "faster"])
    b = adjust(_brief(), ["bolder", "faster"])
    assert a["svg"] == b["svg"]


# ── catalog anti-drift ────────────────────────────────────────────────────────


def test_registry_catalog_no_drift():
    LAYOUTS.check_catalog()
    REVEALS.check_catalog()


def test_catalog_lists_real_vocabulary():
    cat = kinetic_catalog()
    assert set(cat["layouts"]) == set(LAYOUTS.names())
    assert set(cat["reveals"]) == set(REVEALS.names())
    assert set(cat["styles"]) == set(STYLES.names())
    assert set(ALL_LAYOUTS) == set(cat["layouts"])
    assert "bolder" in cat["feedback_vocabulary"]
    assert isinstance(describe_kinetic(), str) and "layouts:" in describe_kinetic()


def test_library_registered_with_spine():
    from lumenframe.craft import craft_catalog
    libs = craft_catalog()["libraries"]
    assert "kinetic_type" in libs
    assert libs["kinetic_type"]["rides"] == "html"


# ── raisers on unknown / unusable input ───────────────────────────────────────


def test_unknown_style_raises():
    with pytest.raises(StyleError):
        build(_brief(style="definitely-not-a-style"))


def test_unknown_layout_raises():
    with pytest.raises(BriefError):
        build(_brief(layout="hexagon_spiral"))


def test_empty_copy_raises():
    with pytest.raises(BriefError):
        build({"style": "title_hero", "layout": "title_card"})


def test_negative_duration_raises():
    # 0 means "unset → default" (the spine convention); a negative is unusable.
    with pytest.raises(BriefError):
        build(_brief(duration=-1))


# ── render safety ─────────────────────────────────────────────────────────────


def test_validate_rejects_unsafe_svg():
    with pytest.raises(RenderError):
        validate_svg('<svg viewBox="0 0 1 1"><image href="x"/></svg>')
    with pytest.raises(RenderError):
        validate_svg('<svg viewBox="0 0 1 1"><rect fill="url(#x)"/></svg>')
    with pytest.raises(RenderError):
        validate_svg('<svg viewBox="0 0 1 1"><script>alert(1)</script></svg>')
    with pytest.raises(RenderError):
        validate_svg("not an svg at all")


def test_generated_svg_is_self_contained():
    for layout in ALL_LAYOUTS:
        svg = build(_brief(layout=layout, style="lyric",
                           lines=["A — 1", "B — 2"]))["svg"]
        low = svg.lower()
        for bad in ("data:", "<script", "xlink", "url("):
            assert bad not in low
        assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")


def test_duration_capped():
    r = build(_brief(duration=120))
    assert r["scene"]["duration"] <= 58.0
    assert any("capped" in n for n in r["notes"])


# ── the single tool surface ───────────────────────────────────────────────────


def test_tool_dispatch_ops():
    from lumenframe.kinetic.tool import dispatch

    created = asyncio.run(dispatch({"op": "create", "brief": _brief()}))
    assert created["applied"] and created["svg"].startswith("<svg")

    adjusted = asyncio.run(dispatch(
        {"op": "adjust", "brief": _brief(), "feedback": ["bolder"]}))
    assert adjusted["applied"] and "brief" in adjusted

    cat = asyncio.run(dispatch({"op": "catalog"}))
    assert cat["applied"] and "layouts" in cat["catalog"]

    bad = asyncio.run(dispatch({"op": "sculpt"}))
    assert bad["applied"] is False and bad["error_code"] == "E_ARG"

    missing = asyncio.run(dispatch({"op": "create"}))
    assert missing["applied"] is False
