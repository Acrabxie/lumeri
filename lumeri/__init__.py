"""Lumeri engine package."""
from .legacy_names import apply_legacy_env as _apply_legacy_env

# Settings written before the rename (GEMIA_*, LUMERAI_*) still apply.
_apply_legacy_env()
