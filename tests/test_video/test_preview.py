from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image as PILImage

from gemia.video.layers import execute_layer_plan
from gemia.video.preview import _scale_layer_plan, render_shadow_preview


class TestShadowPreview:
    def test_scale_layer_plan_scales_image_content_with_canvas(self, tmp_path: Path) -> None:
        image_path = tmp_path / "corner.png"
        rgba = np.zeros((128, 128, 4), dtype=np.uint8)
        rgba[96:, 96:, 0] = 255
        rgba[96:, 96:, 3] = 255
        PILImage.fromarray(rgba, mode="RGBA").save(image_path)

        preview_plan = _scale_layer_plan(
            {
                "width": 128,
                "height": 128,
                "fps": 30.0,
                "total_frames": 1,
                "layers": [
                    {
                        "id": "img",
                        "type": "image",
                        "source": str(image_path),
                        "duration": 1,
                    }
                ],
            },
            max_long_edge=64,
        )

        stack = execute_layer_plan(preview_plan)
        frame = stack.render_frame(0)

        assert frame.shape == (64, 64, 4)
        assert frame[63, 63, 0] > 0.0
        assert frame[63, 63, 3] > 0.0
        assert np.isclose(frame[0, 0, 3], 0.0, atol=1e-6)

    def test_render_shadow_preview_writes_manifest_and_proxy_map(
        self,
        sample_video_path: str,
        tmp_path: Path,
    ) -> None:
        output_path = tmp_path / "shadow.mp4"
        proxy_root = tmp_path / "proxies"
        plan = {
            "width": 128,
            "height": 128,
            "fps": 15.0,
            "total_frames": 8,
            "layers": [
                {
                    "id": "clip",
                    "type": "video",
                    "source": sample_video_path,
                    "start_frame": 0,
                    "end_frame": 8,
                }
            ],
        }

        result = render_shadow_preview(
            plan,
            output_path,
            frame_step=2,
            max_long_edge=64,
            proxy_resolution=64,
            proxy_root=proxy_root,
        )

        assert output_path.exists()
        assert Path(result.manifest_path).exists()
        assert proxy_root.exists()
        assert result.proxy_map

        manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
        assert manifest["compiled_graph"]["backend"] == "neutral"
        assert manifest["execution_graph"]["backend"] == "software"
        assert manifest["render_backend"]["selected"] == "software"
        assert manifest["render_backend"]["source_kind"] == "compositing_graph"
        assert result.render_backend == manifest["render_backend"]
        assert manifest["frame_step"] == 2
        assert manifest["max_long_edge"] == 64
        assert list(manifest["proxy_map"].values())[0].endswith(".mp4")

    def test_render_shadow_preview_infers_manifest_metrics_and_timing(
        self,
        sample_video_path: str,
        tmp_path: Path,
    ) -> None:
        output_path = tmp_path / "shadow-inferred.mp4"
        proxy_root = tmp_path / "proxies"
        plan = {
            "layers": [
                {
                    "id": "clip",
                    "type": "video",
                    "source": sample_video_path,
                    "start_frame": 3,
                },
                {
                    "id": "title",
                    "type": "text",
                    "text": "Hello",
                    "position": [12, 12],
                    "start_frame": 5,
                    "duration": 4,
                    "z_index": 1,
                },
            ],
        }

        result = render_shadow_preview(
            plan,
            output_path,
            frame_step=2,
            max_long_edge=64,
            proxy_resolution=64,
            proxy_root=proxy_root,
        )

        manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
        compiled = manifest["compiled_graph"]
        title_source = next(
            step for step in compiled["steps"]
            if step["layer_id"] == "title" and step["kind"] == "source"
        )

        assert compiled["metadata"]["width"] == 64
        assert compiled["metadata"]["height"] == 64
        assert compiled["metadata"]["fps"] == 15.0
        assert compiled["metadata"]["total_frames"] == 33
        assert compiled["metadata"]["authored_metric_sources"] == {
            "width": "inferred",
            "height": "inferred",
            "fps": "inferred",
            "total_frames": "inferred",
        }
        assert title_source["params"]["start_frame"] == 5
        assert title_source["params"]["end_frame"] == 9
        assert title_source["params"]["duration"] == 4
