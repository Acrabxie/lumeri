from __future__ import annotations

import argparse
import runpy
import sys

import lumeri_script
from lumeri.legacy_names import LEGACY_IMPORT_NAMES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("script_path")
    args = parser.parse_args(argv)

    patch_output = sys.stdout
    sys.stdout = sys.stderr
    lumeri_script.configure_runtime(patch_output=patch_output, script_path=args.script_path)
    # Scripts written before the rename import the runtime by its old name.
    for old_name in LEGACY_IMPORT_NAMES:
        sys.modules.setdefault(old_name, lumeri_script)
    runpy.run_path(args.script_path, run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
