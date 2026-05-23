from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gemia import accounts
from gemia.asset_identity import (
    asset_identity_for_record,
    build_entity_reference,
    parse_entity_reference,
    resolve_asset_identity,
)
from gemia.media_library import import_media, list_assets


def _patch_account_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "accounts"
    monkeypatch.setattr(accounts, "ACCOUNTS_ROOT", root)
    monkeypatch.setattr(accounts, "ACTIVE_ACCOUNT_PATH", root / "active.json")
    monkeypatch.setattr(accounts, "CONFIG_PATH", tmp_path / "config.json")


def test_local_entity_reference_round_trip() -> None:
    ref = build_entity_reference(
        asset_id="asset_abcdef123456abcdef123456",
        account_id="google account",
        fingerprint="abcdef1234567890ffff",
    )
    parsed = parse_entity_reference(ref)
    assert parsed.asset_id == "asset_abcdef123456abcdef123456"
    assert parsed.account_id == "google account"
    assert parsed.version_id == "abcdef1234567890"
    assert parsed.fingerprint == "abcdef1234567890ffff"


def test_asset_identity_for_record_is_openassetio_style() -> None:
    identity = asset_identity_for_record({
        "asset_id": "asset_abcdef123456abcdef123456",
        "account_id": "acct",
        "fingerprint": "abcdef1234567890ffff",
        "storage_path": "/tmp/example.mov",
        "mime_type": "video/mp4",
        "name": "Example",
    })
    assert identity["backend"] == "openassetio_optional_local"
    assert identity["entity_reference"].startswith("gemia://media/acct/")
    assert identity["traits"]["locatable_content"]["path"] == "/tmp/example.mov"
    assert identity["traits"]["versioned_asset"]["version"] == "abcdef1234567890"


def test_media_import_exposes_stable_asset_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_account_roots(monkeypatch, tmp_path)
    source = _make_video(tmp_path / "clip.mp4")
    first = import_media("account_one", source)
    second = import_media("account_one", source)
    assert first["asset_id"] == second["asset_id"]
    assert first["asset_identity"]["entity_reference"] == second["asset_identity"]["entity_reference"]
    assert first["metadata"]["asset_identity"]["fingerprint"] == first["fingerprint"]
    resolved = resolve_asset_identity(first["asset_identity"]["entity_reference"], list_assets("account_one"))
    assert resolved is not None
    assert resolved["asset_id"] == first["asset_id"]


def test_asset_identity_rejects_invalid_references() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        parse_entity_reference("file:///tmp/a.mov")
    with pytest.raises(ValueError, match="fingerprint"):
        parse_entity_reference("gemia://media/account/asset_abcdef")


def test_asset_identity_reproduces_with_two_real_videos(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    real_inputs = [Path("inputs/demo.mp4"), Path("inputs/gemia_timeline_demo.mp4")]
    if not all(path.exists() for path in real_inputs):
        pytest.skip("No two local real input videos found for OpenAssetIO identity testing")
    _patch_account_roots(monkeypatch, tmp_path)
    imported = [import_media("real_account", path) for path in real_inputs]
    refs = [asset["asset_identity"]["entity_reference"] for asset in imported]
    assert len(set(refs)) == 2
    for asset in imported:
        parsed = parse_entity_reference(asset["asset_identity"]["entity_reference"])
        assert parsed.asset_id == asset["asset_id"]
        assert parsed.fingerprint == asset["fingerprint"]
        assert resolve_asset_identity(parsed.entity_reference, imported)["asset_id"] == asset["asset_id"]


def _make_video(path: Path) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc2=duration=0.6:size=96x54:rate=12",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return path
