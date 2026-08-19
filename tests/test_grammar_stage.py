"""Tests for the spatial grammar layer.

The load-bearing ones are not the happy paths. They are:

* ``test_proposal_applies_through_ops`` — the proposal must survive the real
  patch path. The prototype's op shape looked right and was silently ignored by
  ``apply_layer_patch`` (it nested the fields under ``transform`` instead of
  passing them flat), which is exactly the kind of bug a happy-path test misses.
* ``test_hollow_frame_is_not_reported_as_covered`` — bounding boxes claim a
  hollow frame covers a title it never touches. Getting this wrong makes the
  model move something that was already fine.
* the blind-spot tests — alpha cannot see a discarded ``scale_y`` or an
  off-canvas layer, and failing silently there is worse than failing loudly.
"""
from __future__ import annotations

import json

import pytest

from lumenframe import model, ops
from lumenframe.grammar import PhraseError, propose, render_text, stage_report
from lumenframe.grammar import blindspots

W, H = 1920, 1080


def _doc(*layers: dict) -> dict:
    doc = model.empty_doc(title="t", width=W, height=H, fps=30.0)
    doc["root"]["children"] = list(layers)
    return model.normalize_doc(doc)


def _rect(name: str, rect: list[float], **kw) -> dict:
    layer = model.new_layer(
        "shape", name=name, duration=5.0,
        props={"kind": "rect", "rect": rect, "fill": "#cc5533"},
    )
    if kw:
        layer["transform"] = {**model.DEFAULT_TRANSFORM, **kw}
    return layer


def _text(name: str, text: str = "LUMERI", size: int = 130, **kw) -> dict:
    layer = model.new_layer(
        "text", name=name, duration=5.0,
        props={"text": text, "font_size": size, "color": "#ffffff"},
    )
    if kw:
        layer["transform"] = {**model.DEFAULT_TRANSFORM, **kw}
    return layer


# ── measurement ─────────────────────────────────────────────────────────

def test_measures_real_extent_not_canvas_frame():
    """A shape's box must match its geometry, not the canvas-sized content frame."""
    report = stage_report(_doc(_rect("block", [0.25, 0.25, 0.5, 0.5])), 1.0)
    box = report["layers"][0]["bbox_px"]
    assert box["x"] == pytest.approx(W * 0.25, abs=2)
    assert box["y"] == pytest.approx(H * 0.25, abs=2)
    assert box["w"] == pytest.approx(W * 0.25, abs=2)
    assert box["h"] == pytest.approx(H * 0.25, abs=2)


def test_absent_layer_is_listed_not_dropped():
    """A layer with nothing visible is still reported — absence is information."""
    layer = _text("title")
    layer["start"], layer["duration"] = 10.0, 1.0
    report = stage_report(_doc(layer), 1.0)
    assert len(report["layers"]) == 1
    assert report["layers"][0]["present"] is False


def test_stacking_order_decides_who_covers_whom():
    back = _rect("back", [0.2, 0.2, 0.8, 0.8])
    front = _rect("front", [0.3, 0.3, 0.6, 0.6])
    report = stage_report(_doc(back, front), 1.0)
    by_name = {L["name"]: L for L in report["layers"]}
    assert by_name["back"]["z"] < by_name["front"]["z"]
    assert by_name["front"]["overlaps"][0]["relation"] == "covers"
    assert by_name["back"]["overlaps"][0]["relation"] == "covered_by"


# ── occlusion must use real coverage, not boxes ─────────────────────────

def test_hollow_frame_is_not_reported_as_covered():
    """The case that decides whether real coverage was worth its cost.

    A title centred inside an outlined frame is fully inside that frame's
    bounding box, so a box test reports 100% coverage. Their pixels never touch.
    """
    frame = model.new_layer(
        "shape", name="frame", duration=5.0,
        props={
            "kind": "rect", "rect": [0.15, 0.25, 0.85, 0.75],
            "fill": None, "stroke": {"color": "#ffffff", "width": 10},
        },
    )
    report = stage_report(_doc(frame, _text("title")), 1.0)
    title = next(L for L in report["layers"] if L["name"] == "title")

    assert title["overlaps"] == [], (
        "a hollow frame must not be reported as covering the title inside it"
    )


def test_regions_use_coverage_not_bounding_box():
    """Region span must agree with occlusion, or the report contradicts itself."""
    frame = model.new_layer(
        "shape", name="frame", duration=5.0,
        props={
            "kind": "rect", "rect": [0.05, 0.05, 0.95, 0.95],
            "fill": None, "stroke": {"color": "#ffffff", "width": 8},
        },
    )
    report = stage_report(_doc(frame), 1.0)
    covered = report["layers"][0]["regions_covered"]

    assert "mid_center" not in covered, (
        "an outlined frame paints nothing in the middle cell; reporting it "
        "would be the bounding-box lie that real coverage exists to avoid"
    )
    assert "upper_left" in covered


# ── blind spots ─────────────────────────────────────────────────────────

def test_discarded_scale_y_is_reported():
    """The renderer collapses non-uniform scale; alpha cannot see the loss."""
    report = stage_report(_doc(_rect("b", [0.4, 0.4, 0.6, 0.6], scale_x=1.2, scale_y=0.5)), 1.0)
    fields = {w["field"] for w in report["warnings"] if w["kind"] == "discarded_intent"}
    assert "transform.scale_y" in fields


def test_discarded_anchor_is_reported():
    report = stage_report(_doc(_rect("b", [0.4, 0.4, 0.6, 0.6], anchor_x=0.0)), 1.0)
    fields = {w["field"] for w in report["warnings"] if w["kind"] == "discarded_intent"}
    assert "transform.anchor_x" in fields


def test_clean_document_produces_no_warnings():
    """Warnings must be earned — a plain document should be quiet."""
    report = stage_report(_doc(_rect("b", [0.4, 0.4, 0.6, 0.6])), 1.0)
    assert report["warnings"] == []


def test_offcanvas_layer_is_flagged():
    report = stage_report(_doc(_rect("badge", [0.85, 0.3, 1.3, 0.7])), 1.0)
    kinds = {w["kind"] for w in report["warnings"]}
    assert "suspected_clipping" in kinds


def test_limitation_table_matches_renderer_docstring():
    """Drift gate: the ignored-field table must track the renderer's own claims.

    When the renderer starts honouring ``anchor`` / ``scale_y``, this test turns
    red until the table shrinks — a stale limitation warning is its own kind of
    dishonesty.
    """
    from lumenframe import compile as lf_compile

    doc = (lf_compile.__doc__ or "").lower()
    for field in blindspots.renderer_limitation_fields():
        stem = field.split("_")[0]
        assert stem in doc, (
            f"blindspots claims the renderer ignores {field!r}, but compile.py's "
            "docstring no longer mentions it — one of the two is stale"
        )


# ── language bridge and the real write path ─────────────────────────────

def test_proposal_applies_through_ops():
    """A proposal must survive ``apply_layer_patch`` and actually move the layer."""
    layer = _text("title", x=0.0)
    doc = _doc(layer)
    before = stage_report(doc, 1.0)["layers"][0]["bbox_px"]["x"]

    result = propose(
        "往左一点",
        layer_id=layer["id"],
        current_transform=layer.get("transform"),
        canvas_width=W,
    )
    assert result["applied"] is False

    updated = ops.apply_layer_patch(doc, result["patch"])
    after = stage_report(updated, 1.0)["layers"][0]["bbox_px"]["x"]

    expected_step = result["reasoning"]["step_px"]
    assert after == pytest.approx(before - expected_step, abs=2), (
        "the patch must move the layer by exactly the step it reported"
    )


def test_scale_proposal_applies():
    layer = _rect("b", [0.4, 0.4, 0.6, 0.6])
    doc = _doc(layer)
    before = stage_report(doc, 1.0)["layers"][0]["bbox_px"]["w"]

    result = propose(
        "放大一些",
        layer_id=layer["id"],
        current_transform=layer.get("transform"),
        canvas_width=W,
    )
    updated = ops.apply_layer_patch(doc, result["patch"])
    after = stage_report(updated, 1.0)["layers"][0]["bbox_px"]["w"]

    assert after > before
    assert after == pytest.approx(before * result["reasoning"]["factor"], rel=0.02)


def test_unparseable_phrase_refuses():
    with pytest.raises(PhraseError):
        propose("把它弄好看点", layer_id="x", current_transform={}, canvas_width=W)


# ── contracts ───────────────────────────────────────────────────────────

def test_report_is_deterministic_and_serialisable():
    doc = _doc(_rect("a", [0.2, 0.2, 0.5, 0.5]), _text("title"))
    first = json.dumps(stage_report(doc, 1.0), sort_keys=True)
    second = json.dumps(stage_report(doc, 1.0), sort_keys=True)
    assert first == second


def test_grammar_never_writes_to_the_document():
    """NFR-2 / FR-4: the package is a read-only derivation."""
    doc = _doc(_rect("a", [0.2, 0.2, 0.5, 0.5]))
    snapshot = json.dumps(doc, sort_keys=True)
    stage_report(doc, 1.0)
    render_text(stage_report(doc, 1.0))
    assert json.dumps(doc, sort_keys=True) == snapshot


def test_grid_comes_from_the_shared_registry():
    """ADR-3: one grid vocabulary, not a private copy."""
    from lumenframe.compose.framing import grid_for
    from lumenframe.grammar import vocabulary

    report = stage_report(_doc(_rect("a", [0.2, 0.2, 0.5, 0.5])), 1.0, detail="full")
    spec = grid_for(vocabulary.GRID_KIND)
    assert report["grid"]["cut_lines"]["vertical_px"] == [
        pytest.approx(W * v, abs=0.01) for v in spec.v
    ]


def test_text_rendering_is_readable():
    doc = _doc(_rect("subject", [0.5, 0.2, 0.9, 0.8]), _text("title"))
    text = render_text(stage_report(doc, 1.0))
    assert "subject" in text and "title" in text
    assert "alpha_raster" in text
