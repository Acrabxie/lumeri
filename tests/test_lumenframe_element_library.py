"""The overlay-element library — coverage, expansion, render, overlay & drift.

The sibling of ``test_lumenframe_component_library`` but for *elements* rather
than scene *templates*. Where a template stamps a whole styled scene, an element
draws a single graphic (an arrow, a chevron, a badge) and **overlays** it onto
whatever is already on the canvas. These tests pin that contract:

* every registered element has a catalogue entry and vice-versa (no drift);
* every documented param really exists on its function (anti-drift — the shared
  params live in :data:`SHARED_PARAMS`);
* every element expands to valid op dicts, dispatches through ``apply_element``
  and renders frames ``[0, 1]`` to a canvas-sized RGBA without raising;
* every element is a genuine OVERLAY — rendered alone, a frame corner pixel
  stays transparent (it never paints a full-frame background);
* prefixed ids stay unique across two stamps; a colour param round-trips; and
  ``describe_elements`` / ``describe_ops`` expose the whole library.
"""
from __future__ import annotations

import inspect
import json

import pytest

from lumenframe import model
from lumenframe.elements import (
    ELEMENT_CATALOG,
    ELEMENTS,
    SHARED_PARAMS,
    describe_elements,
    element_catalog,
    element_names,
    expand_element,
    theme,
)
from lumenframe.ops import apply_layer_patch
from lumenframe.preview import preview_frames


def patch(*ops):
    return {"version": 1, "ops": list(ops)}


def _empty_doc():
    return {"root": model.new_layer("composition"), "selection": []}


def _param_base(token: str) -> str:
    """Strip a catalogue param token (``progress* (float)``) to its bare name."""
    return token.split("(")[0].strip().rstrip("*").strip()


# ── coverage / drift guards ────────────────────────────────────────────────


class TestCatalogCoverage:
    def test_every_element_has_a_catalog_entry_and_vice_versa(self):
        registered = set(ELEMENTS)
        documented = {e["name"] for e in ELEMENT_CATALOG}
        assert registered == documented, (
            f"ELEMENTS and ELEMENT_CATALOG drifted: "
            f"only registered={registered - documented}, only documented={documented - registered}"
        )

    def test_catalog_is_a_fresh_copy(self):
        a = element_catalog()
        a[0]["name"] = "mutated"
        assert element_catalog()[0]["name"] != "mutated"

    def test_documented_params_exist_on_the_function(self):
        """Every param a catalogue entry names must be a real function param.

        The anti-drift guard: an element can't advertise a param it doesn't
        accept. Shared styling/position params live in SHARED_PARAMS.
        """
        for entry in ELEMENT_CATALOG:
            fn = ELEMENTS[entry["name"]]
            sig = set(inspect.signature(fn).parameters)
            for token in entry.get("params", []):
                name = _param_base(token)
                assert name in sig or name in SHARED_PARAMS, (
                    f"{entry['name']}: documented param {name!r} is not on the function"
                )

    def test_catalog_is_ordered_by_category(self):
        """The catalogue is grouped mark → shape → emphasis → pattern → ribbon → data."""
        order = ["marker", "mark", "shape", "emphasis", "pattern", "ribbon", "data"]
        rank = {c: i for i, c in enumerate(order)}
        seen = [rank[e["category"]] for e in ELEMENT_CATALOG]
        assert seen == sorted(seen), f"catalogue categories out of order: {[e['category'] for e in ELEMENT_CATALOG]}"

    def test_every_entry_has_the_required_fields(self):
        for e in ELEMENT_CATALOG:
            assert {"name", "category", "summary", "params", "example"} <= set(e), e
            assert e["example"].get("element") == e["name"], e


# ── expansion / dispatch / render ──────────────────────────────────────────


class TestExpansionAndRender:
    @pytest.mark.parametrize("name", sorted(ELEMENTS))
    def test_expands_to_valid_ops(self, name):
        ops = expand_element(name, {})
        assert ops and all(isinstance(o, dict) and "op" in o for o in ops), name

    @pytest.mark.parametrize("name", sorted(ELEMENTS))
    def test_applies_and_renders(self, name):
        """Each element dispatches via apply_element and previews two frames."""
        out = apply_layer_patch(_empty_doc(), patch(
            {"op": "apply_element", "element": name, "params": {}}
        ))
        assert out["root"]["children"], f"{name} produced no layers"
        frames = preview_frames(out, [0, 1], strict=False)
        assert len(frames) == 2
        for _idx, rgba in frames:
            assert rgba.shape[2] == 4  # canvas-sized RGBA

    @pytest.mark.parametrize("name", sorted(ELEMENTS))
    def test_element_is_an_overlay_not_a_background(self, name):
        """Rendered alone, a frame corner pixel stays transparent (alpha ~0).

        An element composes onto the canvas — it must never paint a full-frame
        background, so the extreme corners have nothing drawn over them.
        """
        out = apply_layer_patch(_empty_doc(), patch(
            {"op": "apply_element", "element": name, "params": {}}
        ))
        frames = preview_frames(out, [0, 1], strict=False)
        for _idx, rgba in frames:
            h, w = rgba.shape[0], rgba.shape[1]
            corners = [rgba[0, 0, 3], rgba[0, w - 1, 3], rgba[h - 1, 0, 3], rgba[h - 1, w - 1, 3]]
            assert all(int(a) <= 4 for a in corners), (
                f"{name} is not an overlay — a corner alpha is {corners}"
            )

    @pytest.mark.parametrize("name", sorted(ELEMENTS))
    def test_layer_ids_are_unique(self, name):
        out = apply_layer_patch(_empty_doc(), patch(
            {"op": "apply_element", "element": name, "params": {}}
        ))
        ids = [c["id"] for c in out["root"]["children"]]
        assert len(ids) == len(set(ids)), f"{name} produced duplicate ids: {ids}"

    @pytest.mark.parametrize("name", sorted(ELEMENTS))
    def test_prefix_avoids_id_clash_across_two_applies(self, name):
        """The same element stamped twice (distinct prefix) never clashes ids."""
        out = apply_layer_patch(_empty_doc(), patch(
            {"op": "apply_element", "element": name, "params": {"prefix": "a"}},
            {"op": "apply_element", "element": name, "params": {"prefix": "b"}},
        ))
        ids = [c["id"] for c in out["root"]["children"]]
        assert len(ids) == len(set(ids)), f"{name} clashed across two stamps: {ids}"


# ── palette / colour ───────────────────────────────────────────────────────


class TestColour:
    @pytest.mark.parametrize("name", sorted(ELEMENTS))
    def test_color_param_round_trips_into_the_ops(self, name):
        """A distinctive `color` shows up somewhere in the emitted op stream."""
        marker = "#ab12cd"
        ops = expand_element(name, {"color": marker})
        blob = json.dumps(ops)
        assert marker in blob, f"{name} ignored its color param"

    def test_default_colour_is_the_brand_accent(self):
        ops = expand_element("chevron", {})
        assert theme.PALETTES["lumeri"]["accent"] in json.dumps(ops)


# ── agent-facing description ────────────────────────────────────────────────


class TestDescribeElements:
    def test_lists_every_element_and_the_shared_params(self):
        text = describe_elements()
        for name in element_names():
            assert name in text, f"{name} missing from describe_elements()"
        for shared in SHARED_PARAMS:
            assert shared in text
        # palette note
        assert theme.PALETTES["lumeri"]["accent"] in text

    def test_injected_into_describe_ops(self):
        from lumenframe import describe_ops

        text = describe_ops()
        assert "[Element library]" in text
        assert "chevron" in text and "progress_bar" in text
