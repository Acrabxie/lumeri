"""The catalogue must stay in lock-step with the registered core ops."""
from __future__ import annotations

from lumenframe import registry
from lumenframe.catalog import CORE_OPS_CATALOG, describe_ops, op_catalog


def setup_function(_fn):
    registry.reset_for_tests()


def test_every_core_op_has_a_catalog_entry_and_vice_versa():
    catalog_ops = {entry["op"] for entry in CORE_OPS_CATALOG}
    core_ops = {name for name in registry.list_ops() if registry.op_source(name) == "core"}
    assert catalog_ops == core_ops, (
        f"catalog/registry drift — only in catalog: {catalog_ops - core_ops}; "
        f"only registered: {core_ops - catalog_ops}"
    )


def test_describe_ops_renders_all_groups():
    text = describe_ops()
    for op in ("add_layer", "split", "set_mask", "color_grade", "set_keyframe"):
        assert op in text
    assert "Layer management" in text and "Keyframes" in text


def test_op_catalog_marks_sources():
    registry.register_op("custom_thing", lambda doc, op: None)
    entries = {e["op"]: e for e in op_catalog()}
    assert entries["add_layer"]["source"] == "core"
    assert entries["custom_thing"]["source"] == "extension"
    registry.reset_for_tests()
