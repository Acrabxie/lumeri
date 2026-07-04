"""Extension registry tests — the third-party / GitHub plug-in surface."""
from __future__ import annotations

import pytest

from lumenframe import (
    apply_layer_patch,
    empty_doc,
    find_layer,
    list_layer_types,
    list_ops,
    register_layer_type,
    register_op,
)
from lumenframe.ops import LayerPatchError
from lumenframe import registry


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Each test starts from a clean (core-only) registry and restores after."""
    registry.reset_for_tests()
    yield
    registry.reset_for_tests()


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def test_core_ops_are_present():
    ops = list_ops()
    for expected in ("add_layer", "split", "group_layers", "set_mask", "color_grade"):
        assert expected in ops


def test_third_party_can_register_and_drive_a_custom_op():
    @register_op("tag_layer")
    def _tag(doc, op):
        layer = find_layer(doc, op["layer_id"])
        if layer is None:
            raise LayerPatchError("E_NOT_FOUND", "tag_layer: no such layer")
        layer["props"]["tag"] = op.get("value")

    doc = empty_doc()
    doc = apply_layer_patch(doc, patch({"op": "add_layer", "id": "x", "type": "solid"}))
    doc = apply_layer_patch(doc, patch({"op": "tag_layer", "layer_id": "x", "value": "hero"}))
    assert find_layer(doc, "x")["props"]["tag"] == "hero"


def test_core_op_is_protected_from_silent_override():
    with pytest.raises(ValueError):
        register_op("add_layer", lambda doc, op: None)
    # explicit override is allowed
    register_op("add_layer", lambda doc, op: None, override=True)


def test_third_party_layer_type_passes_validation():
    register_layer_type("particle_field", {"container": False, "defaults": {"density": 100}})
    assert "particle_field" in list_layer_types()
    doc = empty_doc()
    out = apply_layer_patch(doc, patch({"op": "add_layer", "id": "p", "type": "particle_field"}))
    assert find_layer(out, "p")["type"] == "particle_field"


def test_unregistered_layer_type_is_rejected_by_validation():
    doc = empty_doc()
    with pytest.raises(LayerPatchError) as e:
        apply_layer_patch(doc, patch({"op": "add_layer", "id": "p", "type": "not_a_real_type"}))
    assert e.value.code == "E_TYPE"
