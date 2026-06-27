"""Test suite for lumenframe text styling, audio, and animation preset ops."""
import pytest
from lumenframe.ops import apply_layer_patch, LayerPatchError
from lumenframe import model


class TestSetText:
    """Tests for set_text op (merge text props)."""

    def test_set_text_merge_single_prop(self):
        """Only provided keys are merged; others stay unchanged."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("text", props={"text": "old", "color": "#000000", "font_size": 24}),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "set_text", "layer_id": layer_id, "text": "new"}],
        })

        props = result["root"]["children"][0]["props"]
        assert props["text"] == "new"
        assert props["color"] == "#000000"
        assert props["font_size"] == 24

    def test_set_text_multiple_props(self):
        """Multiple properties can be set in one op."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("text", props={"text": "hello"}),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "set_text", "layer_id": layer_id, "text": "hi", "color": "#FF0000", "font_size": 48}],
        })

        props = result["root"]["children"][0]["props"]
        assert props["text"] == "hi"
        assert props["color"] == "#FF0000"
        assert props["font_size"] == 48

    def test_set_text_with_all_props(self):
        """All recognized text props can be set."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("text"),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{
                "op": "set_text",
                "layer_id": layer_id,
                "text": "styled",
                "font": "Arial",
                "font_size": 64,
                "color": "#FFFFFF",
                "align": "center",
                "stroke": {"color": "#000000", "width": 2.0},
                "shadow": {"color": "#333333", "dx": 2, "dy": 2, "blur": 4},
                "background": "#FFFFFFFF",
                "line_spacing": 1.5,
            }],
        })

        props = result["root"]["children"][0]["props"]
        assert props["text"] == "styled"
        assert props["font"] == "Arial"
        assert props["font_size"] == 64
        assert props["color"] == "#FFFFFF"
        assert props["align"] == "center"
        assert props["stroke"] == {"color": "#000000", "width": 2.0}
        assert props["shadow"] == {"color": "#333333", "dx": 2, "dy": 2, "blur": 4}
        assert props["background"] == "#FFFFFFFF"
        assert props["line_spacing"] == 1.5

    def test_set_text_layer_not_found(self):
        """set_text on nonexistent layer raises E_NOT_FOUND."""
        doc = {"root": model.new_layer("composition"), "selection": []}
        with pytest.raises(LayerPatchError) as exc:
            apply_layer_patch(doc, {
                "version": 1,
                "ops": [{"op": "set_text", "layer_id": "nonexistent", "text": "hi"}],
            })
        assert exc.value.code == "E_NOT_FOUND"

    def test_set_text_missing_layer_id(self):
        """set_text without layer_id raises E_ARG."""
        doc = {"root": model.new_layer("composition"), "selection": []}
        with pytest.raises(LayerPatchError) as exc:
            apply_layer_patch(doc, {
                "version": 1,
                "ops": [{"op": "set_text", "text": "hi"}],
            })
        assert exc.value.code == "E_ARG"


class TestSetVolume:
    """Tests for set_volume op."""

    def test_set_volume_basic(self):
        """set_volume writes to props.volume."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("audio"),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "set_volume", "layer_id": layer_id, "volume": 0.8}],
        })

        assert result["root"]["children"][0]["props"]["volume"] == 0.8

    def test_set_volume_clamps_to_nonnegative(self):
        """Volume can be any nonnegative float; no clamping in op."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("audio"),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "set_volume", "layer_id": layer_id, "volume": 2.5}],
        })

        assert result["root"]["children"][0]["props"]["volume"] == 2.5

    def test_set_volume_missing_layer_id(self):
        """set_volume without layer_id raises E_ARG."""
        doc = {"root": model.new_layer("composition"), "selection": []}
        with pytest.raises(LayerPatchError) as exc:
            apply_layer_patch(doc, {
                "version": 1,
                "ops": [{"op": "set_volume", "volume": 0.8}],
            })
        assert exc.value.code == "E_ARG"

    def test_set_volume_missing_volume(self):
        """set_volume without volume raises E_ARG."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("audio"),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        with pytest.raises(LayerPatchError) as exc:
            apply_layer_patch(doc, {
                "version": 1,
                "ops": [{"op": "set_volume", "layer_id": layer_id}],
            })
        assert exc.value.code == "E_ARG"


class TestSetAudioFade:
    """Tests for set_audio_fade op."""

    def test_set_audio_fade_in_only(self):
        """set_audio_fade can set fade_in alone."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("audio"),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "set_audio_fade", "layer_id": layer_id, "fade_in": 0.5}],
        })

        props = result["root"]["children"][0]["props"]
        assert props["fade_in"] == 0.5
        assert "fade_out" not in props

    def test_set_audio_fade_out_only(self):
        """set_audio_fade can set fade_out alone."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("audio"),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "set_audio_fade", "layer_id": layer_id, "fade_out": 1.0}],
        })

        props = result["root"]["children"][0]["props"]
        assert props["fade_out"] == 1.0
        assert "fade_in" not in props

    def test_set_audio_fade_both(self):
        """set_audio_fade can set both fade_in and fade_out."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("audio"),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "set_audio_fade", "layer_id": layer_id, "fade_in": 0.3, "fade_out": 0.7}],
        })

        props = result["root"]["children"][0]["props"]
        assert props["fade_in"] == 0.3
        assert props["fade_out"] == 0.7


class TestMuteLayer:
    """Tests for mute_layer op."""

    def test_mute_layer_default_true(self):
        """mute_layer with no muted arg defaults to true."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("audio"),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "mute_layer", "layer_id": layer_id}],
        })

        assert result["root"]["children"][0]["props"]["muted"] is True

    def test_mute_layer_explicit_true(self):
        """mute_layer with muted=true."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("audio"),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "mute_layer", "layer_id": layer_id, "muted": True}],
        })

        assert result["root"]["children"][0]["props"]["muted"] is True

    def test_mute_layer_explicit_false(self):
        """mute_layer with muted=false."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("audio", props={"muted": True}),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "mute_layer", "layer_id": layer_id, "muted": False}],
        })

        assert result["root"]["children"][0]["props"]["muted"] is False


class TestAnimateLayer:
    """Tests for animate_layer op with animation presets."""

    def test_animate_fade_in(self):
        """fade_in preset creates opacity keyframes."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("video", start=0.0, duration=5.0),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "animate_layer", "layer_id": layer_id, "preset": "fade_in", "duration": 0.5}],
        })

        keyframes = result["root"]["children"][0]["keyframes"]
        assert "opacity" in keyframes
        track = keyframes["opacity"]
        assert len(track) == 2
        assert track[0]["t"] == 0.0
        assert track[0]["value"] == 0.0
        assert track[1]["t"] == 0.5
        assert track[1]["value"] == 1.0

    def test_animate_fade_out(self):
        """fade_out preset creates opacity keyframes at layer end."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("video", start=0.0, duration=5.0),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "animate_layer", "layer_id": layer_id, "preset": "fade_out", "duration": 1.0}],
        })

        keyframes = result["root"]["children"][0]["keyframes"]
        track = keyframes["opacity"]
        assert len(track) == 2
        assert track[0]["t"] == 4.0  # layer end - 1.0
        assert track[0]["value"] == 1.0
        assert track[1]["t"] == 5.0  # layer end
        assert track[1]["value"] == 0.0

    def test_animate_fly_in_left(self):
        """fly_in_left preset creates transform.x keyframes."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("image", start=0.0, duration=3.0),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "animate_layer", "layer_id": layer_id, "preset": "fly_in_left", "duration": 0.8}],
        })

        keyframes = result["root"]["children"][0]["keyframes"]
        assert "transform.x" in keyframes
        track = keyframes["transform.x"]
        assert len(track) == 2
        assert track[0]["value"] == -1920.0
        assert track[1]["value"] == 0.0

    def test_animate_fly_in_right(self):
        """fly_in_right preset creates transform.x keyframes (positive)."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("image", start=1.0, duration=2.0),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "animate_layer", "layer_id": layer_id, "preset": "fly_in_right", "duration": 0.5}],
        })

        keyframes = result["root"]["children"][0]["keyframes"]
        track = keyframes["transform.x"]
        assert track[0]["value"] == 1920.0
        assert track[1]["value"] == 0.0

    def test_animate_fly_in_top(self):
        """fly_in_top preset creates transform.y keyframes (negative)."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("text", start=0.0, duration=2.0),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "animate_layer", "layer_id": layer_id, "preset": "fly_in_top", "duration": 0.3}],
        })

        keyframes = result["root"]["children"][0]["keyframes"]
        track = keyframes["transform.y"]
        assert track[0]["value"] == -1080.0
        assert track[1]["value"] == 0.0

    def test_animate_fly_in_bottom(self):
        """fly_in_bottom preset creates transform.y keyframes (positive)."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("image", start=0.0, duration=2.0),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "animate_layer", "layer_id": layer_id, "preset": "fly_in_bottom", "duration": 0.4}],
        })

        keyframes = result["root"]["children"][0]["keyframes"]
        track = keyframes["transform.y"]
        assert track[0]["value"] == 1080.0
        assert track[1]["value"] == 0.0

    def test_animate_fly_out_left(self):
        """fly_out_left preset creates transform.x keyframes at layer end."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("image", start=0.0, duration=4.0),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "animate_layer", "layer_id": layer_id, "preset": "fly_out_left", "duration": 0.5}],
        })

        keyframes = result["root"]["children"][0]["keyframes"]
        track = keyframes["transform.x"]
        assert len(track) == 2
        assert track[0]["t"] == 3.5  # layer end - 0.5
        assert track[0]["value"] == 0.0
        assert track[1]["t"] == 4.0  # layer end
        assert track[1]["value"] == -1920.0

    def test_animate_fly_out_right(self):
        """fly_out_right preset."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("image", start=0.0, duration=3.0),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "animate_layer", "layer_id": layer_id, "preset": "fly_out_right", "duration": 0.6}],
        })

        keyframes = result["root"]["children"][0]["keyframes"]
        track = keyframes["transform.x"]
        assert track[0]["value"] == 0.0
        assert track[1]["value"] == 1920.0

    def test_animate_fly_out_top(self):
        """fly_out_top preset."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("image", start=0.5, duration=2.5),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "animate_layer", "layer_id": layer_id, "preset": "fly_out_top", "duration": 0.3}],
        })

        keyframes = result["root"]["children"][0]["keyframes"]
        track = keyframes["transform.y"]
        assert track[0]["value"] == 0.0
        assert track[1]["value"] == -1080.0

    def test_animate_fly_out_bottom(self):
        """fly_out_bottom preset."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("image", start=0.0, duration=2.0),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "animate_layer", "layer_id": layer_id, "preset": "fly_out_bottom", "duration": 0.4}],
        })

        keyframes = result["root"]["children"][0]["keyframes"]
        track = keyframes["transform.y"]
        assert track[0]["value"] == 0.0
        assert track[1]["value"] == 1080.0

    def test_animate_zoom_in(self):
        """zoom_in preset creates scale keyframes."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("image", start=0.0, duration=3.0),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "animate_layer", "layer_id": layer_id, "preset": "zoom_in", "duration": 1.0}],
        })

        keyframes = result["root"]["children"][0]["keyframes"]
        assert "transform.scale_x" in keyframes
        assert "transform.scale_y" in keyframes
        for prop in ("transform.scale_x", "transform.scale_y"):
            track = keyframes[prop]
            assert len(track) == 2
            assert track[0]["value"] == 1.0
            assert track[1]["value"] == 1.5

    def test_animate_zoom_out(self):
        """zoom_out preset creates scale keyframes (shrinking)."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("image", start=0.0, duration=2.0),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "animate_layer", "layer_id": layer_id, "preset": "zoom_out", "duration": 0.5}],
        })

        keyframes = result["root"]["children"][0]["keyframes"]
        for prop in ("transform.scale_x", "transform.scale_y"):
            track = keyframes[prop]
            assert track[0]["value"] == 1.5
            assert track[1]["value"] == 1.0

    def test_animate_ken_burns(self):
        """ken_burns preset creates scale and pan keyframes over full duration."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("image", start=1.0, duration=5.0),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "animate_layer", "layer_id": layer_id, "preset": "ken_burns"}],
        })

        keyframes = result["root"]["children"][0]["keyframes"]
        # Scale should span full duration
        assert "transform.scale_x" in keyframes
        assert "transform.scale_y" in keyframes
        for prop in ("transform.scale_x", "transform.scale_y"):
            track = keyframes[prop]
            assert track[0]["t"] == 1.0
            assert track[1]["t"] == 6.0  # start + duration
            assert track[0]["value"] == 1.0
            assert track[1]["value"] == 1.1

        # Pan keyframes (x and y)
        assert "transform.x" in keyframes
        assert "transform.y" in keyframes
        for prop in ("transform.x", "transform.y"):
            track = keyframes[prop]
            assert len(track) == 2
            assert track[0]["t"] == 1.0
            assert track[1]["t"] == 6.0

    def test_animate_easing_linear(self):
        """easing parameter maps to interp."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("image", start=0.0, duration=2.0),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "animate_layer", "layer_id": layer_id, "preset": "fade_in", "duration": 0.5, "easing": "linear"}],
        })

        track = result["root"]["children"][0]["keyframes"]["opacity"]
        assert track[0]["interp"] == "linear"
        assert track[1]["interp"] == "linear"

    def test_animate_easing_ease(self):
        """easing='ease'."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("image", start=0.0, duration=2.0),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "animate_layer", "layer_id": layer_id, "preset": "fade_in", "duration": 0.5, "easing": "ease"}],
        })

        track = result["root"]["children"][0]["keyframes"]["opacity"]
        assert track[0]["interp"] == "ease"

    def test_animate_duration_clamped_to_layer_duration(self):
        """If requested duration exceeds layer duration, it is clamped."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("image", start=0.0, duration=0.5),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        result = apply_layer_patch(doc, {
            "version": 1,
            "ops": [{"op": "animate_layer", "layer_id": layer_id, "preset": "fade_in", "duration": 10.0}],
        })

        track = result["root"]["children"][0]["keyframes"]["opacity"]
        # Should be clamped to layer duration (0.5)
        assert track[1]["t"] == 0.5

    def test_animate_unknown_preset(self):
        """Unknown preset raises E_ARG."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("image", start=0.0, duration=2.0),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        with pytest.raises(LayerPatchError) as exc:
            apply_layer_patch(doc, {
                "version": 1,
                "ops": [{"op": "animate_layer", "layer_id": layer_id, "preset": "unknown_preset"}],
            })
        assert exc.value.code == "E_ARG"

    def test_animate_missing_preset(self):
        """Missing preset raises E_ARG."""
        doc = {
            "root": model.new_layer("composition", children=[
                model.new_layer("image", start=0.0, duration=2.0),
            ]),
            "selection": [],
        }
        layer_id = doc["root"]["children"][0]["id"]

        with pytest.raises(LayerPatchError) as exc:
            apply_layer_patch(doc, {
                "version": 1,
                "ops": [{"op": "animate_layer", "layer_id": layer_id}],
            })
        assert exc.value.code == "E_ARG"


class TestCatalogSync:
    """Verify catalog has entries for all new ops."""

    def test_catalog_has_set_text(self):
        """set_text is in the catalog."""
        from lumenframe.catalog import CORE_OPS_CATALOG
        names = {e["op"] for e in CORE_OPS_CATALOG}
        assert "set_text" in names

    def test_catalog_has_set_volume(self):
        """set_volume is in the catalog."""
        from lumenframe.catalog import CORE_OPS_CATALOG
        names = {e["op"] for e in CORE_OPS_CATALOG}
        assert "set_volume" in names

    def test_catalog_has_set_audio_fade(self):
        """set_audio_fade is in the catalog."""
        from lumenframe.catalog import CORE_OPS_CATALOG
        names = {e["op"] for e in CORE_OPS_CATALOG}
        assert "set_audio_fade" in names

    def test_catalog_has_mute_layer(self):
        """mute_layer is in the catalog."""
        from lumenframe.catalog import CORE_OPS_CATALOG
        names = {e["op"] for e in CORE_OPS_CATALOG}
        assert "mute_layer" in names

    def test_catalog_has_animate_layer(self):
        """animate_layer is in the catalog."""
        from lumenframe.catalog import CORE_OPS_CATALOG
        names = {e["op"] for e in CORE_OPS_CATALOG}
        assert "animate_layer" in names

    def test_catalog_groups_text(self):
        """set_text has group 'text'."""
        from lumenframe.catalog import CORE_OPS_CATALOG
        entry = next((e for e in CORE_OPS_CATALOG if e["op"] == "set_text"), None)
        assert entry is not None
        assert entry["group"] == "text"

    def test_catalog_groups_audio(self):
        """Audio ops have group 'audio'."""
        from lumenframe.catalog import CORE_OPS_CATALOG
        audio_ops = {e["op"] for e in CORE_OPS_CATALOG if e["group"] == "audio"}
        assert "set_volume" in audio_ops
        assert "set_audio_fade" in audio_ops
        assert "mute_layer" in audio_ops

    def test_catalog_groups_animation(self):
        """animate_layer has group 'animation'."""
        from lumenframe.catalog import CORE_OPS_CATALOG
        entry = next((e for e in CORE_OPS_CATALOG if e["op"] == "animate_layer"), None)
        assert entry is not None
        assert entry["group"] == "animation"
