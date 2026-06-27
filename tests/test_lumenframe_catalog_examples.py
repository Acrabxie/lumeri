"""Gemini-ergonomics extensions to the op catalogue.

Round-2 enrichment: every core op now ships a structurally-valid ``example`` op
dict and an ``errors`` list of its main failure codes, ``describe_ops`` surfaces
both inline, and ``error_catalog`` decodes every ``LayerPatchError`` code. These
tests pin that contract so the agent-facing docs cannot silently regress.
"""
from __future__ import annotations

from lumenframe import model, registry
from lumenframe.catalog import CORE_OPS_CATALOG, describe_ops, error_catalog
from lumenframe.ops import normalize_doc, validate_patch


def setup_function(_fn):
    registry.reset_for_tests()


def _registered_ops() -> set[str]:
    return set(registry.list_ops())


def test_every_core_op_has_a_nonempty_example_with_a_real_op_name():
    real = _registered_ops()
    for entry in CORE_OPS_CATALOG:
        example = entry.get("example")
        assert isinstance(example, dict) and example, (
            f"{entry['op']}: example must be a non-empty dict"
        )
        op_name = example.get("op")
        assert op_name, f"{entry['op']}: example is missing its 'op' key"
        assert op_name in real, (
            f"{entry['op']}: example op {op_name!r} is not a registered op"
        )
        # The example should illustrate the entry it documents.
        assert op_name == entry["op"], (
            f"{entry['op']}: example op {op_name!r} does not match the entry"
        )


def test_every_core_op_has_a_nonempty_errors_list_of_known_codes():
    codes = set(error_catalog())
    for entry in CORE_OPS_CATALOG:
        errors = entry.get("errors")
        assert isinstance(errors, list) and errors, (
            f"{entry['op']}: errors must be a non-empty list"
        )
        for line in errors:
            assert isinstance(line, str) and line.strip(), (
                f"{entry['op']}: each error must be a non-empty string"
            )
            code = line.split(" ", 1)[0]
            assert code in codes, (
                f"{entry['op']}: error code {code!r} is not in error_catalog()"
            )


def test_examples_are_structurally_valid_against_a_populated_doc():
    """Each example applies with no structural (shape/arg) error.

    A doc is seeded with the layer ids the examples reference, so the only thing
    validate_patch can complain about is layer-not-found if an id were absent —
    never a malformed envelope or a missing required arg. We assert no structural
    code surfaces, proving every example is a well-formed op call.
    """
    structural = {"E_ARG", "E_OP", "E_PATCH", "E_OP_UNKNOWN"}
    doc = normalize_doc({})

    def mk(lid, ltype, **kw):
        return model._normalize_layer({"type": ltype, "id": lid, **kw})

    doc["root"]["children"] = [
        mk("clip1", "video", start=0.0, duration=10.0, source_in=0.0, source_out=10.0,
           effects=[{"type": "gaussian_blur", "id": "fx1", "params": {"radius": 4}}]),
        mk("clip2", "video", start=0.0, duration=5.0, source_in=0.0, source_out=5.0),
        mk("comp1", "composition", start=0.0, duration=5.0, children=[]),
        mk("title", "text", start=0.0, duration=3.0),
        mk("music", "audio", start=0.0, duration=10.0, source_in=0.0, source_out=10.0),
    ]

    for entry in CORE_OPS_CATALOG:
        example = entry["example"]
        result = validate_patch(doc, {"version": 1, "ops": [example]})
        offending = {err["code"] for err in result["errors"]} & structural
        assert not offending, (
            f"{entry['op']}: example raised structural error(s) {offending}: "
            f"{[e['message'] for e in result['errors']]}"
        )


def test_describe_ops_surfaces_examples_and_errors():
    text = describe_ops()
    assert "Example:" in text, "describe_ops() must show an Example: section"
    assert "Errors:" in text, "describe_ops() must show an Errors: section"
    assert "[Error codes]" in text, "describe_ops() must list the error codes"
    # A concrete example string must appear verbatim somewhere in the block.
    assert '"op": "set_opacity"' in text


def test_error_catalog_is_nonempty_and_includes_common_codes():
    catalog = error_catalog()
    assert isinstance(catalog, dict) and catalog, "error_catalog() must be non-empty"
    for code in ("E_NOT_FOUND", "E_ARG", "E_RANGE", "E_OP_UNKNOWN"):
        assert code in catalog, f"error_catalog() missing common code {code}"
        assert catalog[code].strip(), f"error_catalog()[{code!r}] must have a meaning"


def test_error_catalog_stays_in_sync_with_op_hints():
    """The catalogue's codes must match the per-op hints in lumenframe.ops."""
    from lumenframe.ops import _HINTS

    assert set(error_catalog()) == set(_HINTS), (
        "error_catalog() drifted from lumenframe.ops._HINTS — "
        f"only in catalog: {set(error_catalog()) - set(_HINTS)}; "
        f"only in ops: {set(_HINTS) - set(error_catalog())}"
    )


def test_error_catalog_returns_a_copy():
    """Callers mutating the result must not corrupt the shared table."""
    first = error_catalog()
    first["E_NOT_FOUND"] = "tampered"
    assert error_catalog()["E_NOT_FOUND"] != "tampered"
