from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from gemia.video.layers import Layer, LayerStack, render_layer_plan


def _solid_frame(color: tuple[float, float, float, float], size: tuple[int, int] = (8, 8)) -> np.ndarray:
    h, w = size
    return np.tile(np.array(color, dtype=np.float32), (h, w, 1))


class TestLayerRender:
    def test_render_frames_respects_step(self) -> None:
        stack = LayerStack(width=2, height=2, fps=30.0, total_frames=5)
        stack.add_layer(
            Layer(
                id="solid",
                name="solid",
                content_fn=lambda _i: _solid_frame((1.0, 0.0, 0.0, 1.0), (2, 2)),
            )
        )

        frames = stack.render_frames(step=2)

        assert len(frames) == 3
        assert all(frame.shape == (2, 2, 4) for frame in frames)

    def test_render_layer_plan_writes_preview_video(self, tmp_path: Path) -> None:
        plan = {
            "width": 64,
            "height": 64,
            "fps": 24.0,
            "total_frames": 3,
            "layers": [
                {
                    "id": "bg",
                    "type": "solid",
                    "color": [0.0, 0.0, 0.0, 1.0],
                    "start_frame": 0,
                    "end_frame": 3,
                },
                {
                    "id": "fg",
                    "type": "solid",
                    "color": [1.0, 0.0, 0.0, 0.5],
                    "start_frame": 0,
                    "end_frame": 3,
                    "z_index": 1,
                },
            ],
        }
        output_path = tmp_path / "preview.mp4"

        result = render_layer_plan(plan, output_path)

        assert result == str(output_path)
        assert output_path.exists()
        assert output_path.stat().st_size > 0

        cap = cv2.VideoCapture(str(output_path))
        try:
            assert cap.isOpened()
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            assert frame_count == 3
            ok, frame = cap.read()
            assert ok
            assert frame.shape[:2] == (64, 64)
            # mp4v introduces minor drift; just check the frame is visibly red-tinted.
            assert float(frame[..., 2].mean()) > float(frame[..., 1].mean())
            assert float(frame[..., 2].mean()) > float(frame[..., 0].mean())
        finally:
            cap.release()

    def test_render_layer_plan_cli_shape(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.json"
        output_path = tmp_path / "preview.mp4"
        plan_path.write_text(
            json.dumps(
                {
                    "width": 2,
                    "height": 2,
                    "fps": 12.0,
                    "total_frames": 2,
                    "layers": [{"id": "bg", "type": "solid", "color": [1.0, 1.0, 1.0, 1.0], "end_frame": 2}],
                }
            ),
            encoding="utf-8",
        )

        result = render_layer_plan(json.loads(plan_path.read_text(encoding="utf-8")), output_path, step=1)

        assert result == str(output_path)
        assert output_path.exists()
