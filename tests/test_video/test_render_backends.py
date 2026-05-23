from __future__ import annotations

import cv2
import numpy as np
import pytest
from PIL import Image as PILImage

from gemia.video.backends import (
    GRAPH_NATIVE_SOFTWARE_STRATEGY,
    RenderProfile,
    SoftwareRenderBackend,
    choose_render_backend,
)
from gemia.video.compositing_graph import build_compositing_graph_from_layer_plan
from gemia.video.compositing_graph import compile_compositing_graph


def _open_video(path: str) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(path)
    assert cap.isOpened()
    return cap


class TestSoftwareRenderBackend:
    def test_choose_render_backend_selects_graph_native_software_target(self) -> None:
        plan = {
            "width": 6,
            "height": 6,
            "fps": 12.0,
            "total_frames": 3,
            "layers": [
                {
                    "id": "bg",
                    "type": "solid",
                    "color": [0.1, 0.2, 0.3, 1.0],
                    "duration": 3,
                }
            ],
        }
        graph = build_compositing_graph_from_layer_plan(plan)

        backend, decision = choose_render_backend(graph)

        assert backend.name == "software"
        assert decision.requested == "auto"
        assert decision.selected == "software"
        assert decision.source_kind == "compositing_graph"
        assert decision.strategy == GRAPH_NATIVE_SOFTWARE_STRATEGY

    def test_choose_render_backend_rejects_unwired_targets(self) -> None:
        plan = {
            "width": 4,
            "height": 4,
            "layers": [
                {
                    "id": "bg",
                    "type": "solid",
                    "color": [0.0, 0.0, 0.0, 1.0],
                    "duration": 1,
                }
            ],
        }

        with pytest.raises(ValueError, match="Unsupported render backend"):
            choose_render_backend(plan, requested="mlt")

    def test_render_preview_from_layer_plan_uses_preview_profile(
        self,
        tmp_path,
    ) -> None:
        image_path = tmp_path / "still.png"
        PILImage.new("RGBA", (8, 6), (255, 0, 0, 255)).save(image_path)
        plan = {
            "width": 8,
            "height": 6,
            "fps": 12.0,
            "total_frames": 4,
            "layers": [
                {
                    "id": "still",
                    "type": "image",
                    "source": str(image_path),
                    "start_frame": 0,
                    "duration": 4,
                }
            ],
        }
        backend = SoftwareRenderBackend()
        output_path = tmp_path / "preview.mp4"

        result = backend.render_preview(plan, output_path)

        assert result.backend == "software"
        assert result.source_kind == "layer_plan"
        assert result.profile == RenderProfile.preview()
        assert result.output_path == str(output_path)
        assert result.total_frames == 2
        assert output_path.exists()

        cap = _open_video(str(output_path))
        try:
            assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == 2
            ok, frame = cap.read()
            assert ok
            assert frame.shape[:2] == (6, 8)
            assert float(frame[..., 2].mean()) > float(frame[..., 1].mean())
            assert float(frame[..., 2].mean()) > float(frame[..., 0].mean())
        finally:
            cap.release()

    def test_render_graph_reconstructs_layer_plan_and_preserves_timing(
        self,
        tmp_path,
    ) -> None:
        plan = {
            "width": 10,
            "height": 10,
            "fps": 20.0,
            "layers": [
                {
                    "id": "bg",
                    "type": "solid",
                    "color": [0.0, 0.0, 0.0, 1.0],
                    "start_frame": 0,
                    "duration": 5,
                },
                {
                    "id": "fg",
                    "type": "solid",
                    "color": [0.0, 1.0, 0.0, 1.0],
                    "start_frame": 3,
                    "duration": 4,
                    "z_index": 1,
                    "opacity": 0.75,
                },
            ],
        }
        graph = build_compositing_graph_from_layer_plan(plan)
        backend = SoftwareRenderBackend()
        output_path = tmp_path / "final.mp4"

        result = backend.render_final(graph, output_path)

        assert result.backend == "software"
        assert result.source_kind == "compositing_graph"
        assert result.profile == RenderProfile.final()
        assert result.compiled_plan is not None
        assert result.compiled_plan.backend == "software"
        assert result.total_frames == 7
        assert output_path.exists()

        cap = _open_video(str(output_path))
        try:
            assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == 7
            ok, first_frame = cap.read()
            assert ok
            assert first_frame.shape[:2] == (10, 10)
            assert float(first_frame[..., 1].mean()) < 5.0

            cap.set(cv2.CAP_PROP_POS_FRAMES, 4)
            ok, late_frame = cap.read()
            assert ok
            assert float(late_frame[..., 1].mean()) > float(first_frame[..., 1].mean())
        finally:
            cap.release()

    def test_render_compiled_plan_accepts_neutral_backend_output(self, tmp_path) -> None:
        image_path = tmp_path / "still.png"
        image = np.zeros((4, 4, 4), dtype=np.uint8)
        image[..., 1] = 255
        image[..., 3] = 255
        PILImage.fromarray(image, mode="RGBA").save(image_path)
        plan = {
            "width": 4,
            "height": 4,
            "fps": 8.0,
            "total_frames": 2,
            "layers": [
                {
                    "id": "still",
                    "type": "image",
                    "source": str(image_path),
                    "duration": 2,
                }
            ],
        }
        backend = SoftwareRenderBackend()
        neutral_compiled = compile_compositing_graph(
            build_compositing_graph_from_layer_plan(plan)
        )
        output_path = tmp_path / "neutral.mp4"

        result = backend.render_compiled_plan(
            neutral_compiled,
            output_path,
            profile=RenderProfile.final(),
        )

        assert result.source_kind == "compiled_compositing_plan"
        assert result.compiled_plan is neutral_compiled
        assert result.total_frames == 2
        assert output_path.exists()

    def test_render_graph_preserves_image_blur_radius_into_pixels(self, tmp_path) -> None:
        image_path = tmp_path / "dot.png"
        image = np.zeros((64, 64, 4), dtype=np.uint8)
        image[24:40, 24:40] = [255, 255, 255, 255]
        PILImage.fromarray(image, mode="RGBA").save(image_path)

        def make_plan(*, blur_radius: float | None = None) -> dict:
            layer = {
                "id": "contact_shadow_0",
                "type": "image",
                "source": str(image_path),
                "duration": 1,
                "size": [64, 64],
            }
            if blur_radius is not None:
                layer["blur_radius"] = blur_radius
                layer["metadata"] = {"contact_shadow_blur_radius": blur_radius}
            return {
                "width": 64,
                "height": 64,
                "fps": 30.0,
                "total_frames": 1,
                "layers": [layer],
            }

        backend = SoftwareRenderBackend()
        blur_graph = build_compositing_graph_from_layer_plan(make_plan(blur_radius=6.0))
        blur_compiled = backend.compile_graph(blur_graph)
        blur_source = next(
            step for step in blur_compiled.steps
            if step.kind == "source" and step.layer_id == "contact_shadow_0"
        )
        assert blur_source.params["blur_radius"] == 6.0
        assert blur_source.params["metadata"]["contact_shadow_blur_radius"] == 6.0

        no_blur_path = tmp_path / "no-blur.mp4"
        blur_path = tmp_path / "blur.mp4"
        backend.render_final(
            build_compositing_graph_from_layer_plan(make_plan()),
            no_blur_path,
        )
        backend.render_final(blur_graph, blur_path)

        no_blur_cap = _open_video(str(no_blur_path))
        blur_cap = _open_video(str(blur_path))
        try:
            ok, no_blur_frame = no_blur_cap.read()
            assert ok
            ok, blur_frame = blur_cap.read()
            assert ok
        finally:
            no_blur_cap.release()
            blur_cap.release()

        frame_delta = np.abs(
            no_blur_frame.astype(np.int16) - blur_frame.astype(np.int16)
        )
        assert float(frame_delta.mean()) > 1.0
        assert float(blur_frame[20:24, 24:40].mean()) > float(no_blur_frame[20:24, 24:40].mean())
