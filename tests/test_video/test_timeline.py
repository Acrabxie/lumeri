"""Tests for gemia.video.timeline."""
from pathlib import Path
import subprocess

import pytest

from gemia.video.timeline import cut, concat, nest_clips, speed, reverse, roll_edit
from gemia.video.analysis import get_metadata


class TestCut:
    def test_basic(self, sample_video_path, tmp_path):
        out = str(tmp_path / "cut.mp4")
        cut(sample_video_path, out, start_sec=0.0, end_sec=1.0)
        meta = get_metadata(out)
        assert 0.5 < meta["duration"] < 1.5


class TestConcat:
    def test_two_clips(self, sample_video_path, tmp_path):
        out = str(tmp_path / "concat.mp4")
        concat([sample_video_path, sample_video_path], out)
        meta = get_metadata(out)
        orig = get_metadata(sample_video_path)
        assert meta["duration"] > orig["duration"] * 1.5

    def test_empty(self, tmp_path):
        with pytest.raises(ValueError):
            concat([], str(tmp_path / "empty.mp4"))


class TestNestClips:
    @staticmethod
    def _ensure_test_src() -> Path:
        src = Path("/tmp/test_src.mp4")
        if not src.exists():
            subprocess.run(
                [
                    "ffmpeg", "-y", "-f", "lavfi",
                    "-i", "testsrc2=duration=4:size=128x128:rate=15",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(src),
                ],
                capture_output=True,
                check=True,
            )
        return src

    def test_without_crossfade(self, tmp_path):
        src = self._ensure_test_src()
        clips = [
            str(tmp_path / "nest_a.mp4"),
            str(tmp_path / "nest_b.mp4"),
            str(tmp_path / "nest_c.mp4"),
        ]
        ranges = [(0.0, 1.0), (1.0, 2.5), (2.5, 4.0)]
        for clip, (start, end) in zip(clips, ranges):
            cut(str(src), clip, start_sec=start, end_sec=end)

        out = str(tmp_path / "nested_plain.mp4")
        nest_clips(clips, out, crossfade_sec=0.0)

        durations = [get_metadata(clip)["duration"] for clip in clips]
        expected = sum(durations)
        actual = get_metadata(out)["duration"]
        status = "PASS" if abs(actual - expected) < 0.15 else "FAIL"
        print(
            f"{status} nest_clips plain: expected={expected:.3f}s actual={actual:.3f}s"
        )
        assert abs(actual - expected) < 0.15

    def test_with_crossfade(self, tmp_path):
        src = self._ensure_test_src()
        clips = [
            str(tmp_path / "xfade_a.mp4"),
            str(tmp_path / "xfade_b.mp4"),
            str(tmp_path / "xfade_c.mp4"),
        ]
        ranges = [(0.0, 1.2), (1.2, 2.7), (2.7, 4.0)]
        for clip, (start, end) in zip(clips, ranges):
            cut(str(src), clip, start_sec=start, end_sec=end)

        crossfade = 0.3
        out = str(tmp_path / "nested_xfade.mp4")
        nest_clips(clips, out, crossfade_sec=crossfade)

        durations = [get_metadata(clip)["duration"] for clip in clips]
        expected = sum(durations) - crossfade * (len(clips) - 1)
        actual = get_metadata(out)["duration"]
        status = "PASS" if abs(actual - expected) < 0.2 else "FAIL"
        print(
            f"{status} nest_clips xfade: expected={expected:.3f}s actual={actual:.3f}s"
        )
        assert abs(actual - expected) < 0.2


class TestSpeed:
    def test_faster(self, sample_video_path, tmp_path):
        out = str(tmp_path / "fast.mp4")
        speed(sample_video_path, out, factor=2.0)
        meta = get_metadata(out)
        orig = get_metadata(sample_video_path)
        assert meta["duration"] < orig["duration"]

    def test_invalid(self, sample_video_path, tmp_path):
        with pytest.raises(ValueError):
            speed(sample_video_path, str(tmp_path / "bad.mp4"), factor=0)


class TestReverse:
    def test_basic(self, sample_video_path, tmp_path):
        out = str(tmp_path / "reversed.mp4")
        reverse(sample_video_path, out)
        meta = get_metadata(out)
        assert meta["duration"] > 0


class TestRollEdit:
    def test_preserves_total_duration_positive(self, tmp_path):
        src = Path("/tmp/test_src.mp4")
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "testsrc2=duration=4:size=128x128:rate=15",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                str(src),
            ],
            capture_output=True,
            check=True,
        )

        clip_a = str(tmp_path / "clip_a.mp4")
        clip_b = str(tmp_path / "clip_b.mp4")
        cut(str(src), clip_a, start_sec=0.0, end_sec=2.0)
        cut(str(src), clip_b, start_sec=2.0, end_sec=4.0)

        out_a = str(tmp_path / "roll_a.mp4")
        out_b = str(tmp_path / "roll_b.mp4")
        roll_edit(clip_a, clip_b, out_a, out_b, delta_sec=0.5)

        src_total = get_metadata(clip_a)["duration"] + get_metadata(clip_b)["duration"]
        out_total = get_metadata(out_a)["duration"] + get_metadata(out_b)["duration"]
        status = "PASS" if abs(src_total - out_total) < 0.15 else "FAIL"
        print(
            f"{status} positive roll: "
            f"before={src_total:.3f}s after={out_total:.3f}s "
            f"a={get_metadata(out_a)['duration']:.3f}s "
            f"b={get_metadata(out_b)['duration']:.3f}s"
        )
        assert abs(src_total - out_total) < 0.15

    def test_preserves_total_duration_negative(self, tmp_path):
        src = Path("/tmp/test_src.mp4")
        if not src.exists():
            subprocess.run(
                [
                    "ffmpeg", "-y", "-f", "lavfi",
                    "-i", "testsrc2=duration=4:size=128x128:rate=15",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(src),
                ],
                capture_output=True,
                check=True,
            )

        clip_a = str(tmp_path / "clip_a_neg.mp4")
        clip_b = str(tmp_path / "clip_b_neg.mp4")
        cut(str(src), clip_a, start_sec=0.0, end_sec=2.0)
        cut(str(src), clip_b, start_sec=2.0, end_sec=4.0)

        out_a = str(tmp_path / "roll_a_neg.mp4")
        out_b = str(tmp_path / "roll_b_neg.mp4")
        roll_edit(clip_a, clip_b, out_a, out_b, delta_sec=-0.5)

        src_total = get_metadata(clip_a)["duration"] + get_metadata(clip_b)["duration"]
        out_total = get_metadata(out_a)["duration"] + get_metadata(out_b)["duration"]
        status = "PASS" if abs(src_total - out_total) < 0.15 else "FAIL"
        print(
            f"{status} negative roll: "
            f"before={src_total:.3f}s after={out_total:.3f}s "
            f"a={get_metadata(out_a)['duration']:.3f}s "
            f"b={get_metadata(out_b)['duration']:.3f}s"
        )
        assert abs(src_total - out_total) < 0.15
