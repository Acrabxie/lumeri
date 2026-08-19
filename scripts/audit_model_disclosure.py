#!/usr/bin/env python3
"""Compare the models this code can call against the models publicly disclosed.

The public disclosure lives in another repository (lumeri-site) and is written
by hand, in Chinese, with commercial names and provider routes that no generator
can invent. That is the right way to write it — but it means the page and the
code drift silently, and a disclosure that omits a model the platform actually
calls is a false statement to the payment processor rather than a stale page.

This is a report, not a gate: it prints the difference and exits non-zero when
there is one. Deciding what to publish stays with a person, because some
differences are legitimate (a model reachable only under a config that is off,
a route named differently for readers than for an API).

Usage:
    python scripts/audit_model_disclosure.py [path-to-lumeri-site]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from gemia.model_strength import MEDIA_MODEL_PRIORITY  # noqa: E402

DEFAULT_SITE = Path.home() / "Code" / "lumeri-site"
DISCLOSURE_REL = Path("legal-drafts") / "2026-08-14-zh-CN-pack" / "18-AI模型与生成能力披露.md"

# Identifiers on the page that are deliberately not in MEDIA_MODEL_PRIORITY:
# planning/understanding models are chosen elsewhere, and this audit covers the
# media surfaces only.
PLANNING_SECTION = "理解与规划模型"


def _bare(model: str) -> str:
    return model.split("/", 1)[1] if "/" in model else model


def code_models() -> dict[str, set[str]]:
    """Every media model this code can reach, per surface, provider prefix removed."""
    out: dict[str, set[str]] = {}
    for slot, backends in MEDIA_MODEL_PRIORITY.items():
        names: set[str] = set()
        for models in backends.values():
            names.update(_bare(m) for m in models)
        out[slot] = names
    return out


def disclosed_models(path: Path) -> set[str]:
    """Identifiers in the disclosure's media tables, excluding the planning table."""
    text = path.read_text(encoding="utf-8")
    # Drop the planning section: those models are not part of MEDIA_MODEL_PRIORITY.
    head = text.split(f"## 5. {PLANNING_SECTION}")[0]
    head = re.split(rf"##\s*\d*\.?\s*{PLANNING_SECTION}", head)[0]
    found = re.findall(r"`([a-z0-9][a-z0-9.\-/]+)`", head)
    # Dates and other backticked prose are not model identifiers.
    return {_bare(m) for m in found if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", m)}


def main() -> int:
    site = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else DEFAULT_SITE
    disclosure = site / DISCLOSURE_REL
    if not disclosure.is_file():
        print(f"disclosure not found at {disclosure}", file=sys.stderr)
        return 2

    by_slot = code_models()
    reachable = set().union(*by_slot.values())
    published = disclosed_models(disclosure)

    missing = sorted(reachable - published)
    extra = sorted(published - reachable)

    print(f"code (media surfaces): {len(reachable)} identifiers")
    print(f"disclosed on the page: {len(published)} identifiers\n")

    if missing:
        print("REACHABLE BUT NOT DISCLOSED — the page says its tables are exhaustive,")
        print("so each of these is either a model to publish or a route to disable:")
        for name in missing:
            slots = ", ".join(sorted(s for s, v in by_slot.items() if name in v))
            print(f"  - {name}  [{slots}]")
        print()
    if extra:
        print("DISCLOSED BUT NOT REACHABLE — harmless to a reviewer, but it claims a")
        print("capability the code no longer has:")
        for name in extra:
            print(f"  - {name}")
        print()
    if not missing and not extra:
        print("in sync")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
