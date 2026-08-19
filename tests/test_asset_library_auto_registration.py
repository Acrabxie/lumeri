from __future__ import annotations

from pathlib import Path

from gemia import accounts
from gemia.agent_loop_v3 import AgentLoopV3
from gemia.media_library import get_asset, list_assets, soft_delete_asset


def _patch_account_roots(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "accounts"
    monkeypatch.setattr(accounts, "ACCOUNTS_ROOT", root)
    monkeypatch.setattr(accounts, "ACTIVE_ACCOUNT_PATH", root / "active.json")
    monkeypatch.setattr(accounts, "CONFIG_PATH", tmp_path / "config.json")


def test_uploads_and_tool_outputs_are_kept_in_account_library(monkeypatch, tmp_path: Path) -> None:
    _patch_account_roots(monkeypatch, tmp_path)
    account_id = "google_account_one"
    loop = AgentLoopV3(
        session_id="v3-library",
        output_dir=tmp_path / "work",
        gemini_client=object(),
        extra={"account_id": account_id},
    )

    uploaded = tmp_path / "work/uploads/upload-random.png"
    uploaded.parent.mkdir(parents=True, exist_ok=True)
    uploaded.write_bytes(b"uploaded-image")
    session_upload_id = loop.add_external_asset(
        uploaded,
        summary="user-uploaded hero.png",
        original_name="hero.png",
    )

    generated = tmp_path / "work/img_002.png"
    generated.write_bytes(b"generated-image")
    loop.registry.register_output(
        "img_002",
        kind="image",
        path=generated,
        summary="generated visual",
    )

    assets = list_assets(account_id)
    assert {asset["name"] for asset in assets} == {"hero.png", "img_002.png"}
    assert set(loop._library_asset_ids) == {session_upload_id, "img_002"}
    assert all(Path(str(asset["storage_path"])).is_file() for asset in assets)
    assert all(Path(str(asset["storage_path"])) != uploaded for asset in assets)


def test_library_asset_stays_until_manual_delete(monkeypatch, tmp_path: Path) -> None:
    _patch_account_roots(monkeypatch, tmp_path)
    account_id = "google_account_one"
    loop = AgentLoopV3(
        session_id="v3-library-delete",
        output_dir=tmp_path / "work",
        gemini_client=object(),
        extra={"account_id": account_id},
    )
    source = tmp_path / "keep.mp4"
    source.write_bytes(b"visual")

    session_asset_id = loop.add_external_asset(source, original_name="keep.mp4")
    library_asset_id = loop._library_asset_ids[session_asset_id]

    source.unlink()
    kept = get_asset(account_id, library_asset_id)
    assert kept is not None
    assert Path(str(kept["storage_path"])).is_file()

    soft_delete_asset(account_id, library_asset_id)
    assert get_asset(account_id, library_asset_id) is None
    assert list_assets(account_id) == []


def test_anonymous_session_does_not_write_into_an_account_library(monkeypatch, tmp_path: Path) -> None:
    _patch_account_roots(monkeypatch, tmp_path)
    loop = AgentLoopV3(
        session_id="v3-anonymous-library",
        output_dir=tmp_path / "work",
        gemini_client=object(),
    )
    source = tmp_path / "anonymous.png"
    source.write_bytes(b"anonymous")

    loop.add_external_asset(source)

    assert not (tmp_path / "accounts").exists()
