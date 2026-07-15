"""Layer A install-contract meta-check — point-library charter §6.3.

Every verb in ``TOOL_NAMES`` must be FULLY INSTALLED, not merely present in the
schema. A verb is "installed" only when it has, in the SAME change:

* a real (non-stub) dispatcher in ``DISPATCHER``,
* membership in at least one ``TOOL_PACK`` (else it only surfaces on
  full-fallback and the model never proactively uses it),
* exactly one plan-mode classification (plan-allowed XOR plan-blocked),
* an explicit ``_TOOL_COSTS`` row (unless it is a known default-cost utility).

This single drift test collapses the per-point manual checks so that a verb
added to ``TOOL_SCHEMAS`` without wiring the rest fails LOUDLY instead of
silently surfacing only on full-fallback — the exact gap that shipped
``vector_motion`` built-but-not-installed on a branch. Companion:
``test_library_verb_manifest.py`` (Layer B, catches the built-but-not-in-schema
case). See ``gemia/docs/point-library-charter.md`` §6.3 / §10 (failure mode 1).
"""
from __future__ import annotations

from gemia.budget_guard import _TOOL_COSTS
from gemia.plan_mode import PLAN_ALLOWED_TOOLS, PLAN_BLOCKED_TOOLS
from gemia.tool_router import catalog_coverage
from gemia.tools import DISPATCHER
from gemia.tools._schema import TOOL_NAMES

# Verbs that intentionally run on the budget-guard DEFAULT cost rather than an
# explicit ``_TOOL_COSTS`` row (near-free reads / host-side verbs). Bounded on
# purpose: a NEW verb without a cost row fails this test unless it is added here
# in the same reviewed change — a visible, deliberate act, never a silent slip.
BUDGET_DEFAULT_TOOLS = frozenset({
    "recall_skills", "remember", "log_note",
    "project_export_otio", "project_import_otio",
    "read_file", "write_file", "copy_in", "list_dir", "move_file",
    "organize_files", "elicit",
})

# Verbs whose dispatcher is intentionally a stub (none today). A stub that is
# not listed here fails the test — a stub is not "installed".
INTENTIONAL_STUBS: frozenset[str] = frozenset()


def test_tool_names_are_unique_no_magic_count() -> None:
    # No magic 105: the catalog may grow, but names must stay unique so the
    # gate scales without editing a hardcoded number.
    duplicates = sorted({n for n in TOOL_NAMES if TOOL_NAMES.count(n) > 1})
    assert not duplicates, f"duplicate verb names in TOOL_NAMES: {duplicates}"


def test_every_verb_has_a_real_dispatcher() -> None:
    missing = [n for n in TOOL_NAMES if n not in DISPATCHER]
    assert not missing, f"verbs in TOOL_NAMES with no DISPATCHER entry: {missing}"
    stubbed = [
        n for n in TOOL_NAMES
        if getattr(DISPATCHER[n], "__name__", "").startswith("stub_")
        and n not in INTENTIONAL_STUBS
    ]
    assert not stubbed, (
        "verbs present in the schema but wired to a stub dispatcher "
        f"(not installed): {stubbed}"
    )


def test_every_verb_is_in_a_router_pack() -> None:
    missing, unknown = catalog_coverage()
    assert missing == frozenset(), (
        "verbs in the master surface but in NO TOOL_PACK — they only reach the "
        f"model on full-fallback, so the model never proactively uses them: {sorted(missing)}"
    )
    assert unknown == frozenset(), (
        f"packed names that are not real verbs (stale pack entries): {sorted(unknown)}"
    )


def test_every_verb_has_exactly_one_plan_mode_classification() -> None:
    both = sorted(set(TOOL_NAMES) & PLAN_ALLOWED_TOOLS & PLAN_BLOCKED_TOOLS)
    neither = sorted(
        n for n in TOOL_NAMES
        if n not in PLAN_ALLOWED_TOOLS and n not in PLAN_BLOCKED_TOOLS
    )
    assert not both, f"verbs classified as BOTH plan-allowed and plan-blocked: {both}"
    assert not neither, (
        "verbs with no plan-mode classification — a new verb MUST be declared "
        f"plan-safe or plan-blocked (a mutating library is plan-blocked): {neither}"
    )


def test_every_verb_has_an_explicit_budget_cost_or_is_whitelisted() -> None:
    missing = sorted(
        n for n in TOOL_NAMES
        if n not in _TOOL_COSTS and n not in BUDGET_DEFAULT_TOOLS
    )
    assert not missing, (
        "verbs with no _TOOL_COSTS row and not in BUDGET_DEFAULT_TOOLS — add a "
        f"cost row, or (for a near-free verb) add it to the whitelist: {missing}"
    )


def test_install_whitelists_are_bounded() -> None:
    # Whitelists must not accrue stale entries or become an unbounded escape
    # hatch that lets a verb slip past the gate silently.
    stale_budget = sorted(BUDGET_DEFAULT_TOOLS - set(TOOL_NAMES))
    assert not stale_budget, f"stale BUDGET_DEFAULT_TOOLS entries (not real verbs): {stale_budget}"
    stale_stubs = sorted(INTENTIONAL_STUBS - set(TOOL_NAMES))
    assert not stale_stubs, f"stale INTENTIONAL_STUBS entries (not real verbs): {stale_stubs}"
