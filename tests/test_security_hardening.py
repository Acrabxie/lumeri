from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from gemia.audio.effects import audio_vinyl_hiss
from gemia.onboarding import mask_secret
from gemia.tools.web_search import _normalize_result_url
from gemia.video.effects import video_mirror_time


def test_mask_secret_never_echoes_a_secret_fragment() -> None:
    secret = "sk-example-private-tail"

    masked = mask_secret(secret)

    assert masked == "<configured>"
    assert all(part not in masked for part in (secret, secret[-4:]))


def test_duckduckgo_redirect_detection_observes_domain_boundary() -> None:
    target = "https%3A%2F%2Fexample.com%2Fdocs"

    assert _normalize_result_url(f"https://duckduckgo.com/l/?uddg={target}") == (
        "https://example.com/docs"
    )
    assert _normalize_result_url(f"https://evilduckduckgo.com/l/?uddg={target}") == (
        f"https://evilduckduckgo.com/l/?uddg={target}"
    )


def _tracked_mkstemp(monkeypatch, tmp_path: Path) -> list[Path]:
    created: list[Path] = []
    real_mkstemp = tempfile.mkstemp

    def tracked(*args, **kwargs):
        kwargs["dir"] = tmp_path
        fd, name = real_mkstemp(*args, **kwargs)
        created.append(Path(name))
        return fd, name

    monkeypatch.setattr(tempfile, "mkstemp", tracked)
    def fake_run(command, *args, **kwargs):
        returncode = 1 if command[0] == "ffprobe" else 0
        output = "" if kwargs.get("text") else b""
        return subprocess.CompletedProcess(command, returncode, output, output)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return created


def test_audio_effect_reserves_and_cleans_temporary_file(monkeypatch, tmp_path: Path) -> None:
    created = _tracked_mkstemp(monkeypatch, tmp_path)

    audio_vinyl_hiss("input.wav", "output.wav")

    assert len(created) == 1
    assert not created[0].exists()


def test_video_effect_reserves_and_cleans_both_temporary_files(
    monkeypatch, tmp_path: Path
) -> None:
    created = _tracked_mkstemp(monkeypatch, tmp_path)

    video_mirror_time("input.mp4", "output.mp4")

    assert len(created) == 2
    assert all(not path.exists() for path in created)
