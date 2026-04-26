from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image as PILImage

from gemia.video.layers import (
    Layer,
    LayerStack,
    execute_layer_plan,
    make_image_layer,
    make_text_layer,
)


def _solid_frame(color: tuple[float, float, float, float], size: tuple[int, int] = (8, 8)) -> np.ndarray:
    h, w = size
    return np.tile(np.array(color, dtype=np.float32), (h, w, 1))


class TestLayerStack:
    def test_render_frame_normal_alpha_over(self) -> None:
        stack = LayerStack(width=4, height=4, fps=30.0, total_frames=1)
        base = Layer(
            id="base",
            name="base",
            content_fn=lambda _i: _solid_frame((1.0, 0.0, 0.0, 1.0), (4, 4)),
        )
        top = Layer(
            id="top",
            name="top",
            z_index=1,
            content_fn=lambda _i: _solid_frame((0.0, 0.0, 1.0, 0.5), (4, 4)),
        )
        stack.add_layer(base)
        stack.add_layer(top)

        frame = stack.render_frame(0)
        pixel = frame[0, 0]
        assert frame.dtype == np.float32
        assert frame.shape == (4, 4, 4)
        assert np.allclose(pixel[:3], [0.5, 0.0, 0.5], atol=1e-5)
        assert np.isclose(pixel[3], 1.0, atol=1e-6)

    def test_render_frame_multiply_keeps_source_over_transparent_backdrop(self) -> None:
        stack = LayerStack(width=4, height=4, fps=30.0, total_frames=1)
        top = Layer(
            id="top",
            name="top",
            blend_mode="multiply",
            content_fn=lambda _i: _solid_frame((0.2, 0.4, 0.6, 1.0), (4, 4)),
        )
        stack.add_layer(top)

        frame = stack.render_frame(0)
        assert np.allclose(frame[0, 0, :3], [0.2, 0.4, 0.6], atol=1e-5)
        assert np.isclose(frame[0, 0, 3], 1.0, atol=1e-6)

    def test_render_frame_screen_blend_mode(self) -> None:
        stack = LayerStack(width=2, height=2, fps=30.0, total_frames=1)
        stack.add_layer(
            Layer(
                id="base",
                name="base",
                content_fn=lambda _i: _solid_frame((0.2, 0.5, 0.8, 1.0), (2, 2)),
            )
        )
        stack.add_layer(
            Layer(
                id="top",
                name="top",
                z_index=1,
                blend_mode="screen",
                content_fn=lambda _i: _solid_frame((0.4, 0.3, 0.1, 1.0), (2, 2)),
            )
        )

        frame = stack.render_frame(0)

        assert np.allclose(frame[0, 0, :3], [0.52, 0.65, 0.82], atol=1e-5)
        assert np.isclose(frame[0, 0, 3], 1.0, atol=1e-6)

    def test_render_frame_overlay_blend_mode(self) -> None:
        stack = LayerStack(width=2, height=2, fps=30.0, total_frames=1)
        stack.add_layer(
            Layer(
                id="base",
                name="base",
                content_fn=lambda _i: _solid_frame((0.2, 0.5, 0.8, 1.0), (2, 2)),
            )
        )
        stack.add_layer(
            Layer(
                id="top",
                name="top",
                z_index=1,
                blend_mode="overlay",
                content_fn=lambda _i: _solid_frame((0.4, 0.3, 0.1, 1.0), (2, 2)),
            )
        )

        frame = stack.render_frame(0)

        assert np.allclose(frame[0, 0, :3], [0.16, 0.3, 0.64], atol=1e-5)
        assert np.isclose(frame[0, 0, 3], 1.0, atol=1e-6)

    def test_opacity_keyframe_track_uses_frame_indices(self) -> None:
        plan = {
            "width": 2,
            "height": 2,
            "fps": 30.0,
            "total_frames": 10,
            "layers": [
                {
                    "id": "bg",
                    "type": "solid",
                    "color": [0.0, 0.0, 0.0, 1.0],
                    "z_index": 0,
                    "start_frame": 0,
                    "end_frame": 10,
                },
                {
                    "id": "fg",
                    "type": "solid",
                    "color": [1.0, 1.0, 1.0, 1.0],
                    "z_index": 1,
                    "start_frame": 0,
                    "end_frame": 10,
                    "keyframes": {
                        "opacity": {"0": 0.0, "5": 1.0}
                    },
                },
            ],
        }
        stack = execute_layer_plan(plan)

        early = stack.render_frame(0)
        mid = stack.render_frame(5)
        assert np.allclose(early[0, 0, :3], [0.0, 0.0, 0.0], atol=1e-6)
        assert np.allclose(mid[0, 0, :3], [1.0, 1.0, 1.0], atol=1e-6)

    def test_scale_transform_changes_layer_coverage(self) -> None:
        stack = LayerStack(width=5, height=5, fps=30.0, total_frames=1)
        layer = Layer(
            id="scaled",
            name="scaled",
            scale=2.0,
            position=(1, 1),
            content_fn=lambda _i: _solid_frame((1.0, 1.0, 1.0, 1.0), (1, 1)),
        )
        stack.add_layer(layer)

        frame = stack.render_frame(0)
        assert np.count_nonzero(frame[..., 3] > 0.0) >= 4

    def test_rotation_transform_preserves_visible_alpha(self) -> None:
        stack = LayerStack(width=6, height=6, fps=30.0, total_frames=1)
        layer = Layer(
            id="rotated",
            name="rotated",
            rotation_deg=45.0,
            position=(1, 1),
            content_fn=lambda _i: _solid_frame((0.0, 1.0, 0.0, 1.0), (2, 2)),
        )
        stack.add_layer(layer)

        frame = stack.render_frame(0)
        assert np.max(frame[..., 3]) > 0.0
        assert np.count_nonzero(frame[..., 1] > 0.0) >= 4


class TestLayerFactories:
    def test_make_image_layer_returns_static_rgba(self, tmp_path: Path) -> None:
        img_path = tmp_path / "still.png"
        PILImage.new("RGBA", (6, 5), (25, 50, 75, 255)).save(img_path)

        layer = make_image_layer(str(img_path), duration=12)
        frame = layer.frame_content(3)
        assert frame.shape == (5, 6, 4)
        assert np.allclose(frame[0, 0], [25 / 255.0, 50 / 255.0, 75 / 255.0, 1.0], atol=1e-6)

    def test_make_text_layer_renders_non_empty_alpha(self) -> None:
        layer = make_text_layer("Hello", position=(10, 12), font_config={"size": 24})
        frame = layer.frame_content(0)
        assert frame.shape[2] == 4
        assert np.max(frame[..., 3]) > 0.0


class TestExecuteLayerPlan:
    def test_execute_layer_plan_resolves_duration_relative_to_start_frame(self) -> None:
        plan = {
            "width": 32,
            "height": 32,
            "fps": 30.0,
            "total_frames": 10,
            "layers": [
                {
                    "id": "caption",
                    "type": "text",
                    "text": "Hello",
                    "position": [2, 2],
                    "start_frame": 4,
                    "duration": 3,
                }
            ],
        }

        stack = execute_layer_plan(plan)
        layer = next(item for item in stack.layers if item.id == "caption")

        assert layer.start_frame == 4
        assert layer.end_frame == 7
        assert not layer.is_active(3)
        assert layer.is_active(4)
        assert layer.is_active(6)
        assert not layer.is_active(7)

    def test_execute_layer_plan_offsets_video_duration_by_start_frame(
        self,
        sample_video_path: str,
    ) -> None:
        plan = {
            "layers": [
                {
                    "id": "clip",
                    "type": "video",
                    "source": sample_video_path,
                    "start_frame": 4,
                }
            ],
        }

        stack = execute_layer_plan(plan)
        layer = next(item for item in stack.layers if item.id == "clip")

        assert stack.width == 128
        assert stack.height == 128
        assert np.isclose(stack.fps, 15.0)
        assert stack.total_frames == 34
        assert layer.start_frame == 4
        assert layer.end_frame == 34
        assert not layer.is_active(3)
        assert layer.is_active(4)
        assert layer.is_active(33)
        assert not layer.is_active(34)

    def test_execute_layer_plan_with_image_primitive_chain(self, tmp_path: Path) -> None:
        img_path = tmp_path / "input.png"
        arr = np.zeros((4, 4, 4), dtype=np.uint8)
        arr[..., 0] = 64
        arr[..., 1] = 64
        arr[..., 2] = 64
        arr[..., 3] = 255
        PILImage.fromarray(arr, mode="RGBA").save(img_path)

        plan = {
            "width": 4,
            "height": 4,
            "fps": 30.0,
            "total_frames": 2,
            "layers": [
                {
                    "id": "img",
                    "type": "image",
                    "source": str(img_path),
                    "duration": 2,
                    "primitives": [["adjust_exposure", {"stops": 1.0}]],
                    "z_index": 0,
                    "start_frame": 0,
                    "end_frame": 2,
                }
            ],
        }

        stack = execute_layer_plan(plan)
        frame = stack.render_frame(0)
        assert np.all(frame[..., 3] == 1.0)
        assert np.isclose(frame[0, 0, 0], 128 / 255.0, atol=1e-3)
        assert np.isclose(frame[0, 0, 1], 128 / 255.0, atol=1e-3)
        assert np.isclose(frame[0, 0, 2], 128 / 255.0, atol=1e-3)

    def test_execute_layer_plan_with_mask_source(self, tmp_path: Path) -> None:
        img_path = tmp_path / "input.png"
        mask_path = tmp_path / "mask.png"
        PILImage.new("RGBA", (2, 2), (255, 0, 0, 255)).save(img_path)
        mask = np.zeros((2, 2, 4), dtype=np.uint8)
        mask[0, 0, 3] = 255
        PILImage.fromarray(mask, mode="RGBA").save(mask_path)

        plan = {
            "width": 2,
            "height": 2,
            "fps": 30.0,
            "total_frames": 1,
            "layers": [
                {
                    "id": "masked",
                    "type": "image",
                    "source": str(img_path),
                    "duration": 1,
                    "mask_source": str(mask_path),
                }
            ],
        }

        stack = execute_layer_plan(plan)
        frame = stack.render_frame(0)
        assert np.isclose(frame[0, 0, 3], 1.0, atol=1e-6)
        assert np.isclose(frame[1, 1, 3], 0.0, atol=1e-6)
