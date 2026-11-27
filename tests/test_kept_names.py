"""The names the rename kept move in two releases; this one reads both spellings and writes the old.

See ``lumeri/legacy_names.py``.
"""
from __future__ import annotations

import importlib
import runpy
import sys
from pathlib import Path

import pytest

from lumeri import home, legacy_names

OLD = "gem" + "ia"
OLD_SCRIPT = "lumer" + "ai"
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def user_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def test_this_release_writes_the_old_spellings_and_reads_both():
    assert legacy_names.WRITES_NEW_NAMES is False
    assert legacy_names.written("lumeri.project") == f"{OLD}.project"
    assert legacy_names.written("Lumeri Session") == f"{OLD.capitalize()} Session"
    assert legacy_names.written("lumeri_script") == f"{OLD_SCRIPT}_script"
    assert legacy_names.current(f"{OLD}.project") == "lumeri.project"
    assert legacy_names.current("lumeri.project") == "lumeri.project"
    assert legacy_names.current("anything else") == "anything else"
    assert legacy_names.current_prefixed(f"{OLD}://media/a/b", "lumeri", "://") == "lumeri://media/a/b"
    assert legacy_names.current_prefixed("lumeri:usd_stage:s:1", "lumeri", ":") == "lumeri:usd_stage:s:1"
    with pytest.raises(KeyError):
        legacy_names.written("not.kept")


def test_a_new_entry_still_goes_in_the_old_home(user_home):
    assert home.data_path("config.json") == user_home / f".{OLD}" / "config.json"
    assert home.data_path("memory", "daily", "x.md") == user_home / f".{OLD}" / "memory" / "daily" / "x.md"


def test_an_entry_already_in_the_new_home_is_read_there(user_home):
    (user_home / ".lumeri" / "memory").mkdir(parents=True)
    (user_home / f".{OLD}" / "memory").mkdir(parents=True)
    (user_home / f".{OLD}" / "skills").mkdir(parents=True)
    # The first part decides, so a folder is never split between the homes.
    assert home.data_path("memory", "daily", "x.md") == user_home / ".lumeri" / "memory" / "daily" / "x.md"
    assert home.data_path("skills") == user_home / f".{OLD}" / "skills"
    assert home.data_path("config.json") == user_home / f".{OLD}" / "config.json"
    assert home.data_paths("config.json") == (user_home / ".lumeri" / "config.json", user_home / f".{OLD}" / "config.json")


def test_the_shell_sandbox_denies_the_config_in_both_homes(user_home):
    import lumeri.sandbox_v4 as sandbox_v4

    reloaded = importlib.reload(sandbox_v4)
    assert str(user_home / ".lumeri" / "config.json") in reloaded.DEFAULT_CREDENTIAL_DENY
    assert str(user_home / f".{OLD}" / "config.json") in reloaded.DEFAULT_CREDENTIAL_DENY


def test_projects_in_either_schema_are_canonical():
    from lumeri.project_model import PROJECT_SCHEMA, empty_project, is_canonical_project

    project = empty_project()
    assert project["schema"] == PROJECT_SCHEMA == f"{OLD}.project"
    assert project["metadata"]["generator"] == OLD
    assert is_canonical_project(project)
    assert is_canonical_project({**project, "schema": "lumeri.project"})
    assert not is_canonical_project({**project, "schema": "other.project"})


def test_entity_references_are_written_old_and_read_in_either_scheme():
    from lumeri.asset_identity import (
        asset_identity_for_record,
        build_entity_reference,
        parse_entity_reference,
    )

    ref = build_entity_reference(asset_id="a1", account_id="local", fingerprint="f" * 16)
    assert ref.startswith(f"{OLD}://media/")
    new_ref = "lumeri://" + ref.split("://", 1)[1]
    for value in (ref, new_ref):
        parsed = parse_entity_reference(value)
        assert (parsed.account_id, parsed.asset_id) == ("local", "a1")
    with pytest.raises(ValueError):
        parse_entity_reference("other://" + ref.split("://", 1)[1])
    identity = asset_identity_for_record({"asset_id": "a1", "fingerprint": "f" * 16})
    assert identity["traits"]["versioned_asset"]["source"] == f"{OLD}_media_library"


def test_otio_package_metadata_is_read_under_either_key():
    pytest.importorskip("cv2")
    from lumeri.video import opentimelineio_package as otio

    for key in (f"{OLD}_package", "lumeri_package"):
        assert otio._from_otio_json({"metadata": {key: {"id": "p"}}})["package"] == {"id": "p"}
    assert otio._from_otio_json({"metadata": {}})["package"] == {}


def test_usd_and_openxr_identifiers_keep_the_old_prefix():
    from lumeri.openxr_spatial_delivery import build_openxr_spatial_delivery_profile
    from lumeri.usd_stage_identity import build_openusd_stage_identity

    stage = build_openusd_stage_identity({"stage": {}, "usd_layers": [{"layer_id": "l1", "asset_ref": "a"}]})
    assert stage["stage"]["stage_identifier"].startswith(f"{OLD}:usd_stage:{OLD}_stage:")
    profile = build_openxr_spatial_delivery_profile([{"effect": "e", "sources": [{"asset_ref": "a"}]}])
    assert profile["profile"]["profile_identifier"].startswith(f"{OLD}:openxr:{OLD}_openxr_spatial_delivery:")
    assert profile["action_sets"][0]["set_id"] == f"{OLD}_spatial_review"


def test_python_dash_m_with_the_old_name_runs_lumeri(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(runpy, "run_module", lambda name, **kwargs: calls.append((name, kwargs)))
    monkeypatch.delitem(sys.modules, f"{OLD}.__main__", raising=False)
    importlib.import_module(f"{OLD}.__main__")
    assert calls == [("lumeri", {"run_name": "__main__", "alter_sys": True})]
    assert "python -m lumeri" in capsys.readouterr().err


def test_every_kept_name_has_one_new_spelling():
    table = legacy_names.KEPT_NAMES
    assert len(set(table.values())) == len(table)
    assert all(OLD in old.lower() or OLD_SCRIPT in old.lower() for old in table)
    assert not any(OLD in new.lower() or OLD_SCRIPT in new.lower() for new in table.values())


def test_an_untitled_session_in_either_spelling_takes_its_first_message():
    from lumeri.session_history import UNTITLED_SESSION, _session_title

    assert f"{OLD.capitalize()} Session" == UNTITLED_SESSION
    messages = [{"role": "user", "content": "cut the trailer"}]
    for title in (f"{OLD.capitalize()} Session", "Lumeri Session"):
        assert _session_title({"title": title, "messages": messages}) == "cut the trailer"
    assert _session_title({"title": "Trailer", "messages": messages}) == "Trailer"


def test_the_frontend_treats_either_untitled_title_as_untitled():
    text = (REPO / "static/v3/v3.js").read_text(encoding="utf-8")
    assert f'const UNTITLED_SESSION_TITLES = ["{OLD.capitalize()} Session", "Lumeri Session"];' in text
    assert f'!== "{OLD.capitalize()} Session"' not in text
