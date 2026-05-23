from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from gemia.plan_contract import normalize_plan_for_execution
from gemia.video.masking import (
    render_chroma_key_preview,
    render_luma_key_preview,
    render_shape_mask_preview,
)


def _write_video(path: Path, frames: list[np.ndarray], *, fps: float = 12.0) -> None:
    height, width = frames[0].shape[:2]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    for frame in frames:
        writer.write(frame)
    writer.release()


def _read_middle_frame(path: Path) -> np.ndarray:
    cap = cv2.VideoCapture(str(path))
    assert cap.isOpened()
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 1)
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, total // 2))
    ok, frame = cap.read()
    cap.release()
    assert ok
    return frame


def _codec_name(path: Path) -> str:
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe is not available")
    proc = subprocess.run(
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
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def test_chroma_key_preview_replaces_green_background_with_playable_h264(tmp_path: Path) -> None:
    source = tmp_path / "green.mp4"
    frames: list[np.ndarray] = []
    for _ in range(8):
        frame = np.zeros((60, 80, 3), dtype=np.uint8)
        frame[:, :] = (0, 255, 0)
        cv2.rectangle(frame, (28, 18), (52, 42), (0, 0, 255), -1)
        frames.append(frame)
    _write_video(source, frames)

    output = tmp_path / "keyed.mp4"
    result = render_chroma_key_preview(
        str(source),
        str(output),
        key_color="green",
        tolerance=0.2,
        background_color="blue",
    )

    assert result == str(output)
    assert output.exists()
    assert _codec_name(output) == "h264"
    frame = _read_middle_frame(output)
    assert int(frame[5, 5, 0]) > 130
    assert int(frame[5, 5, 1]) < 90
    assert int(frame[30, 40, 2]) > 130
    manifest = json.loads(output.with_suffix(".masking.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "chroma"


def test_luma_key_preview_can_key_dark_background(tmp_path: Path) -> None:
    source = tmp_path / "dark.mp4"
    frames: list[np.ndarray] = []
    for _ in range(6):
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        cv2.circle(frame, (32, 24), 12, (230, 230, 230), -1)
        frames.append(frame)
    _write_video(source, frames)

    output = tmp_path / "luma.mp4"
    render_luma_key_preview(
        str(source),
        str(output),
        low=0.0,
        high=0.2,
        background_color="red",
    )

    assert output.exists()
    frame = _read_middle_frame(output)
    assert int(frame[3, 3, 2]) > 130
    assert int(frame[24, 32, 0]) > 140
    assert int(frame[24, 32, 1]) > 140


def test_shape_mask_dims_outside_and_preserves_inside(tmp_path: Path) -> None:
    source = tmp_path / "white.mp4"
    frames = [np.full((60, 80, 3), 230, dtype=np.uint8) for _ in range(6)]
    _write_video(source, frames)

    output = tmp_path / "mask.mp4"
    render_shape_mask_preview(
        str(source),
        str(output),
        shape="rectangle",
        center=(0.5, 0.5),
        size=(0.5, 0.5),
        feather=0,
        outside_color="black",
        dim_outside=1.0,
    )

    frame = _read_middle_frame(output)
    assert int(frame[30, 40].mean()) > 180
    assert int(frame[4, 4].mean()) < 50


def test_plan_contract_aliases_legacy_keying_to_masking_primitives() -> None:
    plan = {
        "steps": [
            {
                "id": "step_1",
                "function": "gemia.video.compositing.background_replace",
                "args": {"bg": "/tmp/bg.png", "similarity": 0.22, "method": "luma"},
            }
        ]
    }

    normalized = normalize_plan_for_execution(
        plan,
        active_specs=[{"name": "gemia.video.masking.render_masked_composite"}],
    )

    step = normalized["steps"][0]
    assert step["function"] == "gemia.video.masking.render_masked_composite"
    assert step["args"]["background_path"] == "/tmp/bg.png"
    assert step["args"]["tolerance"] == 0.22
    assert step["args"]["mode"] == "luma"
