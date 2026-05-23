"""Tests for gemia.video.generative — mocked API calls."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from unittest.mock import patch, MagicMock


def _video_codec(path: Path) -> str:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


class TestGenerateVideo:
    def test_returns_path(self):
        with patch("gemia.video.generative.VeoClient") as MockClient:
            MockClient.return_value.generate.return_value = "/tmp/fake.mp4"
            from gemia.video.generative import generate_video
            result = generate_video("cityscape at night")
        assert isinstance(result, str)
        assert result.endswith(".mp4")

    def test_params_passed(self):
        with patch("gemia.video.generative.VeoClient") as MockClient:
            instance = MockClient.return_value
            instance.generate.return_value = "/tmp/fake.mp4"
            from gemia.video.generative import generate_video
            generate_video("test", duration=10.0, aspect_ratio="9:16")
            instance.generate.assert_called_once_with("test", duration=10.0, aspect_ratio="9:16")

    def test_default_params(self):
        with patch("gemia.video.generative.VeoClient") as MockClient:
            instance = MockClient.return_value
            instance.generate.return_value = "/tmp/default.mp4"
            from gemia.video.generative import generate_video
            generate_video("ocean waves")
            instance.generate.assert_called_once_with("ocean waves", duration=5.0, aspect_ratio="16:9")

    def test_client_instantiated(self):
        with patch("gemia.video.generative.VeoClient") as MockClient:
            MockClient.return_value.generate.return_value = "/tmp/fake.mp4"
            from gemia.video.generative import generate_video
            generate_video("test prompt")
        MockClient.assert_called_once()

    def test_provider_failure_renders_local_fallback(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GEMIA_VEO_FALLBACK_DIR", str(tmp_path))
        with patch("gemia.video.generative.VeoClient") as MockClient:
            MockClient.return_value.generate.side_effect = RuntimeError(
                "Veo job submission failed after 3 attempts: Veo API request failed: <urlopen error [SSL: UNEXPECTED_EOF_WHILE_READING]>"
            )
            from gemia.video.generative import generate_video

            result = generate_video("一段冰蓝色 Lumeri 广告小样", duration=0.4, aspect_ratio="1:1")

        output = Path(result)
        payload = json.loads(output.with_suffix(".veo-fallback.json").read_text(encoding="utf-8"))
        assert output.exists()
        assert payload["status"] == "fallback"
        assert payload["kind"] == "veo_fallback_preview"
        assert "本地渲染" in payload["user_message"]
        if shutil.which("ffprobe"):
            assert _video_codec(output) == "h264"


class TestGenerateVideoFromImage:
    def test_returns_path(self, tmp_path):
        import cv2
        import numpy as np
        img_path = str(tmp_path / "frame.png")
        cv2.imwrite(img_path, np.zeros((64, 64, 3), dtype=np.uint8))

        with patch("gemia.video.generative.VeoClient") as MockClient:
            MockClient.return_value.generate_from_image.return_value = "/tmp/fake.mp4"
            from gemia.video.generative import generate_video_from_image
            result = generate_video_from_image(img_path, prompt="pan left")
        assert isinstance(result, str)

    def test_args_passed(self, tmp_path):
        import cv2
        import numpy as np
        img_path = str(tmp_path / "frame.png")
        cv2.imwrite(img_path, np.zeros((64, 64, 3), dtype=np.uint8))

        with patch("gemia.video.generative.VeoClient") as MockClient:
            instance = MockClient.return_value
            instance.generate_from_image.return_value = "/tmp/fake.mp4"
            from gemia.video.generative import generate_video_from_image
            generate_video_from_image(img_path, prompt="zoom out slowly", duration=8.0)
            instance.generate_from_image.assert_called_once_with(
                img_path, "zoom out slowly", duration=8.0
            )

    def test_default_duration(self, tmp_path):
        import cv2
        import numpy as np
        img_path = str(tmp_path / "frame2.png")
        cv2.imwrite(img_path, np.zeros((64, 64, 3), dtype=np.uint8))

        with patch("gemia.video.generative.VeoClient") as MockClient:
            instance = MockClient.return_value
            instance.generate_from_image.return_value = "/tmp/fake.mp4"
            from gemia.video.generative import generate_video_from_image
            generate_video_from_image(img_path, prompt="tilt up")
            _, call_kwargs = instance.generate_from_image.call_args
            assert call_kwargs.get("duration") == 5.0

    def test_provider_failure_turns_image_into_local_fallback(self, monkeypatch, tmp_path):
        import cv2
        import numpy as np

        monkeypatch.setenv("GEMIA_VEO_FALLBACK_DIR", str(tmp_path / "veo"))
        img_path = str(tmp_path / "frame3.png")
        cv2.imwrite(img_path, np.full((64, 64, 3), 120, dtype=np.uint8))

        with patch("gemia.video.generative.VeoClient") as MockClient:
            MockClient.return_value.generate_from_image.side_effect = RuntimeError("Veo API request failed: TLS reset")
            from gemia.video.generative import generate_video_from_image

            result = generate_video_from_image(img_path, prompt="slow zoom", duration=0.4)

        output = Path(result)
        payload = json.loads(output.with_suffix(".veo-fallback.json").read_text(encoding="utf-8"))
        assert output.exists()
        assert payload["fallback_mode"] == "image_to_video"
        assert payload["input_path"].endswith("frame3.png")


class TestExtendVideo:
    def test_returns_path(self):
        with patch("gemia.video.generative.VeoClient") as MockClient:
            MockClient.return_value.extend.return_value = "/tmp/fake_ext.mp4"
            from gemia.video.generative import extend_video
            result = extend_video("/fake/input.mp4", prompt="fade to black")
        assert isinstance(result, str)

    def test_args_passed(self):
        with patch("gemia.video.generative.VeoClient") as MockClient:
            instance = MockClient.return_value
            instance.extend.return_value = "/tmp/fake_ext.mp4"
            from gemia.video.generative import extend_video
            extend_video("/fake/input.mp4", prompt="camera pulls back", duration=5.0)
            instance.extend.assert_called_once_with(
                "/fake/input.mp4", "camera pulls back", duration=5.0
            )

    def test_default_duration(self):
        with patch("gemia.video.generative.VeoClient") as MockClient:
            instance = MockClient.return_value
            instance.extend.return_value = "/tmp/fake_ext.mp4"
            from gemia.video.generative import extend_video
            extend_video("/fake/input.mp4", prompt="slow fade out")
            _, call_kwargs = instance.extend.call_args
            assert call_kwargs.get("duration") == 3.0

    def test_provider_failure_extends_locally(self, monkeypatch, tmp_path):
        src = tmp_path / "source.mp4"
        src.write_bytes(b"fake video bytes")
        monkeypatch.setenv("GEMIA_VEO_FALLBACK_DIR", str(tmp_path / "veo"))

        def fake_extend(input_path, output_path, *, duration):
            Path(output_path).write_bytes(b"fallback video")
            return output_path

        monkeypatch.setattr("gemia.video.generative.generative_extend", fake_extend)
        with patch("gemia.video.generative.VeoClient") as MockClient:
            MockClient.return_value.extend.side_effect = RuntimeError("Veo API request failed: TLS reset")
            from gemia.video.generative import extend_video

            result = extend_video(str(src), prompt="continue", duration=0.4)

        output = Path(result)
        payload = json.loads(output.with_suffix(".veo-fallback.json").read_text(encoding="utf-8"))
        assert output.exists()
        assert payload["fallback_mode"] == "local_extend_or_passthrough"
        assert payload["provider"] == "openrouter/veo"


def test_generate_broll_uses_configured_pexels_key(monkeypatch, tmp_path):
    from gemia.video import stock_media
    from gemia.video.generative import generate_broll
    import urllib.request
    import subprocess

    monkeypatch.setattr(stock_media, "_api_key", lambda provider: f"{provider}-configured-key")
    seen_headers: list[str] = []

    class FakeResponse:
        def __init__(self, payload: bytes):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self.payload

    def fake_urlopen(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "videos/search" in url:
            seen_headers.append(req.headers.get("Authorization", ""))
            return FakeResponse(
                json.dumps(
                    {
                        "videos": [
                            {
                                "video_files": [
                                    {"width": 640, "link": "https://cdn.example.test/broll.mp4"}
                                ]
                            }
                        ]
                    }
                ).encode("utf-8")
            )
        return FakeResponse(b"fake mp4")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)

    outputs = generate_broll("city night skyline", str(tmp_path), style="cinematic")

    assert seen_headers == ["pexels-configured-key"] * 3
    assert outputs == [
        str(tmp_path / "broll_city.mp4"),
        str(tmp_path / "broll_night.mp4"),
        str(tmp_path / "broll_skyline.mp4"),
    ]
    assert (tmp_path / "broll_city_raw.mp4").read_bytes() == b"fake mp4"
