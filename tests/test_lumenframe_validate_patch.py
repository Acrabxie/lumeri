"""Dry-run LayerPatch validation (``validate_patch``) — ADD-only ergonomics.

``validate_patch`` lets Gemini check a LayerPatch *before* committing it: it runs
the same op dispatch as :func:`apply_layer_patch` against a private copy, never
raising and never mutating the caller's document. It returns
``{"ok": bool, "errors": [{op_index, code, message, hint}]}``.
"""
from __future__ import annotations

import copy
import json

from lumenframe import apply_layer_patch, empty_doc
from lumenframe.ops import validate_patch


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def make_doc():
    """Root comp with three layers v1<v2<v3 (bottom→top)."""
    doc = empty_doc(width=1920, height=1080, fps=30)
    return apply_layer_patch(doc, patch(
        {"op": "add_layer", "id": "v1", "type": "video", "start": 0, "duration": 4},
        {"op": "add_layer", "id": "v2", "type": "image", "start": 1, "duration": 3},
        {"op": "add_layer", "id": "v3", "type": "text", "start": 2, "duration": 2, "text": "hi"},
    ))


# ── happy path ───────────────────────────────────────────────────────────


def test_valid_patch_reports_ok_with_no_errors():
    doc = make_doc()
    result = validate_patch(doc, patch(
        {"op": "set_opacity", "layer_id": "v2", "opacity": 0.5},
        {"op": "rename_layer", "layer_id": "v1", "name": "base"},
    ))
    assert result == {"ok": True, "errors": []}


def test_valid_patch_on_empty_doc_is_ok():
    result = validate_patch(empty_doc(), patch(
        {"op": "add_layer", "type": "video", "start": 0, "duration": 2},
    ))
    assert result["ok"] is True
    assert result["errors"] == []


# ── failure path: structured error with code + hint + op_index ───────────


def test_bad_op_reports_code_hint_and_op_index_without_raising():
    doc = make_doc()
    result = validate_patch(doc, patch(
        {"op": "set_opacity", "layer_id": "v2", "opacity": 0.5},   # op_index 0 (ok)
        {"op": "delete_layer", "layer_id": "ghost"},               # op_index 1 (bad)
    ))
    assert result["ok"] is False
    assert len(result["errors"]) == 1
    err = result["errors"][0]
    assert err == {
        "op_index": 1,
        "code": "E_NOT_FOUND",
        "message": "delete_layer: layer not found: 'ghost'",
        "hint": "the layer id does not exist; inspect current layers first",
    }


def test_collects_multiple_reachable_errors_in_order():
    doc = make_doc()
    result = validate_patch(doc, patch(
        {"op": "delete_layer", "layer_id": "ghost1"},   # op_index 0 — E_NOT_FOUND
        {"op": "set_speed", "layer_id": "v1", "speed": 0},  # op_index 1 — E_SPEED
    ))
    assert result["ok"] is False
    codes = [(e["op_index"], e["code"]) for e in result["errors"]]
    assert codes == [(0, "E_NOT_FOUND"), (1, "E_SPEED")]
    # Each error carries a non-empty actionable hint.
    assert all(e["hint"] for e in result["errors"])
    assert result["errors"][1]["hint"] == "speed must be greater than 0"


def test_range_error_hint():
    doc = make_doc()
    # split at a time outside the layer -> E_RANGE
    result = validate_patch(doc, patch(
        {"op": "split", "layer_id": "v1", "at_time": 999},
    ))
    assert result["ok"] is False
    err = result["errors"][0]
    assert err["op_index"] == 0
    assert err["code"] == "E_RANGE"
    assert err["hint"] == "value out of allowed range"


def test_unknown_op_is_reported_not_raised():
    doc = make_doc()
    result = validate_patch(doc, patch({"op": "frobnicate", "layer_id": "v1"}))
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "E_OP_UNKNOWN"
    assert result["errors"][0]["op_index"] == 0


def test_bad_envelope_reported_not_raised():
    doc = make_doc()
    result = validate_patch(doc, {"version": 2, "ops": []})
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "E_PATCH"


# ── no-mutation guarantee ────────────────────────────────────────────────


def test_validate_does_not_mutate_doc_on_failure():
    doc = make_doc()
    before = json.dumps(doc, sort_keys=True)
    snapshot = copy.deepcopy(doc)
    result = validate_patch(doc, patch(
        {"op": "delete_layer", "layer_id": "v2"},       # would succeed if applied
        {"op": "delete_layer", "layer_id": "ghost"},    # fails
    ))
    assert result["ok"] is False
    after = json.dumps(doc, sort_keys=True)
    # byte-identical serialisation AND deep-equal object identity of contents
    assert after == before
    assert doc == snapshot


def test_validate_does_not_mutate_doc_on_success():
    doc = make_doc()
    before = json.dumps(doc, sort_keys=True)
    result = validate_patch(doc, patch(
        {"op": "delete_layer", "layer_id": "v2"},
        {"op": "set_opacity", "layer_id": "v1", "opacity": 0.25},
    ))
    assert result["ok"] is True
    after = json.dumps(doc, sort_keys=True)
    assert after == before


def test_returned_errors_are_plain_json_serialisable():
    doc = make_doc()
    result = validate_patch(doc, patch({"op": "delete_layer", "layer_id": "ghost"}))
    # The whole result round-trips through JSON unchanged (Gemini-facing surface).
    assert json.loads(json.dumps(result)) == result
