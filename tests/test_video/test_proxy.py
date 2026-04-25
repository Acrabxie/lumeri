from __future__ import annotations

from pathlib import Path

from gemia.video.proxy import ProxyManager


class TestProxyManager:
    def test_attach_to_plan_avoids_same_basename_proxy_collisions(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        source_a = tmp_path / "a" / "clip.mp4"
        source_b = tmp_path / "b" / "clip.mp4"
        source_a.parent.mkdir(parents=True, exist_ok=True)
        source_b.parent.mkdir(parents=True, exist_ok=True)
        source_a.write_bytes(b"a")
        source_b.write_bytes(b"b")

        def fake_proxy_generate(input_path: str, output_path: str, *, resolution: int = 720) -> str:
            del input_path, resolution
            Path(output_path).write_bytes(b"proxy")
            return output_path

        monkeypatch.setattr("gemia.video.proxy.proxy_generate", fake_proxy_generate)

        manager = ProxyManager(tmp_path / "proxies")
        preview_plan, proxy_map = manager.attach_to_plan(
            {
                "layers": [
                    {"id": "clip_a", "type": "video", "source": str(source_a)},
                    {"id": "clip_b", "type": "video", "source": str(source_b)},
                ]
            },
            resolution=540,
        )

        proxy_paths = [Path(layer["source"]) for layer in preview_plan["layers"]]

        assert proxy_paths[0] != proxy_paths[1]
        assert proxy_paths[0].name != proxy_paths[1].name
        assert proxy_map[str(source_a.resolve())] != proxy_map[str(source_b.resolve())]
