"""The names the rename kept move in two releases; this one writes the new spelling and still reads the old.

The release before this one read both spellings and wrote the old. This one
writes the new spelling, turns an old one into the new where it reads it, and
moves what an earlier release left in ``~/.gemia`` to ``~/.lumeri``. See ``lumeri/legacy_names.py``.
"""
from __future__ import annotations

import errno
import importlib
import importlib.util
import os
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


def test_this_release_writes_the_new_spellings_and_reads_both():
    assert not hasattr(legacy_names, "written") and not hasattr(legacy_names, "WRITES_NEW_NAMES")
    assert legacy_names.current(f"{OLD}.project") == "lumeri.project"
    assert legacy_names.current(f"{OLD.capitalize()} Session") == "Lumeri Session"
    assert legacy_names.current(f"{OLD_SCRIPT}_script") == "lumeri_script"
    assert legacy_names.current("lumeri.project") == "lumeri.project"
    assert legacy_names.current("anything else") == "anything else"
    assert legacy_names.spellings("lumeri_package") == ("lumeri_package", f"{OLD}_package")
    assert legacy_names.current_prefixed(f"{OLD}://media/a/b", "lumeri", "://") == "lumeri://media/a/b"
    assert legacy_names.current_prefixed("lumeri:usd_stage:s:1", "lumeri", ":") == "lumeri:usd_stage:s:1"


def test_every_kept_name_has_one_new_spelling():
    table = legacy_names.KEPT_NAMES
    assert len(set(table.values())) == len(table)
    assert all(OLD in old.lower() or OLD_SCRIPT in old.lower() for old in table)
    assert not any(OLD in new.lower() or OLD_SCRIPT in new.lower() for new in table.values())


def test_a_new_entry_goes_in_the_new_home(user_home):
    assert home.data_path("config.json") == user_home / ".lumeri" / "config.json"
    assert home.data_path("memory", "daily", "x.md") == user_home / ".lumeri" / "memory" / "daily" / "x.md"
    assert not (user_home / f".{OLD}").exists()


def test_an_entry_left_in_the_old_home_moves_the_first_time_it_is_used(user_home):
    old = user_home / f".{OLD}"
    (old / "memory" / "daily").mkdir(parents=True)
    (old / "memory" / "daily" / "x.md").write_text("notes", encoding="utf-8")
    (old / "config.json").write_text("{}", encoding="utf-8")

    path = home.data_path("memory", "daily", "x.md")
    assert path == user_home / ".lumeri" / "memory" / "daily" / "x.md"
    assert path.read_text(encoding="utf-8") == "notes"
    assert not (old / "memory").exists()
    # Only the entry asked for moves; the rest waits until it is used.
    assert (old / "config.json").exists() and not (user_home / ".lumeri" / "config.json").exists()


def test_an_entry_in_both_homes_is_read_in_the_new_one_and_the_old_one_is_left(user_home):
    for root, text in ((user_home / ".lumeri", "new"), (user_home / f".{OLD}", "old")):
        root.mkdir()
        (root / "config.json").write_text(text, encoding="utf-8")
    assert home.data_path("config.json").read_text(encoding="utf-8") == "new"
    assert (user_home / f".{OLD}" / "config.json").read_text(encoding="utf-8") == "old"


def test_an_entry_on_another_file_system_is_copied_across_whole(user_home, monkeypatch):
    old = user_home / f".{OLD}"
    (old / "skills" / "a").mkdir(parents=True)
    (old / "skills" / "a" / "SKILL.md").write_text("a", encoding="utf-8")
    real_rename = os.rename

    def rename(src, dst):
        if Path(src).parent == old:
            raise OSError(errno.EXDEV, "cross-device link")
        return real_rename(src, dst)

    monkeypatch.setattr(home.os, "rename", rename)
    path = home.data_path("skills", "a", "SKILL.md")
    assert path == user_home / ".lumeri" / "skills" / "a" / "SKILL.md"
    assert path.read_text(encoding="utf-8") == "a"
    assert not (old / "skills").exists()
    assert sorted(p.name for p in (user_home / ".lumeri").iterdir()) == ["skills"]


def test_an_entry_that_cannot_move_is_used_where_it_is(user_home, monkeypatch):
    old = user_home / f".{OLD}"
    old.mkdir()
    (old / "config.json").write_text("{}", encoding="utf-8")

    def refuse(src, dst):
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(home.os, "rename", refuse)
    assert home.data_path("config.json") == old / "config.json"
    assert (old / "config.json").exists() and not (user_home / ".lumeri" / "config.json").exists()


def test_the_shell_sandbox_denies_the_config_in_both_homes(user_home):
    import lumeri.sandbox_v4 as sandbox_v4

    reloaded = importlib.reload(sandbox_v4)
    assert str(user_home / ".lumeri" / "config.json") in reloaded.DEFAULT_CREDENTIAL_DENY
    assert str(user_home / f".{OLD}" / "config.json") in reloaded.DEFAULT_CREDENTIAL_DENY


def test_projects_are_written_in_the_new_schema_and_old_ones_are_rewritten_on_read():
    from lumeri.project_model import (
        PROJECT_SCHEMA,
        empty_project,
        is_canonical_project,
        normalize_project,
    )

    project = empty_project()
    assert project["schema"] == PROJECT_SCHEMA == "lumeri.project"
    assert project["metadata"]["generator"] == "lumeri"
    saved = {**project, "schema": f"{OLD}.project", "metadata": {**project["metadata"], "generator": OLD}}
    assert is_canonical_project(saved)
    assert not is_canonical_project({**project, "schema": "other.project"})
    reread = normalize_project(saved)
    assert reread["schema"] == "lumeri.project"
    assert reread["metadata"]["generator"] == "lumeri"


def test_entity_references_are_written_new_and_read_in_either_scheme():
    from lumeri.asset_identity import (
        asset_identity_for_record,
        build_entity_reference,
        parse_entity_reference,
    )

    ref = build_entity_reference(asset_id="a1", account_id="local", fingerprint="f" * 16)
    assert ref.startswith("lumeri://media/")
    old_ref = f"{OLD}://" + ref.split("://", 1)[1]
    for value in (ref, old_ref):
        parsed = parse_entity_reference(value)
        assert (parsed.account_id, parsed.asset_id) == ("local", "a1")
    with pytest.raises(ValueError):
        parse_entity_reference("other://" + ref.split("://", 1)[1])
    identity = asset_identity_for_record({"asset_id": "a1", "fingerprint": "f" * 16})
    assert identity["traits"]["versioned_asset"]["source"] == "lumeri_media_library"


def test_otio_package_metadata_is_read_under_either_key():
    pytest.importorskip("cv2")
    from lumeri.video import opentimelineio_package as otio

    for key in (f"{OLD}_package", "lumeri_package"):
        assert otio._from_otio_json({"metadata": {key: {"id": "p"}}})["package"] == {"id": "p"}
    assert otio._from_otio_json({"metadata": {}})["package"] == {}


def test_usd_and_openxr_identifiers_use_the_new_prefix():
    from lumeri.openxr_spatial_delivery import build_openxr_spatial_delivery_profile
    from lumeri.usd_stage_identity import build_openusd_stage_identity

    stage = build_openusd_stage_identity({"stage": {}, "usd_layers": [{"layer_id": "l1", "asset_ref": "a"}]})
    assert stage["stage"]["stage_identifier"].startswith("lumeri:usd_stage:lumeri_stage:")
    profile = build_openxr_spatial_delivery_profile([{"effect": "e", "sources": [{"asset_ref": "a"}]}])
    assert profile["profile"]["profile_identifier"].startswith("lumeri:openxr:lumeri_openxr_spatial_delivery:")
    assert profile["action_sets"][0]["set_id"] == "lumeri_spatial_review"


def test_an_untitled_session_in_either_spelling_takes_its_first_message():
    from lumeri.session_history import UNTITLED_SESSION, _session_title

    assert UNTITLED_SESSION == "Lumeri Session"
    messages = [{"role": "user", "content": "cut the trailer"}]
    for title in (f"{OLD.capitalize()} Session", "Lumeri Session"):
        assert _session_title({"title": title, "messages": messages}) == "cut the trailer"
    assert _session_title({"title": "Trailer", "messages": messages}) == "Trailer"


def test_the_frontend_treats_either_untitled_title_as_untitled():
    text = (REPO / "static/v3/v3.js").read_text(encoding="utf-8")
    assert f'const UNTITLED_SESSION_TITLES = ["{OLD.capitalize()} Session", "Lumeri Session"];' in text
    assert f'!== "{OLD.capitalize()} Session"' not in text


def test_python_dash_m_with_the_old_name_is_gone():
    assert importlib.util.find_spec(OLD) is None
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert f'"{OLD}"' not in pyproject and f'"{OLD}*"' not in pyproject
