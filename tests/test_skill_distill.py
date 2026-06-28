"""Tests for skill DISTILLATION ("沉淀") + recall.

Covers:
- save_skill distills a reusable task into the durable store (name/when/steps).
- Re-saving the same name UPDATES, does not duplicate.
- recall_skills returns a matching skill for a query and ranks relevance.
- recall includes BOTH user-distilled skills and built-in library skills.
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


def _real_jsons(d: Path) -> list[Path]:
    """JSON files in ``d`` ignoring macOS ._* AppleDouble sidecars.

    The external SSD (non-HFS) auto-creates ._name.json resource forks next
    to every written file; those are not real skills and the store ignores
    them, so the tests count the same way.
    """
    return [p for p in sorted(d.glob("*.json")) if not p.name.startswith(".")]


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

    # Persisted to disk as a single JSON file.
    files = _real_jsons(store_dir)
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["name"] == "Cyberpunk grade"
    assert data["source"] == "distilled"
    assert len(data["steps"]) == 3


def test_distill_same_name_updates_not_duplicates(store_dir: Path) -> None:
    from gemia.skill_store import DistilledSkillStore

    store = DistilledSkillStore()
    store.distill("Trim intro", when_to_use="cut the first 2s", steps=["trim 0-2s"])
    first_files = _real_jsons(store_dir)
    assert len(first_files) == 1
    created_at = json.loads(first_files[0].read_text())["created_at"]

    # Re-distill same name with new content.
    store.distill("Trim intro", when_to_use="cut the dead air at the head", steps=["trim 0-3s", "fade in"])

    files = _real_jsons(store_dir)
    assert len(files) == 1, "same-name distill must update, not duplicate"
    data = json.loads(files[0].read_text())
    assert data["when_to_use"] == "cut the dead air at the head"
    assert data["steps"] == ["trim 0-3s", "fade in"]
    assert data["created_at"] == created_at  # created_at preserved across update

    listed = store.list_distilled()
    assert len(listed) == 1


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
