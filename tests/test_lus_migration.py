"""Tests for the one-shot legacy-JSON → .lus skill migrator.

Spec: docs/lus-skill-format.md §8 (WP3 test gate, §11). Covers:

- fixture dir with 3 legacy JSONs (one CJK name, one with an unparseable
  timestamp, one containing an absolute path) → 2 migrated with correct
  field mapping + ``.json.bak`` present, 1 skipped-with-violation and left
  untouched;
- idempotency — a second run over the result changes ZERO bytes (tree hash
  equal);
- dual-read recall parity before/after migration;
- ``--dry-run`` writes nothing;
- derived-name conflicts abort only that file;
- the ``scripts/migrate_skills_to_lus.py`` CLI wrapper.

Everything runs against a tmp store root (GEMIA_SKILL_STORE_DIR or an
explicit ``root=``) — the real ~/.gemia/skills is never touched.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from gemia.lus import derive_name, validate_lus
from gemia.skill_store import migrate_distilled_to_lus, recall_skills, DistilledSkillStore

REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture()
def store_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "gemia_skills"
    d.mkdir()
    monkeypatch.setenv("GEMIA_SKILL_STORE_DIR", str(d))
    return d


def _write_json(d: Path, filename: str, record: dict) -> Path:
    path = d / filename
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def _legacy_fixture_trio(d: Path) -> dict[str, Path]:
    """The WP3 gate trio: CJK name, unparseable timestamp, abs-path violation."""
    cjk = _write_json(d, "音频闪避.json", {
        "name": "音频闪避",
        "source": "distilled",
        "when_to_use": "旁白与背景音乐重叠时自动压低音乐音量",
        "steps": ["用 `analyze_media` 找旁白区间", "用 `mix_audio` 压低音乐"],
        "notes": "闪避深度默认 -12dB",
        "tags": ["闪避", "audio ducking"],
        "created_at": "2026-07-01T00:00:00+00:00",
        "updated_at": "2026-07-02T00:00:00+00:00",
    })
    badts = _write_json(d, "rough_cut.json", {
        "name": "Rough cut",
        "source": "distilled",
        "when_to_use": "assemble a first rough cut from selected clips",
        "steps": ["pick clips", "insert in order"],
        "notes": "",
        "tags": ["rough cut"],
        "created_at": "yesterday",          # unparseable → migration time
        "updated_at": "later",
    })
    violation = _write_json(d, "bad_paths.json", {
        "name": "Bad paths",
        "source": "distilled",
        "when_to_use": "never — this legacy skill leaks a user path",
        "steps": ["read /Users/somebody/Movies/raw.mp4 and trim"],
        "notes": "",
        "tags": ["bad"],
        "created_at": "2026-07-01T00:00:00+00:00",
        "updated_at": "2026-07-01T00:00:00+00:00",
    })
    return {"cjk": cjk, "badts": badts, "violation": violation}


def _tree_hash(d: Path) -> dict[str, str]:
    """name → sha256 of bytes for every real (non-dotfile) file in ``d``."""
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(d.iterdir())
        if p.is_file() and not p.name.startswith(".")
    }


# ── the WP3 gate: trio migration + field mapping ─────────────────────────


def test_migrate_trio_two_migrated_one_violation(store_dir: Path) -> None:
    paths = _legacy_fixture_trio(store_dir)
    violation_bytes = paths["violation"].read_bytes()

    report = migrate_distilled_to_lus(store_dir)
    by_file = {e["file"]: e["status"] for e in report}
    assert by_file["音频闪避.json"] == "migrated"
    assert by_file["rough_cut.json"] == "migrated"
    assert by_file["bad_paths.json"] == "skipped(violation: E_LUS_ABS_PATH)"

    # CJK skill: machine name is the deterministic skill-<sha8> form (D2/§7.1)
    cjk_machine = derive_name("音频闪避")
    assert cjk_machine.startswith("skill-")
    cjk_lus = store_dir / f"{cjk_machine}.lus"
    assert cjk_lus.exists()
    meta, body, warnings = validate_lus(cjk_lus.read_text(encoding="utf-8"),
                                        filename=cjk_lus.name)
    assert warnings == []                       # fresh checksum, name matches
    assert meta.title == "音频闪避"
    assert meta.version == "1.0.0"
    assert meta.domain == "video"
    assert meta.language == "zh"
    assert meta.description == "旁白与背景音乐重叠时自动压低音乐音量"
    assert meta.triggers == ("闪避", "audio ducking")
    # timestamps carried over verbatim (parseable, §8.2)
    assert meta.created_at == "2026-07-01T00:00:00+00:00"
    assert meta.updated_at == "2026-07-02T00:00:00+00:00"
    # tools auto-extracted from steps; mix_audio is plan-blocked → mutates
    assert "mix_audio" in meta.tools_used
    assert meta.safety_mutates_project is True
    assert meta.safety_requires_paid_generation is False
    assert "1. 用 `analyze_media` 找旁白区间" in body
    assert "## Pitfalls" in body and "-12dB" in body
    # original renamed to .json.bak, not deleted (§8.3)
    assert not paths["cjk"].exists()
    assert (store_dir / "音频闪避.json.bak").exists()

    # unparseable-timestamp skill: both timestamps = migration time, noted
    badts_lus = store_dir / f"{derive_name('Rough cut')}.lus"
    assert badts_lus.exists()
    meta2, _body2, _ = validate_lus(badts_lus.read_text(encoding="utf-8"))
    assert meta2.created_at == meta2.updated_at
    assert meta2.created_at != "yesterday"
    detail = next(e["detail"] for e in report if e["file"] == "rough_cut.json")
    assert "migration time" in detail
    assert (store_dir / "rough_cut.json.bak").exists()

    # violating skill: NOT migrated, NOT renamed, bytes untouched (§8.2)
    assert paths["violation"].exists()
    assert paths["violation"].read_bytes() == violation_bytes
    assert not (store_dir / "bad_paths.json.bak").exists()
    assert not (store_dir / f"{derive_name('Bad paths')}.lus").exists()


def test_migrate_idempotent_second_run_is_byte_identical(store_dir: Path) -> None:
    _legacy_fixture_trio(store_dir)
    migrate_distilled_to_lus(store_dir)
    snapshot = _tree_hash(store_dir)

    report2 = migrate_distilled_to_lus(store_dir)
    assert _tree_hash(store_dir) == snapshot, "second run must change zero bytes"
    # the only .json left is the violating one; it is re-reported, unwritten
    assert [e["status"] for e in report2] == ["skipped(violation: E_LUS_ABS_PATH)"]

    # third run, for luck — still identical
    migrate_distilled_to_lus(store_dir)
    assert _tree_hash(store_dir) == snapshot


def test_migrate_skips_when_lus_or_bak_already_exists(store_dir: Path) -> None:
    _write_json(store_dir, "beat_cut.json", {
        "name": "Beat cut", "source": "distilled",
        "when_to_use": "beat cut", "steps": ["do it"], "tags": ["beat"],
        "created_at": "2026-07-01T00:00:00+00:00",
        "updated_at": "2026-07-01T00:00:00+00:00",
    })
    # A .lus with the derived name already exists (e.g. re-saved post-migration).
    store = DistilledSkillStore(store_dir)
    store.distill("Beat cut", when_to_use="beat cut fresh", steps=["new step"])

    snapshot = _tree_hash(store_dir)
    report = migrate_distilled_to_lus(store_dir)
    assert [e["status"] for e in report] == ["skipped(already)"]
    assert _tree_hash(store_dir) == snapshot


def test_migrate_conflict_on_derived_name_aborts_that_file_only(store_dir: Path) -> None:
    _write_json(store_dir, "a.json", {
        "name": "Beat Cut!", "source": "distilled",
        "when_to_use": "one", "steps": ["s1"], "tags": ["x"],
        "created_at": "2026-07-01T00:00:00+00:00",
        "updated_at": "2026-07-01T00:00:00+00:00",
    })
    _write_json(store_dir, "b.json", {
        "name": "beat cut", "source": "distilled",
        "when_to_use": "two", "steps": ["s2"], "tags": ["y"],
        "created_at": "2026-07-01T00:00:00+00:00",
        "updated_at": "2026-07-01T00:00:00+00:00",
    })
    report = migrate_distilled_to_lus(store_dir)
    statuses = sorted(e["status"] for e in report)
    assert statuses == ["conflict(name)", "migrated"]
    # exactly one .lus landed; the conflicting original is untouched
    lus_files = [p for p in store_dir.glob("*.lus") if not p.name.startswith(".")]
    assert len(lus_files) == 1
    conflicted = next(e for e in report if e["status"] == "conflict(name)")
    assert (store_dir / conflicted["file"]).exists()


def test_migrate_dry_run_writes_nothing(store_dir: Path) -> None:
    _legacy_fixture_trio(store_dir)
    snapshot = _tree_hash(store_dir)
    report = migrate_distilled_to_lus(store_dir, dry_run=True)
    assert _tree_hash(store_dir) == snapshot, "--dry-run must not write"
    statuses = {e["file"]: e["status"] for e in report}
    assert statuses["音频闪避.json"] == "migrated(dry-run)"
    assert statuses["bad_paths.json"] == "skipped(violation: E_LUS_ABS_PATH)"


def test_migrate_default_root_honors_env_override(store_dir: Path) -> None:
    # No explicit root: the migrator must resolve distilled_skills_dir(),
    # which the fixture redirected into tmp via GEMIA_SKILL_STORE_DIR.
    _legacy_fixture_trio(store_dir)
    report = migrate_distilled_to_lus()
    assert sum(1 for e in report if e["status"] == "migrated") == 2
    assert (store_dir / "音频闪避.json.bak").exists()


# ── dual-read recall parity before/after migration (D9) ──────────────────


def test_recall_parity_before_and_after_migration(store_dir: Path) -> None:
    _legacy_fixture_trio(store_dir)

    def _snapshot(query: str):
        return [
            (r["name"], r["when_to_use"], tuple(r["steps"]), tuple(r["tags"]))
            for r in recall_skills(query, include_library=False)
        ]

    before_zh = _snapshot("音频 闪避")
    before_en = _snapshot("rough cut")
    assert any(name == "音频闪避" for name, *_ in before_zh)
    assert any(name == "Rough cut" for name, *_ in before_en)

    migrate_distilled_to_lus(store_dir)

    assert _snapshot("音频 闪避") == before_zh
    assert _snapshot("rough cut") == before_en


# ── CLI wrapper script ───────────────────────────────────────────────────


def _load_migrator_script():
    script = REPO_ROOT / "scripts" / "migrate_skills_to_lus.py"
    spec = importlib.util.spec_from_file_location("migrate_skills_to_lus", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migrator_script_main(store_dir: Path, capsys: pytest.CaptureFixture) -> None:
    _legacy_fixture_trio(store_dir)
    module = _load_migrator_script()

    rc = module.main(["--dry-run", "--root", str(store_dir)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "migrated(dry-run)" in out
    assert "skipped(violation: E_LUS_ABS_PATH)" in out
    assert not list(store_dir.glob("*.lus"))    # dry run wrote nothing

    rc = module.main(["--root", str(store_dir)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "音频闪避.json: migrated" in out
    assert (store_dir / "音频闪避.json.bak").exists()

    # empty follow-up run message path
    empty = store_dir / "empty"
    empty.mkdir()
    rc = module.main(["--root", str(empty)])
    assert rc == 0
    assert "nothing to migrate" in capsys.readouterr().out
