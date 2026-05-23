from __future__ import annotations

import argparse
import runpy
import sys

import lumerai


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("script_path")
    args = parser.parse_args(argv)

    patch_output = sys.stdout
    sys.stdout = sys.stderr
    lumerai.configure_runtime(patch_output=patch_output, script_path=args.script_path)
    runpy.run_path(args.script_path, run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
