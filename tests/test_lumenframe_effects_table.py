"""Drift guard for the effect dispatch table vs. the self-describing catalogue.

The built-in effect vocabulary now lives in exactly one place: the
``lumenframe.compile.EFFECTS`` dispatch dict (effect type -> adapter fn). Adding
a new effect is "a new function + one dict entry" — no if/elif edits.

The documentation that the agent and third-party authors read
(``lumenframe.catalog.describe_effects`` / ``effect_types``) must stay in
lock-step with that table, so the prompt block can never silently claim an
effect the renderer doesn't implement (or omit one it does). These tests fail
loudly the moment the two drift apart.
"""
from __future__ import annotations

import re

from lumenframe.catalog import EFFECTS_CATALOG, describe_effects, effect_types
from lumenframe.compile import EFFECTS


def test_effects_table_matches_catalog_no_drift():
    """The dispatch table key set must equal the catalogue's effect-type set."""
    dispatch = set(EFFECTS)
    catalog = effect_types()
    assert dispatch == catalog, (
        "effect table / catalogue drift — "
        f"only in compile.EFFECTS: {sorted(dispatch - catalog)}; "
        f"only in catalog.effect_types(): {sorted(catalog - dispatch)}"
    )


def test_every_dispatch_entry_is_callable():
    """Every effect maps to a real adapter callable."""
    for name, fn in EFFECTS.items():
        assert callable(fn), f"effect {name!r} is not callable"


def test_describe_effects_lists_every_dispatch_type():
    """Every dispatch type appears verbatim in the human-readable prompt block."""
    text = describe_effects()
    # Parse the type tokens out of the "  name|alias: args" lines.
    described: set[str] = set()
    for line in text.splitlines():
        m = re.match(r"^  (\S+):", line)
        if m:
            for token in m.group(1).split("|"):
                described.add(token)
    assert described == set(EFFECTS), (
        "describe_effects() / dispatch drift — "
        f"only described: {sorted(described - set(EFFECTS))}; "
        f"only dispatched: {sorted(set(EFFECTS) - described)}"
    )


def test_catalog_alias_row_points_at_same_adapter():
    """Aliases that share a catalogue row share one dispatch adapter (e.g. mirror/flip)."""
    for entry in EFFECTS_CATALOG:
        types = entry["types"]
        adapters = {EFFECTS[t] for t in types}
        assert len(adapters) == 1, (
            f"alias row {types} maps to {len(adapters)} distinct adapters; "
            "aliases must be behaviour-identical"
        )
