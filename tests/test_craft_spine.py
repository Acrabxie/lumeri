"""Spine tests — the shared craft machinery every point library rides on."""
from __future__ import annotations

import pytest

from lumenframe.craft import (
    AxisSpace,
    FeedbackVocab,
    IdSeq,
    Registry,
    StyleBook,
    StyleError,
    axis_space,
    clamp01,
    new_rng,
    remap,
    stable_digest,
)


def _space() -> AxisSpace:
    return axis_space(
        ("energy", "smoothness", "warmth", "elegance"),
        {"energy": 0.5, "smoothness": 0.5, "warmth": 0.5, "elegance": 0.5},
        extra_feelings={"toasty": {"warmth": +0.3}},
    )


# ── params / axes ──────────────────────────────────────────────────────────

def test_clamp_and_remap():
    assert clamp01(-1) == 0.0 and clamp01(2) == 1.0 and clamp01(0.3) == 0.3
    assert remap(0.0, 10, 20) == 10 and remap(1.0, 10, 20) == 20 and remap(0.5, 10, 20) == 15


def test_resolution_order_baseline_feelings_overrides():
    sp = _space()
    r = sp.resolve(baseline={"warmth": 0.2}, feelings=["toasty"], overrides={"energy": 0.9})
    assert r["warmth"] == pytest.approx(0.5)   # 0.2 baseline + 0.3 toasty
    assert r["energy"] == 0.9                    # override wins
    assert r["smoothness"] == 0.5               # untouched neutral


def test_unknown_feeling_surfaced_not_fatal():
    sp = _space()
    r = sp.resolve(feelings=["toasty", "wibble"])
    assert "wibble" in r.unknown_feelings and "toasty" not in r.unknown_feelings


def test_feeling_for_undeclared_axis_is_inert():
    # "playful" nudges a "playfulness" axis this space does not declare → no-op,
    # and it is NOT reported unknown (it is a known shared feeling).
    sp = _space()
    r = sp.resolve(feelings=["playful"])
    assert r.unknown_feelings == ()
    assert set(r.values) == {"energy", "smoothness", "warmth", "elegance"}


def test_unknown_override_axis_raises():
    with pytest.raises(ValueError):
        _space().resolve(overrides={"nope": 0.5})


def test_bad_baseline_axis_raises():
    with pytest.raises(ValueError):
        _space().resolve(baseline={"nope": 0.5})


# ── styles ─────────────────────────────────────────────────────────────────

def _book() -> StyleBook:
    sp = _space()
    book = StyleBook(space=sp, default="house")
    book.add("house", "the neutral default", {"warmth": 0.5})
    book.add("warm", "cozy and toasty", {"warmth": 0.85, "energy": 0.4})
    book.alias("cozy-like", "warm")
    return book


def test_style_alias_and_default():
    book = _book()
    assert book.resolve_name(None) == "house"
    assert book.resolve_name("cozy-like") == "warm"
    assert book.resolve_name("WARM") == "warm"


def test_unknown_style_raises():
    with pytest.raises(StyleError):
        _book().resolve_name("neon")


def test_style_baseline_feeds_params():
    book = _book()
    r = book.resolve_params(style="warm")
    assert r["warmth"] == 0.85
    assert r.hints["style"] == "warm"


def test_style_with_undeclared_axis_rejected():
    book = _book()
    with pytest.raises(ValueError):
        book.add("bad", "x", {"undeclared": 0.5})


# ── feedback ───────────────────────────────────────────────────────────────

def test_feedback_more_less_and_bilingual():
    sp = _space()
    vocab = FeedbackVocab(space=sp).extend({"toasty": {"warmth": +0.25}})
    d1, u1 = vocab.parse(["more toasty"])
    assert d1["warmth"] == pytest.approx(0.25) and u1 == []
    d2, _ = vocab.parse(["less toasty"])
    assert d2["warmth"] == pytest.approx(-0.25)
    d3, _ = vocab.parse(["更暖"])
    assert d3["warmth"] == pytest.approx(0.2)


def test_feedback_apply_absolute_and_compounds():
    sp = _space()
    vocab = FeedbackVocab(space=sp)
    brief = {"params": {}}

    def resolve(b):
        return sp.resolve(overrides=b.get("params") or {})

    nb, unknown = vocab.apply(brief, ["much warmer"], resolve)  # much=×1.5, warm=+0.2
    assert unknown == []
    assert nb["params"]["warmth"] == pytest.approx(clamp01(0.5 + 0.2 * 1.5), abs=1e-4)
    # original brief untouched
    assert brief["params"] == {}


def test_feedback_unknown_or_inert_reported():
    sp = _space()
    vocab = FeedbackVocab(space=sp)
    _, unknown = vocab.parse(["more sproingy", "more playful"])  # playful → no declared axis
    assert "more sproingy" in unknown and "more playful" in unknown


# ── determinism ────────────────────────────────────────────────────────────

def test_rng_is_seeded_and_repeatable():
    assert [new_rng(7).random() for _ in range(3)] == [new_rng(7).random() for _ in range(3)]
    assert new_rng(7).random() != new_rng(8).random()


def test_id_seq_resets_and_increments():
    ids = IdSeq("clip")
    ids.reset()
    assert ids.next() == "clip_0001"
    assert ids.next("cut") == "cut_0002"
    ids.reset()
    assert ids.next() == "clip_0001"


def test_stable_digest_is_process_independent_and_order_free():
    a = stable_digest({"b": 1, "a": [1, 2, 3]})
    b = stable_digest({"a": [1, 2, 3], "b": 1})
    assert a == b and len(a) == 32  # 16 bytes hex


# ── registry / anti-drift ──────────────────────────────────────────────────

def test_registry_families_and_catalog():
    reg = Registry("moves", families=("push", "pan"))
    @reg.verb("push.in", family="push", summary="dolly toward subject")
    def _push_in():  # noqa: ANN202
        return "in"
    @reg.verb("pan.left", family="pan", summary="pan to the left")
    def _pan_left():  # noqa: ANN202
        return "left"
    assert reg.names() == ["pan.left", "push.in"]
    assert reg.require("push.in")() == "in"
    reg.check_catalog()  # no drift
    assert {e["name"] for e in reg.catalog()} == {"push.in", "pan.left"}


def test_registry_rejects_bad_family_and_dupes():
    reg = Registry("moves", families=("push",))
    with pytest.raises(ValueError):
        reg.verb("pan.left", family="pan", summary="x")  # unknown family
    with pytest.raises(ValueError):
        reg.verb("wrong", family="push", summary="x")    # missing 'push.' prefix

    reg2 = Registry("looks")
    reg2.verb("noir", summary="a")(lambda: None)
    with pytest.raises(ValueError):
        reg2.verb("noir", summary="b")(lambda: None)     # duplicate
