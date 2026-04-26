"""Tests for KeyframeTrack and apply_animated_op."""
import numpy as np
import pytest

from gemia.video.compositing_graph import build_compositing_graph_from_layer_plan
from gemia.video.keyframe import (
    KeyframeTrack,
    adjust_keyframe_tracks_for_clips,
    apply_animated_op,
    retime_keyframe_track,
)
from gemia.video.layers import render_layer_plan


class TestKeyframeTrack:
    def test_empty_track_returns_zero(self):
        track = KeyframeTrack()
        assert track.evaluate(0.5) == 0.0

    def test_single_keyframe(self):
        track = KeyframeTrack()
        track.add_keyframe(0.0, 42.0)
        assert track.evaluate(0.0) == 42.0
        assert track.evaluate(1.0) == 42.0
        assert track.evaluate(-1.0) == 42.0

    def test_linear_interpolation(self):
        track = KeyframeTrack()
        track.add_keyframe(0.0, 0.0)
        track.add_keyframe(1.0, 1.0)
        val = track.evaluate(0.5)
        assert abs(val - 0.5) < 1e-5

    def test_linear_at_endpoints(self):
        track = KeyframeTrack()
        track.add_keyframe(0.0, 10.0)
        track.add_keyframe(2.0, 20.0)
        assert abs(track.evaluate(0.0) - 10.0) < 1e-5
        assert abs(track.evaluate(2.0) - 20.0) < 1e-5

    def test_ease_in(self):
        track = KeyframeTrack()
        track.add_keyframe(0.0, 0.0, easing="ease_in")
        track.add_keyframe(1.0, 1.0)
        mid = track.evaluate(0.5)
        assert mid < 0.5

    def test_ease_out(self):
        track = KeyframeTrack()
        track.add_keyframe(0.0, 0.0, easing="ease_out")
        track.add_keyframe(1.0, 1.0)
        mid = track.evaluate(0.5)
        assert mid > 0.5

    def test_ease_in_out(self):
        track = KeyframeTrack()
        track.add_keyframe(0.0, 0.0, easing="ease_in_out")
        track.add_keyframe(1.0, 1.0)
        mid = track.evaluate(0.5)
        assert abs(mid - 0.5) < 0.01

    def test_bezier(self):
        track = KeyframeTrack()
        track.add_keyframe(0.0, 0.0, easing="bezier")
        track.add_keyframe(1.0, 1.0)
        val = track.evaluate(0.5)
        assert 0.0 <= val <= 1.0

    def test_invalid_easing_raises(self):
        track = KeyframeTrack()
        with pytest.raises(ValueError):
            track.add_keyframe(0.0, 0.0, easing="magic_easing")

    def test_out_of_bounds_clamped(self):
        track = KeyframeTrack()
        track.add_keyframe(1.0, 5.0)
        track.add_keyframe(2.0, 10.0)
        assert track.evaluate(0.0) == 5.0
        assert track.evaluate(3.0) == 10.0

    def test_keyframes_sorted_on_add(self):
        track = KeyframeTrack()
        track.add_keyframe(1.0, 1.0)
        track.add_keyframe(0.0, 0.0)
        assert abs(track.evaluate(0.5) - 0.5) < 1e-5

    def test_multiple_segments(self):
        track = KeyframeTrack()
        track.add_keyframe(0.0, 0.0)
        track.add_keyframe(1.0, 10.0)
        track.add_keyframe(2.0, 0.0)
        assert abs(track.evaluate(1.0) - 10.0) < 1e-5
        assert track.evaluate(1.5) > 0.0

    def test_all_easings_run(self):
        for easing in ["linear", "ease_in", "ease_out", "ease_in_out", "bezier"]:
            track = KeyframeTrack()
            track.add_keyframe(0.0, 0.0, easing=easing)
            track.add_keyframe(1.0, 1.0)
            val = track.evaluate(0.5)
            assert 0.0 <= val <= 1.0, f"easing={easing} out of range: {val}"

    def test_loop_mode_wraps_time(self):
        track = KeyframeTrack(mode="loop")
        track.add_keyframe(0.0, 0.0)
        track.add_keyframe(10.0, 100.0)
        assert abs(track.evaluate(12.5) - 25.0) < 1e-5

    def test_pingpong_mode_reflects_time(self):
        track = KeyframeTrack(mode="pingpong")
        track.add_keyframe(0.0, 0.0)
        track.add_keyframe(10.0, 100.0)
        assert abs(track.evaluate(12.5) - 75.0) < 1e-5

    def test_relative_mode_offsets_time(self):
        track = KeyframeTrack(mode="relative", relative_to=10.0)
        track.add_keyframe(0.0, 0.0)
        track.add_keyframe(10.0, 100.0)
        assert abs(track.evaluate(15.0) - 50.0) < 1e-5

    def test_custom_bezier_easing_runs(self):
        track = KeyframeTrack()
        track.add_keyframe(0.0, 0.0, easing="bezier(0.2,0.0,0.1,1.0)")
        track.add_keyframe(1.0, 1.0)
        assert 0.0 <= track.evaluate(0.35) <= 1.0

    def test_retime_keyframe_track_adjusts_time_and_value(self):
        track = KeyframeTrack(mode="relative", relative_to=5.0)
        track.add_keyframe(0.0, 1.0)
        track.add_keyframe(4.0, 3.0)
        retimed = retime_keyframe_track(
            track,
            time_scale=2.0,
            time_offset=10.0,
            value_scale=10.0,
            value_offset=-5.0,
        )
        assert retimed.mode == "relative"
        assert retimed.to_curve_metadata()["keyframes"] == [
            {"time": 10.0, "value": 5.0, "easing": "linear"},
            {"time": 18.0, "value": 25.0, "easing": "linear"},
        ]

    def test_multi_clip_adjustment_keeps_clip_offsets(self):
        first = KeyframeTrack()
        first.add_keyframe(0.0, 0.0)
        second = KeyframeTrack()
        second.add_keyframe(0.0, 1.0)
        adjusted = adjust_keyframe_tracks_for_clips(
            {"a": {"opacity": first}, "b": {"opacity": second}},
            {"a": 0.0, "b": 20.0},
            value_offset=2.0,
        )
        assert adjusted["a"]["opacity"].to_curve_metadata()["keyframes"][0]["time"] == 0.0
        assert adjusted["b"]["opacity"].to_curve_metadata()["keyframes"][0]["time"] == 20.0
        assert adjusted["b"]["opacity"].to_curve_metadata()["keyframes"][0]["value"] == 3.0

    def test_layer_plan_curve_metadata_includes_modes(self, tmp_path):
        plan = {
            "width": 96,
            "height": 54,
            "fps": 12.0,
            "total_frames": 8,
            "layers": [
                {"id": "bg", "type": "solid", "color": [0.0, 0.0, 0.0, 1.0], "end_frame": 8},
                {
                    "id": "box",
                    "type": "solid",
                    "color": [1.0, 0.8, 0.1, 0.85],
                    "position": [8, 8],
                    "z_index": 1,
                    "end_frame": 8,
                    "keyframes": {
                        "opacity": {
                            "mode": "pingpong",
                            "points": [
                                {"time": 0, "value": 0.1, "easing": "linear"},
                                {"time": 4, "value": 0.9, "easing": "bezier(0.2,0,0.1,1)"},
                            ],
                        }
                    },
                },
            ],
        }
        out = tmp_path / "curves.mp4"

        render_layer_plan(plan, out)
        graph = build_compositing_graph_from_layer_plan(plan)
        automation = next(node for node in graph.nodes.values() if node.kind == "automation")
        opacity = automation.params["tracks"]["opacity"]

        assert out.exists()
        assert opacity["mode"] == "pingpong"
        assert opacity["keyframes"][1]["easing"] == "bezier(0.2,0,0.1,1)"


class TestApplyAnimatedOp:
    def test_returns_path(self, sample_video_path, tmp_path):
        from gemia.picture.color import adjust_exposure
        out = str(tmp_path / "animated.mp4")
        track = KeyframeTrack()
        track.add_keyframe(0.0, 0.0)
        track.add_keyframe(1.0, 1.0)
        result = apply_animated_op(
            sample_video_path, out,
            op_fn=adjust_exposure,
            param_name="stops",
            keyframe_track=track,
        )
        assert result == out

    def test_output_exists(self, sample_video_path, tmp_path):
        import os
        from gemia.picture.color import adjust_exposure
        out = str(tmp_path / "animated2.mp4")
        track = KeyframeTrack()
        track.add_keyframe(0.0, 0.0)
        apply_animated_op(
            sample_video_path, out,
            op_fn=adjust_exposure,
            param_name="stops",
            keyframe_track=track,
        )
        assert os.path.exists(out)

    def test_invalid_path_raises(self, tmp_path):
        from gemia.picture.color import adjust_exposure
        out = str(tmp_path / "out.mp4")
        track = KeyframeTrack()
        with pytest.raises(FileNotFoundError):
            apply_animated_op(
                "/nonexistent/video.mp4", out,
                op_fn=adjust_exposure,
                param_name="stops",
                keyframe_track=track,
            )
