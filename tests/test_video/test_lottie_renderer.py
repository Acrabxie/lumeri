import json
import builtins
import numpy as np
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from gemia.video.lottie_renderer import (
    DeterministicLottieRenderer,
    RlottieRenderer,
    select_lottie_renderer,
)
import gemia.video.lottie_renderer as lottie_module
from gemia.video.html_graphics import lottie_renderer_metadata, render_lottie_frame


@pytest.fixture
def dummy_lottie(tmp_path):
    lottie_data = {
        "v": "5.5.7",
        "fr": 30,
        "ip": 0,
        "op": 60,
        "w": 100,
        "h": 100,
        "nm": "Test",
        "layers": [
            {
                "ty": 4,
                "nm": "Shape Layer 1",
                "ks": {
                    "o": {"k": 100},
                    "p": {"k": [50, 50, 0]},
                    "s": {"k": [100, 100, 100]},
                },
                "shapes": [
                    {
                        "ty": "rc",
                        "s": {"k": [50, 50]},
                        "p": {"k": [0, 0]},
                        "r": {"k": 0},
                    },
                    {
                        "ty": "fl",
                        "c": {"k": [1, 0, 0, 1]},
                        "o": {"k": 100},
                    }
                ]
            }
        ]
    }
    lottie_path = tmp_path / "test.json"
    lottie_path.write_text(json.dumps(lottie_data))
    return str(lottie_path)


def test_deterministic_renderer(dummy_lottie):
    renderer = DeterministicLottieRenderer()
    assert renderer.name == "deterministic_pil"
    
    meta = renderer.get_metadata(dummy_lottie)
    assert meta["width"] == 100
    assert meta["height"] == 100
    assert meta["fps"] == 30.0
    assert meta["frames"] == 60

    frame = renderer.render_frame(dummy_lottie, width=200, height=200, frame_index=0)
    assert frame.shape == (200, 200, 4)
    assert frame.dtype == np.float32
    assert np.any(frame[..., 3] > 0)  # Should have some content
    assert frame[100, 100, 0] > 0.9
    assert frame[100, 100, 1] < 0.1
    assert frame[100, 100, 2] < 0.1


def test_rlottie_renderer_fallback(dummy_lottie, monkeypatch):
    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name in {"rlottie", "rlottie_python"}:
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    renderer = RlottieRenderer(rlottie_module=None)

    assert renderer.is_available() is False
    assert select_lottie_renderer().name == "deterministic_pil"
    with pytest.raises(RuntimeError, match="rlottie is not available"):
        renderer.render_frame(dummy_lottie, width=100, height=100, frame_index=0)


def test_rlottie_renderer_mock(dummy_lottie):
    # Mock rlottie module
    mock_rlottie = MagicMock()
    mock_anim = MagicMock(width=100, height=100, fps=30.0, total_frames=60)
    
    # Mock render_frame to return a uint8 RGBA array
    mock_frame = np.zeros((100, 100, 4), dtype=np.uint8)
    mock_frame[..., 3] = 255
    mock_anim.render_frame.return_value = mock_frame
    
    mock_rlottie.LottieAnimation.return_value = mock_anim
    
    renderer = RlottieRenderer(rlottie_module=mock_rlottie)
    assert renderer.name == "rlottie"
    assert renderer.is_available() is True
    
    meta = renderer.get_metadata(dummy_lottie)
    assert meta["width"] == 100
    assert meta["frames"] == 60
    
    frame = renderer.render_frame(dummy_lottie, width=100, height=100, frame_index=0)
    assert frame.shape == (100, 100, 4)
    assert frame.dtype == np.float32
    assert np.allclose(frame[..., 3], 1.0)


@pytest.mark.parametrize(
    "bad_frame",
    [
        np.zeros((10, 10, 3), dtype=np.uint8),
        np.ones((100, 100, 4), dtype=np.float32) * 999.0,
    ],
)
def test_rlottie_renderer_rejects_invalid_frames(dummy_lottie, bad_frame):
    mock_rlottie = MagicMock()
    mock_anim = MagicMock(width=100, height=100, fps=30.0, total_frames=60)
    mock_anim.render_frame.return_value = bad_frame
    mock_rlottie.LottieAnimation.return_value = mock_anim
    renderer = RlottieRenderer(rlottie_module=mock_rlottie)
    with pytest.raises(RuntimeError, match="rlottie render failed"):
        renderer.render_frame(dummy_lottie, width=100, height=100, frame_index=0)


def test_selected_rlottie_runtime_failure_falls_back(dummy_lottie, monkeypatch):
    class BrokenRlottie:
        name = "rlottie"

        def is_available(self):
            return True

        def render_frame(self, source, *, width, height, frame_index):
            raise RuntimeError("boom")

        def get_metadata(self, source):
            raise RuntimeError("boom")

    monkeypatch.setattr(lottie_module, "RlottieRenderer", BrokenRlottie)
    renderer = select_lottie_renderer()
    assert "fallback" in renderer.name
    meta = renderer.get_metadata(dummy_lottie)
    assert meta["width"] == 100
    frame = renderer.render_frame(dummy_lottie, width=100, height=100, frame_index=0)
    assert frame.shape == (100, 100, 4)


def test_select_renderer():
    renderer = select_lottie_renderer()
    # Should be deterministic_pil unless rlottie is installed
    assert renderer.name in ["deterministic_pil", "rlottie_with_deterministic_pil_fallback"]


def test_html_graphics_metadata_integration(dummy_lottie):
    metadata = lottie_renderer_metadata(dummy_lottie)
    assert "renderer" in metadata
    assert metadata["source"] == dummy_lottie


def test_render_lottie_frame_wrapper(dummy_lottie):
    frame = render_lottie_frame(dummy_lottie, width=100, height=100, frame_index=0)
    assert frame.shape == (100, 100, 4)
    assert frame.dtype == np.float32
