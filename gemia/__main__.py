"""``python -m gemia``: runs ``python -m lumeri`` with the same arguments."""
import runpy
import sys

print("python -m gemia is now python -m lumeri; the old name stops working in the next release.", file=sys.stderr)
runpy.run_module("lumeri", run_name="__main__", alter_sys=True)
