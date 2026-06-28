"""Optional canvas WORK AREA: persistence/validation + range-render default.

A document may carry an OPTIONAL ``canvas.work_area`` (an ``{"in", "out"}`` span
in seconds on the root timeline). It is the editor's "current range of interest"
— the default span a range render/export honours when no explicit ``t_in`` /
``t_out`` is given.

Iron laws exercised here:

* **byte-identical default** — a doc WITHOUT a work area normalises exactly as
  before: no ``work_area`` key is ever added (to ``canvas`` or the doc), and the
  whole normalised dict equals the historic no-work-area baseline.
* **persist + validate** — a valid work area round-trips through
  :func:`normalize_doc`; a present-but-bad range raises.
* **render default** — :func:`render_range` with no explicit range honours the
  work area; **explicit args always win** (bit-for-bit prior behaviour).

Synthetic docs only; no network, no keys, tmp files only.
"""
from __future__ import annotations

import copy

import numpy as np
import pytest

from lumenframe import apply_layer_patch, empty_doc, model, normalize_doc
from lumenframe.compile import compile_to_layer_stack
from lumenframe.model import WorkAreaError, get_work_area
from lumenframe.render_range import render_range


# ── synthetic doc helpers (mirror test_lumenframe_render_range.py) ─────────


def _patch(*ops):
    return {"version": 1, "ops": list(ops)}


def _add_solid(doc, lid, color, *, start=0.0, duration=1.0):
    return apply_layer_patch(doc, _patch({
        "op": "add_layer", "id": lid, "type": "solid", "color": color,
        "start": start, "duration": duration,
    }))


def build_doc():
    """fps=10, ~2s -> 20 frames. red covers 0..9, green covers 10..19."""
    doc = empty_doc(width=64, height=48, fps=10)
    doc = _add_solid(doc, "red", "#FF0000", start=0.0, duration=1.0)
    doc = _add_solid(doc, "green", "#00FF00", start=1.0, duration=1.0)
    return doc


# ── 1. DEFAULT (no work area) is byte-identical to the baseline ────────────


def test_default_doc_normalizes_without_work_area_key():
    """A doc WITHOUT a work area gains NO new key anywhere — byte-identical."""
    doc = empty_doc()
    norm = normalize_doc(doc)

    # No work_area key sneaks into the canvas or the top-level doc.
    assert "work_area" not in norm["canvas"]
    assert "work_area" not in norm
    # Exact key sets stay as they always were.
    assert set(norm.keys()) == {
        "version", "id", "title", "canvas", "root", "assets", "selection",
    }
    assert set(norm["canvas"].keys()) == {"width", "height", "fps", "background"}


def test_default_normalization_equals_no_work_area_baseline():
    """The full normalised dict equals a re-derived no-work-area baseline.

    We build the baseline by normalising a doc that never had a work area and
    pinning its id (the only nondeterministic field) so the two dicts compare
    structurally. No new keys, no changed values.
    """
    doc = build_doc()
    norm = normalize_doc(doc)

    # Baseline: same doc, normalised again; force matching ids so the compare is
    # about structure/keys, not the random doc id.
    baseline = normalize_doc(doc)
    baseline["id"] = norm["id"]

    assert norm == baseline
    # And explicitly: the baseline itself carries no work area, anywhere.
    assert "work_area" not in baseline["canvas"]
    assert "work_area" not in baseline


def test_normalize_does_not_mutate_input_and_adds_no_key():
    doc = empty_doc()
    snapshot = copy.deepcopy(doc)
    normalize_doc(doc)
    assert doc == snapshot  # input untouched
    assert "work_area" not in doc["canvas"]


def test_get_work_area_none_for_default_doc():
    assert get_work_area(empty_doc()) is None
    assert get_work_area(normalize_doc(empty_doc())) is None
    assert get_work_area(None) is None
    assert get_work_area({}) is None
    assert get_work_area({"canvas": {}}) is None


# ── 2. PERSIST + VALIDATE ──────────────────────────────────────────────────


def test_work_area_persists_through_normalize():
    doc = empty_doc()
    doc["canvas"]["work_area"] = {"in": 0.5, "out": 1.5}
    norm = normalize_doc(doc)
    assert norm["canvas"]["work_area"] == {"in": 0.5, "out": 1.5}
    assert get_work_area(norm) == (0.5, 1.5)


def test_work_area_is_rounded_to_timebase_ndigits():
    doc = empty_doc()
    doc["canvas"]["work_area"] = {"in": 0.1234567, "out": 1.7654321}
    norm = normalize_doc(doc)
    assert norm["canvas"]["work_area"] == {
        "in": round(0.1234567, model.TIME_NDIGITS),
        "out": round(1.7654321, model.TIME_NDIGITS),
    }


def test_work_area_accepts_in_out_pair_sequence():
    doc = empty_doc()
    doc["canvas"]["work_area"] = [0.25, 0.75]
    norm = normalize_doc(doc)
    assert get_work_area(norm) == (0.25, 0.75)


@pytest.mark.parametrize("bad", [
    {"in": 1.0, "out": 1.0},     # out == in
    {"in": 2.0, "out": 1.0},     # out < in
    {"in": -0.5, "out": 1.0},    # in < 0
    {"in": 0.0},                 # missing out
    {"out": 1.0},                # missing in
    {"in": "x", "out": 1.0},     # non-numeric
    [0.0, 1.0, 2.0],             # wrong-length sequence
    [1.0, 1.0],                  # out == in (sequence form)
])
def test_bad_work_area_raises(bad):
    doc = empty_doc()
    doc["canvas"]["work_area"] = bad
    with pytest.raises(WorkAreaError):
        normalize_doc(doc)
    # get_work_area surfaces the same validation error.
    with pytest.raises(WorkAreaError):
        get_work_area(doc)


def test_absent_or_empty_work_area_is_not_an_error():
    """Absent / empty span is "no work area", never an error, never a key."""
    for value in (None, {}, []):
        doc = empty_doc()
        doc["canvas"]["work_area"] = value
        norm = normalize_doc(doc)
        assert "work_area" not in norm["canvas"]
        assert get_work_area(norm) is None


# ── 3. render_range honours the work area as the default ────────────────────


def test_render_range_no_args_uses_full_doc_without_work_area():
    """No work area + no explicit range -> full doc (historic behaviour)."""
    doc = build_doc()  # 20 frames
    got = render_range(doc)
    assert len(got) == 20
    golden = compile_to_layer_stack(doc).render_frames(
        start_frame=0, end_frame=20, step=1
    )
    assert len(got) == len(golden)
    assert np.array_equal(got[0], golden[0])
    assert np.array_equal(got[-1], golden[-1])


def test_render_range_no_args_uses_work_area():
    """work area 0.5..1.5 -> frames [5, 15) -> 10 frames, matching golden."""
    doc = build_doc()
    doc["canvas"]["work_area"] = {"in": 0.5, "out": 1.5}
    got = render_range(doc)
    assert len(got) == 10
    golden = compile_to_layer_stack(doc).render_frames(
        start_frame=5, end_frame=15, step=1
    )
    assert len(got) == len(golden)
    for g, gold in zip(got, golden):
        assert np.array_equal(g, gold)


def test_explicit_range_overrides_work_area():
    """Explicit t_in/t_out win, bit-for-bit, regardless of any work area."""
    doc = build_doc()
    doc["canvas"]["work_area"] = {"in": 0.5, "out": 1.5}

    explicit = render_range(doc, 0.0, 0.3)  # frames [0, 3)
    assert len(explicit) == 3
    golden = compile_to_layer_stack(doc).render_frames(
        start_frame=0, end_frame=3, step=1
    )
    for g, gold in zip(explicit, golden):
        assert np.array_equal(g, gold)

    # Same explicit call against a doc WITHOUT a work area is identical:
    # the work area never touches an explicit request.
    plain = build_doc()
    explicit_plain = render_range(plain, 0.0, 0.3)
    assert len(explicit_plain) == 3
    for a, b in zip(explicit, explicit_plain):
        assert np.array_equal(a, b)


def test_partial_explicit_bound_fills_other_from_work_area():
    """One explicit bound wins; the omitted bound comes from the work area."""
    doc = build_doc()
    doc["canvas"]["work_area"] = {"in": 0.5, "out": 1.5}

    # t_in explicit (0.8 -> frame 8), t_out from work area (1.5 -> frame 15).
    got = render_range(doc, 0.8)
    assert len(got) == len(range(8, 15)) == 7

    # t_out explicit (1.2 -> frame 12), t_in from work area (0.5 -> frame 5).
    got2 = render_range(doc, None, 1.2)
    assert len(got2) == len(range(5, 12)) == 7


def test_render_range_no_work_area_no_args_equals_explicit_full():
    """render_range(doc) with no work area == explicit full-range request."""
    doc = build_doc()
    auto = render_range(doc)
    explicit = render_range(doc, 0.0, 2.0)  # 20 frames @ fps 10
    assert len(auto) == len(explicit) == 20
    for a, b in zip(auto, explicit):
        assert np.array_equal(a, b)
