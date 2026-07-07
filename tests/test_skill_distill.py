"""Tests for skill DISTILLATION ("沉淀") + recall.

Covers (extended for the .lus store, docs/lus-skill-format.md §7 / WP2 gate):
- save_skill distills a reusable task into the durable store as a single
  ``<machine-name>.lus`` file that passes validate_lus.
- Re-saving the same name UPDATES the same file (patch bump, exactly one
  ``.lus.bak``, ``created_at`` preserved), does not duplicate.
- recall_skills returns a matching skill for a query and ranks relevance.
- recall includes BOTH user-distilled skills and built-in library skills.
- dual-read (spec D9): a legacy ``*.json`` beside the store is still
  recalled; on a name collision the ``.lus`` wins.
- save-time validation: a secret in the steps fails with E_LUS_SECRET and
  nothing is written.
- the model-facing ``_recall_view`` shape is PINNED (unchanged by .lus).
- the combo-stub glob ignores a planted ._fake.yaml AppleDouble dotfile.
- save_skill + recall_skills are wired into TOOL_NAMES + DISPATCHER + schema.

The durable store is redirected into tmp via the GEMIA_SKILL_STORE_DIR env
override (monkeypatched), so no real ~/.gemia is touched and no TTY/network
/keys are required.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from gemia.tools._context import AssetRegistry, ToolContext


def _run(coro):
    return asyncio.run(coro)


def _real_files(d: Path, pattern: str) -> list[Path]:
    """Files matching ``pattern`` ignoring macOS ._* AppleDouble sidecars.

    The external SSD (non-HFS) auto-creates ._name.* resource forks next
    to every written file; those are not real skills and the store ignores
    them, so the tests count the same way.
    """
    return [p for p in sorted(d.glob(pattern)) if not p.name.startswith(".")]


def _real_lus(d: Path) -> list[Path]:
    return _real_files(d, "*.lus")


@pytest.fixture()
def store_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the distilled-skill store into a tmp dir."""
    d = tmp_path / "gemia_skills"
    monkeypatch.setenv("GEMIA_SKILL_STORE_DIR", str(d))
    return d


@pytest.fixture()
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        session_id="test_session",
        output_dir=tmp_path / "work",
        registry=AssetRegistry(),
        emit_progress=lambda _: None,
    )


# ── DistilledSkillStore ──────────────────────────────────────────────────


def test_distill_persists_name_when_steps(store_dir: Path) -> None:
    from gemia.lus import validate_lus
    from gemia.skill_store import DistilledSkillStore

    store = DistilledSkillStore()
    skill = store.distill(
        "Cyberpunk grade",
        when_to_use="when the user asks for a neon cyberpunk look",
        steps=["bump teal/orange split tone", "raise contrast", "add glow"],
        notes="keep skin tones natural",
    )

    assert skill["name"] == "Cyberpunk grade"
    assert skill["when_to_use"].startswith("when the user")
    assert skill["steps"][0].startswith("bump teal")
    assert skill["notes"] == "keep skin tones natural"

    # Persisted to disk as a single .lus file named by the derived machine
    # key (spec §7.1), and the stored bytes pass validate_lus.
    files = _real_lus(store_dir)
    assert len(files) == 1
    assert files[0].name == "cyberpunk-grade.lus"
    meta, body, warnings = validate_lus(
        files[0].read_text(encoding="utf-8"), filename=files[0].name)
    assert meta.title == "Cyberpunk grade"
    assert meta.name == "cyberpunk-grade"
    assert meta.version == "1.0.0"
    assert "## Steps" in body
    assert not any(w.code.startswith("W_LUS_CHECKSUM") for w in warnings)


def test_distill_same_name_updates_not_duplicates(store_dir: Path) -> None:
    from gemia.lus import validate_lus
    from gemia.skill_store import DistilledSkillStore

    store = DistilledSkillStore()
    store.distill("Trim intro", when_to_use="cut the first 2s", steps=["trim 0-2s"])
    first_files = _real_lus(store_dir)
    assert len(first_files) == 1
    first_meta, _, _ = validate_lus(first_files[0].read_text(encoding="utf-8"))
    assert first_meta.version == "1.0.0"

    # Re-distill same name with new content.
    store.distill("Trim intro", when_to_use="cut the dead air at the head",
                  steps=["trim 0-3s", "fade in"])

    files = _real_lus(store_dir)
    assert len(files) == 1, "same-name distill must update, not duplicate"
    meta, body, _ = validate_lus(files[0].read_text(encoding="utf-8"))
    assert meta.description == "cut the dead air at the head"
    assert "1. trim 0-3s" in body and "2. fade in" in body
    assert meta.created_at == first_meta.created_at  # preserved across update
    assert meta.version == "1.0.1"  # patch bump on changed content (§7.1)

    # Exactly ONE .bak generation is kept.
    baks = _real_files(store_dir, "*.lus.bak")
    assert len(baks) == 1
    bak_meta, _, _ = validate_lus(baks[0].read_text(encoding="utf-8"))
    assert bak_meta.version == "1.0.0"

    listed = store.list_distilled()
    assert len(listed) == 1

    # Idempotent no-change re-save: version stays put, still one file+bak.
    store.distill("Trim intro", when_to_use="cut the dead air at the head",
                  steps=["trim 0-3s", "fade in"])
    meta2, _, _ = validate_lus(files[0].read_text(encoding="utf-8"))
    assert meta2.version == "1.0.1"
    assert len(_real_lus(store_dir)) == 1
    assert len(_real_files(store_dir, "*.lus.bak")) == 1


def test_recall_returns_matching_skill_and_ranks(store_dir: Path) -> None:
    from gemia.skill_store import DistilledSkillStore, recall_skills

    store = DistilledSkillStore()
    store.distill(
        "Cyberpunk grade",
        when_to_use="neon cyberpunk color grade for night city footage",
        steps=["teal/orange", "glow"],
        tags=["cyberpunk", "grade", "neon"],
    )
    store.distill(
        "Vintage film",
        when_to_use="warm faded vintage film look",
        steps=["lower saturation", "add grain"],
        tags=["vintage", "grade"],
    )

    results = recall_skills("cyberpunk neon grade", include_library=False)
    assert results, "recall should find the cyberpunk skill"
    names = [r["name"] for r in results]
    assert "Cyberpunk grade" in names
    # The more relevant cyberpunk skill must rank above the vintage one.
    assert names.index("Cyberpunk grade") == 0
    top = results[0]
    assert "when_to_use" in top and "steps" in top
    assert top["steps"] == ["teal/orange", "glow"]


def test_recall_includes_library_and_user(store_dir: Path) -> None:
    from gemia.skill_store import DistilledSkillStore, recall_skills, _library_skills

    # Ensure the library is actually populated in this environment; otherwise
    # the "both sources" assertion would be vacuous.
    library = _library_skills()
    assert library, "expected built-in library skills to be available"

    store = DistilledSkillStore()
    store.distill(
        "My custom timeline trick",
        when_to_use="arrange timeline clips in a special order",
        steps=["insert clip", "trim", "transition"],
        tags=["timeline"],
    )

    results = recall_skills("timeline", include_library=True, limit=25)
    sources = {r["source"] for r in results}
    assert "distilled" in sources, "recall must include user-distilled skills"
    assert "library" in sources, "recall must include built-in library skills"
    names = [r["name"] for r in results]
    assert "My custom timeline trick" in names


def test_recall_finds_zh_skill_by_zh_query(store_dir: Path) -> None:
    """e2e: a Chinese-titled skill lands as .lus and is recalled by a zh query."""
    from gemia.skill_store import DistilledSkillStore, recall_skills

    store = DistilledSkillStore()
    saved = store.distill(
        "卡点粗剪",
        when_to_use="用户要求按音乐节拍卡点剪辑视频素材",
        steps=["用 `analyze_media` 找节拍", "用 `timeline_insert_clip` 对齐插入"],
        tags=["卡点", "踩点"],
    )
    assert saved["file"].endswith(".lus")

    results = recall_skills("卡点", include_library=False)
    assert results
    assert results[0]["name"] == "卡点粗剪"
    assert results[0]["steps"][0].startswith("用 `analyze_media`")


# ── dual-read: legacy JSON beside .lus (spec D9) ─────────────────────────


def _write_legacy_json(store_dir: Path, name: str, **over) -> Path:
    record = {
        "name": name,
        "source": "distilled",
        "when_to_use": "warm faded vintage film look",
        "steps": ["lower saturation", "add grain"],
        "notes": "",
        "tags": ["vintage"],
        "created_at": "2026-07-01T00:00:00+00:00",
        "updated_at": "2026-07-01T00:00:00+00:00",
    }
    record.update(over)
    store_dir.mkdir(parents=True, exist_ok=True)
    path = store_dir / f"{name}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def test_legacy_json_planted_beside_store_is_still_recalled(store_dir: Path) -> None:
    from gemia.skill_store import DistilledSkillStore, recall_skills

    store = DistilledSkillStore()
    store.distill("Beat cut", when_to_use="beat-synced rough cut",
                  steps=["find beats", "insert clips"], tags=["beat"])
    _write_legacy_json(store_dir, "Vintage film",
                       when_to_use="warm faded vintage film look",
                       tags=["vintage", "film"])

    results = recall_skills("vintage film look", include_library=False)
    names = [r["name"] for r in results]
    assert "Vintage film" in names, "unmigrated legacy JSON must still be recalled"

    # And both formats coexist in the listing.
    listed_names = {s["name"] for s in store.list_distilled()}
    assert {"Beat cut", "Vintage film"} <= listed_names


def test_same_name_lus_wins_over_legacy_json(store_dir: Path) -> None:
    from gemia.skill_store import DistilledSkillStore, recall_skills

    store = DistilledSkillStore()
    # Legacy JSON with the SAME display name but stale steps.
    _write_legacy_json(store_dir, "Beat cut",
                       when_to_use="OLD json when_to_use",
                       steps=["OLD step"], tags=["beat"])
    store.distill("Beat cut", when_to_use="beat-synced rough cut",
                  steps=["find beats", "insert clips"], tags=["beat"])

    results = recall_skills("beat", include_library=False)
    matches = [r for r in results if r["name"] == "Beat cut"]
    assert len(matches) == 1, "collision must yield ONE result (.lus wins, D9)"
    assert matches[0]["steps"] == ["find beats", "insert clips"]
    assert matches[0]["when_to_use"] == "beat-synced rough cut"

    listed = [s for s in store.list_distilled() if s["name"] == "Beat cut"]
    assert len(listed) == 1
    assert listed[0]["file"].endswith(".lus")


# ── save-time validation (spec §7.1 step 1) ──────────────────────────────


def test_save_skill_with_secret_fails_typed_and_writes_nothing(
        store_dir: Path, ctx: ToolContext) -> None:
    from gemia.lus import LusValidationError
    from gemia.tools import save_skill

    with pytest.raises(LusValidationError) as excinfo:
        _run(save_skill.dispatch_save_skill(
            {
                "name": "Sneaky skill",
                "when_to_use": "never",
                "steps": ["export API key sk-abcdefghij0123456789 to env"],
            },
            ctx,
        ))
    assert excinfo.value.code == "E_LUS_SECRET"
    assert "sk-abcdefghij0123456789" not in str(excinfo.value)
    assert _real_lus(store_dir) == [], "nothing may be written on rejection"
    assert _real_files(store_dir, "*.json") == []


def test_save_skill_with_absolute_path_fails_typed(store_dir: Path, ctx: ToolContext) -> None:
    from gemia.lus import LusValidationError
    from gemia.tools import save_skill

    with pytest.raises(LusValidationError) as excinfo:
        _run(save_skill.dispatch_save_skill(
            {
                "name": "Path skill",
                "when_to_use": "never",
                "steps": ["read /Users/somebody/clip.mp4"],
            },
            ctx,
        ))
    assert excinfo.value.code == "E_LUS_ABS_PATH"
    assert _real_lus(store_dir) == []


# ── model-facing recall view shape (PINNED, spec §7.2) ───────────────────


def test_recall_view_shape_is_pinned(store_dir: Path) -> None:
    """The _recall_view projection is the model-facing contract of
    recall_skills — its key set must not change with the .lus backend."""
    from gemia.skill_store import DistilledSkillStore, recall_skills

    store = DistilledSkillStore()
    store.distill(
        "Denoise then sharpen",
        when_to_use="grainy low-light clip that needs cleanup",
        steps=["denoise", "sharpen"],
        notes="denoise before sharpen",
        tags=["denoise"],
    )
    results = recall_skills("grainy low-light cleanup", include_library=False)
    assert results
    view = results[0]
    assert set(view.keys()) == {"name", "source", "when_to_use", "steps", "notes", "tags"}
    assert view["name"] == "Denoise then sharpen"
    assert view["source"] == "distilled"
    assert view["when_to_use"] == "grainy low-light clip that needs cleanup"
    assert view["steps"] == ["denoise", "sharpen"]
    assert view["notes"] == "denoise before sharpen"
    assert view["tags"] == ["denoise"]
    assert isinstance(view["steps"], list)
    assert all(isinstance(s, str) for s in view["steps"])


# ── dotfile glob hardening ───────────────────────────────────────────────


def test_combo_glob_ignores_appledouble_dotfile(tmp_path: Path) -> None:
    """A planted ._fake.yaml binary sidecar must not crash the combo glob."""
    from gemia.ai import skill_context

    combos = tmp_path / "skills" / "_combos"
    combos.mkdir(parents=True)
    # A valid combo file.
    (combos / "transition+color-grade.yaml").write_text(
        "trigger_skills: [transition, color-grade]\nplan_template: []\n",
        encoding="utf-8",
    )
    # An AppleDouble resource-fork sidecar: binary, would UnicodeDecodeError.
    (combos / "._fake.yaml").write_bytes(b"\x00\x05\x16\x07\xff\xfe\xb0bad-bytes")

    # _iter_yaml must skip the dotfile, returning only the real combo file.
    found = skill_context._iter_yaml(combos)
    names = [p.name for p in found]
    assert "transition+color-grade.yaml" in names
    assert "._fake.yaml" not in names
    # Reading the surviving file must not raise (no binary sidecars leak through).
    for p in found:
        p.read_text(encoding="utf-8")

    # The real combo path (against the shipped skills/_combos, which also has
    # ._*.yaml AppleDouble sidecars on the external SSD) must not crash.
    chunks, ids = skill_context._combo_stubs(["transition", "color-grade"], "fade transition")
    assert isinstance(chunks, list)
    assert isinstance(ids, list)


# ── save_skill / recall_skills dispatchers ───────────────────────────────


def test_dispatch_save_skill_distills(store_dir: Path, ctx: ToolContext) -> None:
    from gemia.tools import save_skill

    result = _run(save_skill.dispatch_save_skill(
        {
            "name": "Denoise then sharpen",
            "when_to_use": "grainy low-light clip that needs cleanup",
            "steps": ["denoise", "sharpen", "regrade"],
            "notes": "denoise before sharpen to avoid amplifying noise",
        },
        ctx,
    ))

    assert result["skill"] == "Denoise then sharpen"
    assert result["source"] == "distilled"
    assert result["steps"] == ["denoise", "sharpen", "regrade"]
    assert Path(result["path"]).exists()


def test_dispatch_recall_skills_finds_distilled(store_dir: Path, ctx: ToolContext) -> None:
    from gemia.tools import save_skill

    _run(save_skill.dispatch_save_skill(
        {
            "name": "Denoise then sharpen",
            "when_to_use": "grainy low-light clip that needs cleanup",
            "steps": ["denoise", "sharpen"],
        },
        ctx,
    ))

    out = _run(save_skill.dispatch_recall_skills({"query": "grainy low-light cleanup"}, ctx))
    assert out["count"] >= 1
    names = [s["name"] for s in out["skills"]]
    assert "Denoise then sharpen" in names


def test_dispatch_recall_skills_includes_both_sources(store_dir: Path, ctx: ToolContext) -> None:
    from gemia.tools import save_skill

    _run(save_skill.dispatch_save_skill(
        {
            "name": "Timeline arrange combo",
            "when_to_use": "arrange clips on the timeline",
            "steps": ["insert", "trim"],
            "tags": ["timeline"],
        },
        ctx,
    ))
    out = _run(save_skill.dispatch_recall_skills(
        {"query": "timeline", "limit": 25, "include_library": True},
        ctx,
    ))
    sources = {s["source"] for s in out["skills"]}
    assert "distilled" in sources
    assert "library" in sources


# ── wiring ───────────────────────────────────────────────────────────────


def test_tools_registered_in_names_dispatcher_schema() -> None:
    from gemia import tools as T

    for name in ("save_skill", "recall_skills"):
        assert name in T.TOOL_NAMES, f"{name} missing from TOOL_NAMES"
        assert name in T.DISPATCHER, f"{name} missing from DISPATCHER"
        assert T.DISPATCHER[name].__name__ != f"stub_{name}", f"{name} is still a stub"
        schemas = [t for t in T.TOOL_SCHEMAS if t["function"]["name"] == name]
        assert len(schemas) == 1, f"{name} missing/duplicated in TOOL_SCHEMAS"

    # save_skill must keep its dispatch_save_skill identity (build-verb contract).
    assert T.DISPATCHER["save_skill"].__name__ == "dispatch_save_skill"
    assert T.DISPATCHER["recall_skills"].__name__ == "dispatch_recall_skills"


def test_save_skill_schema_nudges_distillation() -> None:
    from gemia.tools._schema import TOOL_SCHEMAS

    save = next(t for t in TOOL_SCHEMAS if t["function"]["name"] == "save_skill")
    desc = save["function"]["description"].lower()
    assert "distill" in desc
    recall = next(t for t in TOOL_SCHEMAS if t["function"]["name"] == "recall_skills")
    rdesc = recall["function"]["description"].lower()
    assert "first" in rdesc or "reuse" in rdesc


def test_save_skill_backward_compat_source_path(tmp_path: Path, ctx: ToolContext) -> None:
    """When given a 'source' file, save_skill still archives it (build path)."""
    from gemia.tools import save_skill

    builds = ctx.output_dir / "builds" / "b1"
    builds.mkdir(parents=True)
    (builds / "script.py").write_text("print('hi')")

    result = _run(save_skill.dispatch_save_skill(
        {"source": "builds/b1/script.py", "name": "Archived Script"},
        ctx,
    ))
    # build.dispatch_save_skill returns a slug under 'skill'.
    assert result["skill"] == "archived-script"
    assert Path(result["path"]).exists()
