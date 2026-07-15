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
