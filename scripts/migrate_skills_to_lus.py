#!/usr/bin/env python3
"""One-shot migrator: legacy distilled-skill JSONs → .lus.

Implements docs/lus-skill-format.md §8.3.  Also exposed as
``python -m gemia migrate-skills``; the logic lives in
:func:`gemia.skill_store.migrate_distilled_to_lus` so both entry points and
the tests share one implementation.

Usage::

    python scripts/migrate_skills_to_lus.py [--dry-run] [--root DIR]

Default root is ``distilled_skills_dir()`` (``~/.gemia/skills`` or the
``GEMIA_SKILL_STORE_DIR`` override).  Originals become ``*.json.bak`` (never
deleted); running twice is a byte-identical no-op; skills whose content
violates the format (secrets, absolute paths) are skipped, reported, and left
untouched for manual review.
"""
from __future__ import annotations

import argparse
import sys


def format_report(report: list[dict]) -> str:
    if not report:
        return "No legacy skill JSONs found; nothing to migrate."
    lines = []
    for entry in report:
        line = f"{entry['file']}: {entry['status']}"
        if entry.get("detail"):
            line += f" — {entry['detail']}"
        lines.append(line)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate legacy distilled skill JSONs to .lus "
                    "(docs/lus-skill-format.md §8).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the migration plan without writing anything")
    parser.add_argument("--root", default=None,
                        help="Skills root to migrate (default: the distilled "
                             "skill store, honoring GEMIA_SKILL_STORE_DIR)")
    args = parser.parse_args(argv)

    from gemia.skill_store import migrate_distilled_to_lus

    report = migrate_distilled_to_lus(args.root, dry_run=args.dry_run)
    print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
