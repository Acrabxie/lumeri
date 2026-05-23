#!/usr/bin/env python3
"""Sync the source Creative Runtime panel asset into the served web bundle."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tauri-app" / "src" / "assets" / "creative-runtime-ui.js"
TARGET = ROOT / "tauri-app" / "dist" / "assets" / "creative-runtime-ui.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy tauri-app/src/assets/creative-runtime-ui.js into tauri-app/dist/assets/."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify that source and served dist asset are identical without writing",
    )
    args = parser.parse_args()

    if not SOURCE.exists():
        parser.error(f"missing source asset: {SOURCE}")
    if args.check:
        if not TARGET.exists():
            print(f"missing served asset: {TARGET}", file=sys.stderr)
            return 1
        if _read(SOURCE) != _read(TARGET):
            print(
                "creative-runtime-ui.js is stale; run scripts/sync_creative_runtime_asset.py",
                file=sys.stderr,
            )
            return 1
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(_read(SOURCE), encoding="utf-8")
    print(f"synced {SOURCE.relative_to(ROOT)} -> {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
