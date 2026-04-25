from __future__ import annotations

from gemia.video.compositing_graph import (
    build_compositing_graph_from_layer_plan,
    build_compositing_graph_from_layer_stack,
    compile_compositing_graph,
)
from gemia.video.layers import Layer, LayerStack, execute_layer_plan


def _solid_rgba_frame() -> list[list[list[float]]]:
    return [[[1.0, 0.0, 0.0, 1.0]]]


class TestCompositingGraph:
    def test_build_graph_from_layer_plan_creates_ordered_dag(self) -> None:
        plan = {
            "width": 64,
            "height": 64,
            "fps": 24.0,
            "total_frames": 12,
            "layers": [
                {
                    "id": "bg",
                    "type": "solid",
                    "color": [0.0, 0.0, 0.0, 1.0],
                    "start_frame": 0,
                    "end_frame": 12,
                },
                {
                    "id": "title",
                    "type": "text",
                    "text": "Hello",
                    "position": [12, 8],
                    "start_frame": 0,
                    "end_frame": 12,
                    "z_index": 1,
                    "keyframes": {"opacity": {"0": 0.0, "6": 1.0}},
                },
            ],
        }

        graph = build_compositing_graph_from_layer_plan(plan)
        compiled = compile_compositing_graph(graph)

        assert graph.outputs["frame"].node_id.endswith("_composite")
        assert any(node.kind == "automation" for node in graph.nodes.values())
        assert any(node.kind == "transform" for node in graph.nodes.values())
        assert compiled.backend == "neutral"
        assert compiled.steps[-1].id == graph.outputs["frame"].node_id
        assert compiled.metadata["metric_sources"] == {
            "width": "explicit",
            "height": "explicit",
            "fps": "explicit",
            "total_frames": "explicit",
        }
        assert compiled.metadata["explicit_metrics"] == [
            "width",
            "height",
            "fps",
            "total_frames",
        ]

    def test_build_graph_from_layer_stack_preserves_layer_ids(self) -> None:
        stack = LayerStack(width=4, height=4, fps=30.0, total_frames=1)
        stack.add_layer(
            Layer(
                id="base",
                name="base",
                content_fn=lambda _i: _solid_rgba_frame(),
            )
        )

        graph = build_compositing_graph_from_layer_stack(stack)

        assert graph.metadata["layer_order"] == ["base"]
        assert any(node.layer_id == "base" for node in graph.nodes.values())

    def test_build_graph_from_layer_plan_matches_inferred_stack_metrics(
        self,
        sample_video_path: str,
    ) -> None:
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
                    "position": [8, 8],
                    "start_frame": 5,
                    "duration": 4,
                    "z_index": 1,
                },
            ],
        }

        stack = execute_layer_plan(plan)
        graph = build_compositing_graph_from_layer_plan(plan)
        compiled = compile_compositing_graph(graph)
        title_source = next(
            step for step in compiled.steps
            if step.layer_id == "title" and step.kind == "source"
        )

        assert graph.metadata["width"] == stack.width
        assert graph.metadata["height"] == stack.height
        assert graph.metadata["fps"] == stack.fps
        assert graph.metadata["total_frames"] == stack.total_frames
        assert compiled.step("track_main").params["total_frames"] == stack.total_frames
        assert title_source.params["start_frame"] == 5
        assert title_source.params["end_frame"] == 9
        assert title_source.params["duration"] == 4
        assert compiled.metadata["metric_sources"] == {
            "width": "inferred",
            "height": "inferred",
            "fps": "inferred",
            "total_frames": "inferred",
        }
        assert compiled.metadata["inferred_metrics"] == [
            "width",
            "height",
            "fps",
            "total_frames",
        ]
