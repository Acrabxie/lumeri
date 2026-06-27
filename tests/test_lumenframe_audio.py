"""Doc-structure tests for audio fade *shapes* and the new duck_audio op.

These cover the CapCut/DaVinci audio basics added in round 6:

* ``set_audio_fade`` accepts ``shape`` in {linear, exp, log} and stores the
  curve alongside the (still scalar) fade durations;
* ``duck_audio`` writes a structured sidechain-ducking descriptor and validates
  that its target layer exists (E_NOT_FOUND otherwise);
* every new core op has a catalog entry (catalog/registry parity, run here too).

All audio data is structured metadata for a downstream mixer — lumenframe
compile is video-only — so these assert document shape, not rendered pixels.
"""
from __future__ import annotations

import pytest

from lumenframe import model, registry
from lumenframe.ops import apply_layer_patch, LayerPatchError


def _doc_with(*layers: dict) -> dict:
    return {
        "root": model.new_layer("composition", children=list(layers)),
        "selection": [],
    }


# ════════════════════════════════════════════════════════════════════════
# set_audio_fade — fade SHAPES
# ════════════════════════════════════════════════════════════════════════


class TestAudioFadeShape:
    """set_audio_fade stores fade curve shape alongside scalar durations."""

    def test_fade_shape_exp_stored_on_both_edges(self):
        """shape='exp' with both fades records the curve on each edge."""
        audio = model.new_layer("audio")
        doc = _doc_with(audio)
        lid = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{
                "op": "set_audio_fade",
                "layer_id": lid,
                "fade_in": 0.5,
                "fade_out": 1.0,
                "shape": "exp",
            }],
        })

        props = result["root"]["children"][0]["props"]
        # Durations stay plain scalars (unchanged contract).
        assert props["fade_in"] == 0.5
        assert props["fade_out"] == 1.0
        # Shape recorded for both edges.
        assert props["fade_in_shape"] == "exp"
        assert props["fade_out_shape"] == "exp"

    def test_fade_shape_log_in_only(self):
        """shape applies to whichever edge the op writes (fade_in only here)."""
        audio = model.new_layer("audio")
        doc = _doc_with(audio)
        lid = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "set_audio_fade", "layer_id": lid, "fade_in": 0.4, "shape": "log"}],
        })

        props = result["root"]["children"][0]["props"]
        assert props["fade_in"] == 0.4
        assert props["fade_in_shape"] == "log"
        # No fade_out written -> no fade_out_shape leaked.
        assert "fade_out" not in props
        assert "fade_out_shape" not in props

    def test_fade_shape_defaults_to_linear(self):
        """Omitting shape stores the linear default next to the duration."""
        audio = model.new_layer("audio")
        doc = _doc_with(audio)
        lid = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "set_audio_fade", "layer_id": lid, "fade_in": 0.3}],
        })

        props = result["root"]["children"][0]["props"]
        assert props["fade_in"] == 0.3
        assert props["fade_in_shape"] == "linear"

    def test_fade_unknown_shape_raises_e_arg(self):
        """An out-of-vocabulary shape is rejected with E_ARG."""
        audio = model.new_layer("audio")
        doc = _doc_with(audio)
        lid = doc["root"]["children"][0]["id"]

        with pytest.raises(LayerPatchError) as exc:
            apply_layer_patch(doc, {
                "version": 1,
                "ops": [{"op": "set_audio_fade", "layer_id": lid, "fade_in": 0.5, "shape": "bounce"}],
            })
        assert exc.value.code == "E_ARG"

    def test_fade_without_shape_stays_backward_compatible(self):
        """No shape + no fade keys means no shape keys appear at all."""
        audio = model.new_layer("audio")
        doc = _doc_with(audio)
        lid = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "set_audio_fade", "layer_id": lid, "fade_out": 2.0}],
        })

        props = result["root"]["children"][0]["props"]
        assert props["fade_out"] == 2.0
        assert props["fade_out_shape"] == "linear"
        assert "fade_in" not in props
        assert "fade_in_shape" not in props


# ════════════════════════════════════════════════════════════════════════
# duck_audio — sidechain ducking descriptor
# ════════════════════════════════════════════════════════════════════════


class TestDuckAudio:
    """duck_audio writes/validates props.ducking on the layer."""

    def test_duck_audio_writes_valid_descriptor(self):
        """duck_audio stores a complete ducking descriptor."""
        music = model.new_layer("audio")
        vo = model.new_layer("audio")
        doc = _doc_with(music, vo)
        music_id = doc["root"]["children"][0]["id"]
        vo_id = doc["root"]["children"][1]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{
                "op": "duck_audio",
                "layer_id": music_id,
                "target_id": vo_id,
                "amount": -12,
                "attack": 0.05,
                "release": 0.3,
            }],
        })

        ducking = result["root"]["children"][0]["props"]["ducking"]
        assert ducking == {
            "target_id": vo_id,
            "amount": -12.0,
            "attack": 0.05,
            "release": 0.3,
        }

    def test_duck_audio_defaults(self):
        """amount/attack/release fall back to documented defaults."""
        music = model.new_layer("audio")
        vo = model.new_layer("audio")
        doc = _doc_with(music, vo)
        music_id = doc["root"]["children"][0]["id"]
        vo_id = doc["root"]["children"][1]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "duck_audio", "layer_id": music_id, "target_id": vo_id}],
        })

        ducking = result["root"]["children"][0]["props"]["ducking"]
        assert ducking["target_id"] == vo_id
        assert ducking["amount"] == -12.0
        assert ducking["attack"] == 0.05
        assert ducking["release"] == 0.3

    def test_duck_audio_linear_gain_amount(self):
        """A 0..1 amount (linear gain floor) is stored verbatim."""
        music = model.new_layer("audio")
        vo = model.new_layer("audio")
        doc = _doc_with(music, vo)
        music_id = doc["root"]["children"][0]["id"]
        vo_id = doc["root"]["children"][1]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "duck_audio", "layer_id": music_id, "target_id": vo_id, "amount": 0.25}],
        })

        assert result["root"]["children"][0]["props"]["ducking"]["amount"] == 0.25

    def test_duck_audio_missing_target_raises_not_found(self):
        """A ducking target that does not exist raises E_NOT_FOUND."""
        music = model.new_layer("audio")
        doc = _doc_with(music)
        music_id = doc["root"]["children"][0]["id"]

        with pytest.raises(LayerPatchError) as exc:
            apply_layer_patch(doc, {
                "version": 1,
                "ops": [{"op": "duck_audio", "layer_id": music_id, "target_id": "ghost"}],
            })
        assert exc.value.code == "E_NOT_FOUND"

    def test_duck_audio_missing_layer_id_raises_e_arg(self):
        """duck_audio without layer_id raises E_ARG."""
        doc = _doc_with(model.new_layer("audio"))
        with pytest.raises(LayerPatchError) as exc:
            apply_layer_patch(doc, {
                "version": 1,
                "ops": [{"op": "duck_audio", "target_id": "x"}],
            })
        assert exc.value.code == "E_ARG"

    def test_duck_audio_missing_target_id_raises_e_arg(self):
        """duck_audio without target_id raises E_ARG."""
        music = model.new_layer("audio")
        doc = _doc_with(music)
        music_id = doc["root"]["children"][0]["id"]
        with pytest.raises(LayerPatchError) as exc:
            apply_layer_patch(doc, {
                "version": 1,
                "ops": [{"op": "duck_audio", "layer_id": music_id}],
            })
        assert exc.value.code == "E_ARG"

    def test_duck_audio_negative_attack_raises_e_range(self):
        """A negative attack/release time is rejected with E_RANGE."""
        music = model.new_layer("audio")
        vo = model.new_layer("audio")
        doc = _doc_with(music, vo)
        music_id = doc["root"]["children"][0]["id"]
        vo_id = doc["root"]["children"][1]["id"]
        with pytest.raises(LayerPatchError) as exc:
            apply_layer_patch(doc, {
                "version": 1,
                "ops": [{"op": "duck_audio", "layer_id": music_id, "target_id": vo_id, "attack": -0.1}],
            })
        assert exc.value.code == "E_RANGE"


# ════════════════════════════════════════════════════════════════════════
# Catalog parity for the new ops
# ════════════════════════════════════════════════════════════════════════


class TestAudioCatalog:
    """New core ops are catalogued and stay in lock-step with the registry."""

    def test_catalog_has_duck_audio(self):
        from lumenframe.catalog import CORE_OPS_CATALOG
        names = {e["op"] for e in CORE_OPS_CATALOG}
        assert "duck_audio" in names

    def test_duck_audio_group_is_audio(self):
        from lumenframe.catalog import CORE_OPS_CATALOG
        entry = next(e for e in CORE_OPS_CATALOG if e["op"] == "duck_audio")
        assert entry["group"] == "audio"

    def test_set_audio_fade_catalog_mentions_shape(self):
        from lumenframe.catalog import CORE_OPS_CATALOG
        entry = next(e for e in CORE_OPS_CATALOG if e["op"] == "set_audio_fade")
        joined = " ".join(entry["args"]) + " " + entry["summary"]
        assert "shape" in joined
        assert entry["example"].get("shape") in {"linear", "exp", "log"}

    def test_catalog_registry_parity_for_core_ops(self):
        """The catalog/registry drift invariant holds with the new ops."""
        registry.reset_for_tests()
        from lumenframe.catalog import CORE_OPS_CATALOG
        catalog_ops = {e["op"] for e in CORE_OPS_CATALOG}
        core_ops = {n for n in registry.list_ops() if registry.op_source(n) == "core"}
        assert catalog_ops == core_ops, (
            f"only in catalog: {catalog_ops - core_ops}; "
            f"only registered: {core_ops - catalog_ops}"
        )
