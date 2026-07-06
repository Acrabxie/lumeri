#!/usr/bin/env python3
"""Render gemia/v3_contract.py to the JSON copies both frontends test against.

Writes:
- static/v3/contract.json                      (web, served next to v3.js)
- ~/Code/lumeri-cli/src/contract.json          (CLI vendored copy, if the
                                                repo exists on this machine)

Run after ANY change to gemia/v3_contract.py; tests/test_v3_contract.py reds
when the JSON copies are stale.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from gemia.v3_contract import as_dict  # noqa: E402

CLI_REPO = Path.home() / "Code" / "lumeri-cli"


def main() -> int:
    payload = json.dumps(as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    web_copy = REPO / "static" / "v3" / "contract.json"
    web_copy.write_text(payload, encoding="utf-8")
    print(f"wrote {web_copy}")

    if CLI_REPO.is_dir():
        cli_copy = CLI_REPO / "src" / "contract.json"
        cli_copy.write_text(payload, encoding="utf-8")
        print(f"wrote {cli_copy}")
    else:
        print(f"CLI repo not found at {CLI_REPO} — skipped vendored copy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
