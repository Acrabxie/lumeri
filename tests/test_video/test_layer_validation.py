from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image as PILImage

from gemia.video.layer_validation import (
    LayerPlanValidationError,
    validate_layer_plan,
    validate_layer_stack_preview,
)
from gemia.video.layers import execute_layer_plan


def _write_rgba_image(path: Path, *, size: tuple[int, int] = (4, 4), color: tuple[int, int, int, int]) -> None:
    PILImage.new("RGBA", size, color).save(path)


def _assert_error_contains(exc: LayerPlanValidationError, *snippets: str) -> None:
    message = str(exc)
    for snippet in snippets:
        assert snippet in message, message


class TestLayerPlanValidation:
    def test_video_plan_smoke_preview_is_renderable(self, sample_video_path: str) -> None:
        plan = {
            "layers": [
                {
                    "id": "clip",
                    "type": "video",
                    "source": sample_video_path,
                    "start_frame": 0,
                }
            ],
        }

        validate_layer_plan(plan)
        stack = execute_layer_plan(plan)
        sample_frames = validate_layer_stack_preview(stack)

        assert sample_frames
        frame = stack.render_frame(0)
        assert frame.shape == (stack.height, stack.width, 4)
        assert frame.dtype.name == "float32"

    def test_execute_layer_plan_rejects_schema_and_timeline_mismatches(self, tmp_path: Path) -> None:
        img_path = tmp_path / "input.png"
        _write_rgba_image(img_path, color=(255, 0, 0, 255))
        plan = {
            "width": 0,
            "height": 4,
            "fps": 30.0,
            "total_frames": 4,
            "layers": [
                {
                    "id": "dup",
                    "type": "image",
                    "source": str(img_path),
                    "start_frame": 0,
                    "end_frame": 6,
                },
                {
                    "id": "dup",
                    "type": "solid",
                    "color": [1.0, 1.0, 1.0, 1.0],
                    "start_frame": 3,
                    "end_frame": 2,
                },
            ],
        }

        with pytest.raises(LayerPlanValidationError) as exc_info:
            execute_layer_plan(plan)

        _assert_error_contains(
            exc_info.value,
            "width must be > 0",
            "ends at frame 6, beyond plan.total_frames=4",
            "duplicates layers[0].id",
            "end_frame must be greater than start_frame (3), got 2",
        )

    def test_non_video_layers_need_explicit_preview_length(self) -> None:
        plan = {
            "layers": [
                {
                    "id": "title",
                    "type": "text",
                    "text": "Hello",
                }
            ],
        }

        with pytest.raises(LayerPlanValidationError) as exc_info:
            validate_layer_plan(plan)

        _assert_error_contains(
            exc_info.value,
            "needs duration/end_frame or plan.total_frames so preview length is explicit",
        )

    def test_bad_layer_inputs_fail_with_aggregated_messages(self, tmp_path: Path) -> None:
        img_path = tmp_path / "input.png"
        _write_rgba_image(img_path, color=(255, 255, 255, 255))
        plan = {
            "width": 4,
            "height": 4,
            "fps": 30.0,
            "total_frames": 6,
            "layers": [
                {
                    "id": "img",
                    "type": "image",
                    "source": str(img_path),
                    "position": [0],
                    "blend_mode": "soft_light",
                    "opacity": 1.2,
                    "scale": 0,
                    "keyframes": {
                        "x_position": {"0": 1.0},
                    },
                }
            ],
        }

        with pytest.raises(LayerPlanValidationError) as exc_info:
            validate_layer_plan(plan)

        _assert_error_contains(
            exc_info.value,
            "position must be a 2-item (x, y) sequence",
            "blend_mode must be one of multiply, normal, overlay, screen, got 'soft_light'",
            "opacity must be within [0.0, 1.0], got 1.2",
            "scale must be > 0, got 0.0",
            "keyframes.x_position is unsupported",
        )

    def test_screen_and_overlay_blend_modes_are_valid(self, tmp_path: Path) -> None:
        img_path = tmp_path / "input.png"
        _write_rgba_image(img_path, color=(255, 255, 255, 255))
        plan = {
            "width": 4,
            "height": 4,
            "fps": 30.0,
            "total_frames": 3,
            "layers": [
                {
                    "id": "screen",
                    "type": "image",
                    "source": str(img_path),
                    "duration": 3,
                    "blend_mode": "screen",
                },
                {
                    "id": "overlay",
                    "type": "solid",
                    "color": [0.2, 0.4, 0.6, 0.5],
                    "duration": 3,
                    "blend_mode": "overlay",
                    "z_index": 1,
                },
            ],
        }

        validate_layer_plan(plan)

    def test_missing_mask_and_non_picture_primitives_are_rejected(self, tmp_path: Path) -> None:
        img_path = tmp_path / "input.png"
        _write_rgba_image(img_path, color=(255, 255, 255, 255))
        plan = {
            "width": 4,
            "height": 4,
            "fps": 30.0,
            "total_frames": 3,
            "layers": [
                {
                    "id": "img",
                    "type": "image",
                    "source": str(img_path),
                    "mask_source": str(tmp_path / "missing-mask.png"),
                    "primitives": [
                        ["gemia.video.timeline.cut", {}],
                        ["not_a_real_primitive", {}],
                    ],
                }
            ],
        }

        with pytest.raises(LayerPlanValidationError) as exc_info:
            validate_layer_plan(plan)

        _assert_error_contains(
            exc_info.value,
            "mask_source does not exist",
            "must reference a picture primitive, got 'gemia.video.timeline.cut' (video)",
            "references unknown picture primitive 'not_a_real_primitive'",
        )

    def test_keyframes_must_stay_inside_layer_window(self) -> None:
        plan = {
            "width": 4,
            "height": 4,
            "fps": 30.0,
            "total_frames": 10,
            "layers": [
                {
                    "id": "title",
                    "type": "solid",
                    "color": [1.0, 1.0, 1.0, 1.0],
                    "start_frame": 5,
                    "end_frame": 8,
                    "keyframes": {
                        "opacity": {
                            "0": 1.0,
                            "8": {"value": 0.5, "easing": "magic"},
                        }
                    },
                }
            ],
        }

        with pytest.raises(LayerPlanValidationError) as exc_info:
            validate_layer_plan(plan)

        _assert_error_contains(
            exc_info.value,
            "occurs before the layer start_frame 5",
            "occurs at or after the layer end_frame 8",
            ".easing must be one of",
        )

    def test_preview_smoke_rejects_fully_transparent_results(self) -> None:
        plan = {
            "width": 4,
            "height": 4,
            "fps": 30.0,
            "total_frames": 3,
            "layers": [
                {
                    "id": "transparent",
                    "type": "solid",
                    "color": [1.0, 1.0, 1.0, 0.0],
                    "start_frame": 0,
                    "end_frame": 3,
                }
            ],
        }

        stack = execute_layer_plan(plan)

        with pytest.raises(LayerPlanValidationError) as exc_info:
            validate_layer_stack_preview(stack)

        _assert_error_contains(
            exc_info.value,
            "preview smoke check rendered only fully transparent frames across all samples",
        )
