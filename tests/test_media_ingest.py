from __future__ import annotations

import pytest
import subprocess
from pathlib import Path

from gemia import accounts
from gemia.media_ingest import probe_image_sequence, probe_still_metadata
from gemia.media_library import import_media


def _make_image(path: Path, color: str = "royalblue") -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=80x48:d=0.1", "-frames:v", "1", str(path)],
        capture_output=True,
        check=True,
    )
    return path


def _patch_account_roots(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "accounts"
    monkeypatch.setattr(accounts, "ACCOUNTS_ROOT", root)
    monkeypatch.setattr(accounts, "ACTIVE_ACCOUNT_PATH", root / "active.json")
    monkeypatch.setattr(accounts, "CONFIG_PATH", tmp_path / "config.json")


def test_probe_still_metadata_uses_optional_backend_fallback(tmp_path: Path) -> None:
    image = _make_image(tmp_path / "frame.png")
    metadata = probe_still_metadata(image)
    assert metadata["schema_version"] == 1
    assert metadata["width"] == 80
    assert metadata["height"] == 48
    assert metadata["channels"] >= 3
    assert metadata["fingerprint"]
    assert metadata["ingest_backend"] in {"openimageio", "pillow", "opencv", "filesystem"}
    assert "openimageio_available" in metadata


def test_probe_image_sequence_returns_stable_manifest(tmp_path: Path) -> None:
    frames = [_make_image(tmp_path / f"frame_{idx:02d}.png", color) for idx, color in enumerate(["red", "green"])]
    first = probe_image_sequence(frames)
    second = probe_image_sequence(frames)
    assert first["frame_count"] == 2
    assert first["consistent_dimensions"] is True
    assert first["sequence_fingerprint"] == second["sequence_fingerprint"]
    assert [frame["width"] for frame in first["frames"]] == [80, 80]


def test_media_library_merges_image_ingest_metadata(monkeypatch, tmp_path: Path) -> None:
    _patch_account_roots(monkeypatch, tmp_path)
    asset = import_media("google_account_one", _make_image(tmp_path / "still.jpg"))
    image_ingest = asset["metadata"].get("image_ingest")
    assert asset["media_kind"] == "image"
    assert image_ingest["width"] == 80
    assert image_ingest["height"] == 48
    assert image_ingest["fingerprint"] == asset["fingerprint"]
    assert image_ingest["ingest_backend"] in {"openimageio", "pillow", "opencv", "filesystem"}


def test_probe_image_sequence_empty_list_raises_value_error() -> None:
    with pytest.raises(ValueError, match="paths cannot be empty"):
        probe_image_sequence([])


def test_probe_image_sequence_mismatched_dimensions(tmp_path: Path) -> None:
    image1 = _make_image(tmp_path / "frame_01.png", color="red")
    # Create an image with different dimensions
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=blue:s=120x60:d=0.1", "-frames:v", "1", str(tmp_path / "frame_02.png")],
        capture_output=True,
        check=True,
    )
    image2 = tmp_path / "frame_02.png"

    frames = [image1, image2]
    manifest = probe_image_sequence(frames)

    assert manifest["frame_count"] == 2
    assert manifest["consistent_dimensions"] is False
    assert len(manifest["dimensions_summary"]) == 2
    assert "80x48" in manifest["dimensions_summary"]
    assert "120x60" in manifest["dimensions_summary"]
    assert "image sequence has mixed dimensions" in manifest["diagnostics"]
