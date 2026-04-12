"""Tests for gemia.video.analysis."""
from pathlib import Path
import subprocess

from gemia.video.analysis import get_metadata, detect_scenes
from gemia.video.analysis import multicam_sync


class TestGetMetadata:
    def test_basic(self, sample_video_path):
        meta = get_metadata(sample_video_path)
        assert meta["width"] == 128
        assert meta["height"] == 128
        assert 1.5 < meta["duration"] < 2.5
        assert meta["fps"] > 0
        assert meta["codec"] == "h264"


class TestDetectScenes:
    def test_basic(self, sample_video_path):
        scenes = detect_scenes(sample_video_path, threshold=30.0)
        assert isinstance(scenes, list)
        # testsrc2 has scene changes built in


class TestMulticamSync:
    @staticmethod
    def _ensure_test_src() -> Path:
        src = Path("/tmp/test_src.mp4")
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "testsrc2=duration=4:size=128x128:rate=15",
                "-f", "lavfi", "-i", "sine=frequency=880:duration=4",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest",
                str(src),
            ],
            capture_output=True,
            check=True,
        )
        return src

    @staticmethod
    def _make_delayed_clip(src: Path, out: Path, delay_sec: float) -> None:
        delay_ms = int(round(delay_sec * 1000))
        duration = get_metadata(str(src))["duration"]
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(src),
                "-af", f"adelay={delay_ms}|{delay_ms},atrim=duration={duration:.3f}",
                "-c:v", "copy",
                "-c:a", "aac",
                str(out),
            ],
            capture_output=True,
            check=True,
        )

    def test_audio_sync_aligns_durations(self, tmp_path):
        src = self._ensure_test_src()
        delays = [0.0, 0.3, 0.7]
        inputs: list[str] = []
        for idx, delay in enumerate(delays):
            clip = tmp_path / f"cam_src_{idx}.mp4"
            self._make_delayed_clip(src, clip, delay)
            inputs.append(str(clip))

        outputs = multicam_sync(inputs, str(tmp_path / "synced"))
        durations = [get_metadata(path)["duration"] for path in outputs]
        spread = max(durations) - min(durations)
        status = "PASS" if spread <= 0.5 else "FAIL"
        print(
            f"{status} multicam_sync durations: "
            + ", ".join(f"cam_{idx}={duration:.3f}s" for idx, duration in enumerate(durations))
        )
        assert spread <= 0.5
