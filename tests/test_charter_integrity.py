"""Charter reference-integrity meta-gate — point-library charter §13.1(5) / §13.2.

Guards the charter's own honesty: the "law" must not cite a "court" that does
not exist. Every ``tests/test_*.py`` the charter marks 【已存在】 (present) MUST
resolve to a real file, and the five ratification-gate test files named in
§13.1 must exist. This is the last item of the charter's coming-into-force set
(§13.1); with it green, all three lived failure modes have landed, adversarially
verified gates and the charter may move DRAFT → RATIFIED.
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_CHARTER = _REPO / "gemia" / "docs" / "point-library-charter.md"
_TEST_PATH = re.compile(r"tests/test_[a-z0-9_]+\.py")

# The concrete gate files that enforce the three lived failure modes — the
# load-bearing ratification set (§13.1 items 1-5). These MUST exist verbatim.
_RATIFICATION_GATES = (
    "tests/test_tool_catalog_contract.py",       # FM1 Layer A
    "tests/test_library_verb_manifest.py",       # FM1 Layer B (auto-discovery)
    "tests/test_addressability_roundtrip.py",    # FM2 verbatim-addressability
    "tests/test_library_ledger_contract.py",     # FM3 library side
    "tests/test_v3_ledger_partial_disclosure.py",  # FM3 host side
    "tests/test_charter_integrity.py",           # this meta-gate (§13.1 item 5)
)


def test_charter_file_exists() -> None:
    assert _CHARTER.is_file(), f"canonical charter missing at {_CHARTER}"


def test_ratification_gate_files_exist() -> None:
    missing = [p for p in _RATIFICATION_GATES if not (_REPO / p).is_file()]
    assert not missing, f"§13.1 ratification-gate test files missing: {missing}"


def test_charter_referenced_tests_exist() -> None:
    """Every test file the charter marks 【已存在】 must actually exist."""
    claimed_present: set[str] = set()
    for line in _CHARTER.read_text(encoding="utf-8").splitlines():
        if "已存在" not in line:
            continue
        for path in _TEST_PATH.findall(line):
            claimed_present.add(path)
    liars = sorted(p for p in claimed_present if not (_REPO / p).is_file())
    assert not liars, (
        "the charter marks these test files 【已存在】 but they do not exist — "
        f"fix the charter (mark 【待落地】) or create them: {liars}"
    )


# ── skill/library boundary (charter §14) ─────────────────────────────────

_LUS_SPEC = _REPO / "docs" / "lus-skill-format.md"


def test_skill_boundary_docs_cross_reference() -> None:
    """Charter §14 arbitrates the two rulebooks' worldviews; each must cite
    the other, and the three boundary rules must exist as section anchors —
    deleting or renaming the section body (not just the header mention)
    turns this red."""
    charter = _CHARTER.read_text(encoding="utf-8")
    spec = _LUS_SPEC.read_text(encoding="utf-8")
    assert "## §14 技能层" in charter, "charter §14 (skill boundary) section missing"
    for anchor in ("S1 · 品味归库", "S2 · 流程归技能", "S3 · 技能引用库的创作语言",
                   "§14.3 手艺信号"):
        assert anchor in charter, f"charter §14 anchor missing: {anchor}"
    assert "lus-skill-format.md" in charter, "charter must cite the .lus spec"
    assert "point-library-charter.md" in spec, "the .lus spec must cite the charter"
    assert "E_LUS_CRAFT_NUMBERS" in charter, "charter §14 must name its guard code"
    assert "E_LUS_CRAFT_NUMBERS" in spec, "spec addendum must document the guard code"


def test_craft_guard_registry_matches_installed_tools() -> None:
    """Build==install (P11) applied to the guard itself: a domain may be
    listed as closed only while the library's tool verb is actually live,
    and every installed creative verb must be either guarded or on the
    bounded pending list — no silent gaps in either direction (§14.1)."""
    from gemia.lus import CRAFT_CLOSED_DOMAINS, CRAFT_GUARD_PENDING_VERBS
    from gemia.tools._schema import TOOL_NAMES

    closed = {verb for _domain, verb, _patterns in CRAFT_CLOSED_DOMAINS}
    for domain, verb, patterns in CRAFT_CLOSED_DOMAINS:
        assert verb in TOOL_NAMES, (
            f"CRAFT_CLOSED_DOMAINS claims {domain!r} is closed by {verb!r}, "
            "but that tool verb is not installed — remove the domain or "
            "install the library (charter §14/P11)"
        )
        assert patterns, f"domain {domain!r} lists no guard patterns"

    # Reference set = the charter's installed creative point-library verbs.
    # A new creative verb must be added here AND either guarded or explicitly
    # pending in the same PR (charter §14.1 扩表义务).
    reference = {"grade", "vector_motion",
                 "kinetic_type", "camera", "compose", "edit_grammar",
                 "rhythm_edit"}
    assert reference <= set(TOOL_NAMES), "reference creative verbs must be installed"
    assert closed | CRAFT_GUARD_PENDING_VERBS == reference, (
        "installed creative verbs must be exactly partitioned into "
        f"guarded ∪ pending; got closed={sorted(closed)}, "
        f"pending={sorted(CRAFT_GUARD_PENDING_VERBS)}"
    )
    assert not (closed & CRAFT_GUARD_PENDING_VERBS), (
        "a verb cannot be both guarded and pending"
    )
