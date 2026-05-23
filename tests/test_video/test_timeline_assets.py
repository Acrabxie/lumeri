from __future__ import annotations

import subprocess
from pathlib import Path

from gemia.video.timeline_assets import (
    extract_waveform_peaks,
    generate_timeline_thumbnails,
    media_kind_for_path,
    probe_media,
)


def _make_video(path: Path, *, audio: bool) -> Path:
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=duration=1.2:size=160x90:rate=12",
    ]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1.2"]
    cmd += [
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
    ]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    else:
        cmd += ["-an"]
    cmd.append(str(path))
    subprocess.run(cmd, capture_output=True, check=True)
    return path


def _make_image(path: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=skyblue:s=96x54:d=0.1",
            "-frames:v",
            "1",
            str(path),
        ],
        capture_output=True,
        check=True,
    )
    return path


def _make_audio(path: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1.0",
            str(path),
        ],
        capture_output=True,
        check=True,
    )
    return path


def test_probe_media_reports_video_and_audio(tmp_path: Path) -> None:
    video = _make_video(tmp_path / "with_audio.mp4", audio=True)

    meta = probe_media(str(video))

    assert meta["duration"] > 0
    assert meta["width"] == 160
    assert meta["height"] == 90
    assert meta["has_audio"] is True
    assert meta["audio_codec"]


def test_generate_timeline_thumbnails_writes_jpegs(tmp_path: Path) -> None:
    video = _make_video(tmp_path / "thumbs.mp4", audio=False)

    thumbs = generate_timeline_thumbnails(str(video), tmp_path / "cache", count=3, width=96)

    assert len(thumbs) == 3
    assert all(Path(item).exists() for item in thumbs)
    assert all(Path(item).suffix == ".jpg" for item in thumbs)


def test_extract_waveform_peaks_and_no_audio_fallback(tmp_path: Path) -> None:
    with_audio = _make_video(tmp_path / "wave.mp4", audio=True)
    silent = _make_video(tmp_path / "silent.mp4", audio=False)

    peaks = extract_waveform_peaks(str(with_audio), samples=64)
    silent_peaks = extract_waveform_peaks(str(silent), samples=64)

    assert len(peaks) == 64
    assert max(peaks) > 0
    assert len(silent_peaks) == 64
    assert silent_peaks == [0.0] * 64


def test_image_media_generates_single_thumbnail(tmp_path: Path) -> None:
    image = _make_image(tmp_path / "still.png")

    meta = probe_media(str(image))
    thumbs = generate_timeline_thumbnails(str(image), tmp_path / "image-cache", count=4, width=96)

    assert media_kind_for_path(image) == "image"
    assert meta["media_kind"] == "image"
    assert meta["width"] == 96
    assert meta["height"] == 54
    assert len(thumbs) == 1
    assert Path(thumbs[0]).exists()


def test_audio_media_waveform_without_thumbnails(tmp_path: Path) -> None:
    audio = _make_audio(tmp_path / "tone.wav")

    meta = probe_media(str(audio))
    thumbs = generate_timeline_thumbnails(str(audio), tmp_path / "audio-cache", count=4, width=96)
    peaks = extract_waveform_peaks(str(audio), samples=64)

    assert media_kind_for_path(audio) == "audio"
    assert meta["media_kind"] == "audio"
    assert meta["has_audio"] is True
    assert meta["duration"] > 0
    assert thumbs == []
    assert len(peaks) == 64
    assert max(peaks) > 0
