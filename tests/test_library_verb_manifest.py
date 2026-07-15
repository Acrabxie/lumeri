"""Layer B auto-discovery meta-check — point-library charter §6.3.

The ONLY gate that catches a tool-verb library that is BUILT BUT NOT INSTALLED:
a module under ``gemia/tools/`` that exposes a module-level ``dispatch``
coroutine (the single-verb tool convention, e.g. ``vector_motion.py``) but was
never wired into ``DISPATCHER`` / ``TOOL_NAMES``. ``vector_motion.py`` sat
exactly like this on a branch — a finished engine with a ``dispatch`` entry
point, invisible to the live model because no registration referenced it.

The expected set is DISCOVERED FROM THE FILESYSTEM, never from a hand-kept list
— a hand-kept list would reproduce the very "forgot to register" omission this
gate exists to catch. See ``gemia/docs/point-library-charter.md`` §6.3 / §10
(failure mode 1). Companion: ``test_tool_catalog_contract.py`` (Layer A).
"""
from __future__ import annotations

import ast
import importlib
import pathlib

import gemia.tools as tools_pkg
from gemia.tools import DISPATCHER

# Tool modules whose module-level ``dispatch`` is deliberately NOT a top-level
# verb (a helper/entry point reused elsewhere). Bounded; none today. A module
# added here is a visible, reviewed exemption — never a silent omission.
NON_VERB_DISPATCH_MODULES: frozenset[str] = frozenset()


def _tool_modules_with_bare_dispatch() -> list[str]:
    """Every ``gemia/tools/<name>.py`` that defines a module-level ``dispatch``.

    Skips private/dunder modules and non-UTF-8 AppleDouble resource-fork files
    (``._*.py``) that appear on external/exFAT volumes.
    """
    toolsdir = pathlib.Path(tools_pkg.__file__).parent
    found: list[str] = []
    for path in sorted(toolsdir.glob("*.py")):
        name = path.name
        if name.startswith((".", "_")) or name == "__init__.py":
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # resource-fork artifact on an external volume
        tree = ast.parse(source)
        if any(
            isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
            and node.name == "dispatch"
            for node in tree.body
        ):
            found.append(path.stem)
    return found


def test_every_tool_module_dispatch_is_installed() -> None:
    wired = {id(fn) for fn in DISPATCHER.values()}
    orphans: list[str] = []
    for stem in _tool_modules_with_bare_dispatch():
        if stem in NON_VERB_DISPATCH_MODULES:
            continue
        module = importlib.import_module(f"gemia.tools.{stem}")
        fn = getattr(module, "dispatch", None)
        if fn is None or id(fn) not in wired:
            orphans.append(stem)
    assert not orphans, (
        "tool module(s) expose a module-level `dispatch` but are NOT wired into "
        f"DISPATCHER/TOOL_NAMES — built but not installed: {orphans}. Register the "
        "verb in gemia/tools/_schema.py, the DISPATCHER _REAL table, budget_guard, "
        "plan_mode, a tool_router pack, and system_v3.md (gemia/docs/"
        "point-library-charter.md §6). If the module is intentionally not a verb, "
        "add it to NON_VERB_DISPATCH_MODULES."
    )


def test_non_verb_whitelist_is_bounded() -> None:
    stale = NON_VERB_DISPATCH_MODULES - set(_tool_modules_with_bare_dispatch())
    assert not stale, (
        f"NON_VERB_DISPATCH_MODULES has stale entries (no such tool module): {sorted(stale)}"
    )
