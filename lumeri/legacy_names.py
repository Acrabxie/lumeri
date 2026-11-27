"""Old names that Lumeri still accepts when it reads.

The engine package used to be called ``gemia`` and the script runtime
``lumerai``. Code now says ``lumeri`` and ``lumeri_script``. This module is
the one place that still knows the old spellings, so settings, scripts and
saved data written before the rename keep working. Nothing here writes an
old name; it only maps old names to current ones on the way in.
"""
from __future__ import annotations

import os
from collections.abc import MutableMapping

LEGACY_ENV_PREFIXES: tuple[str, ...] = ("GEMIA_", "LUMERAI_")
CURRENT_ENV_PREFIX = "LUMERI_"

# Python import roots used by scripts written before the rename.
LEGACY_IMPORT_NAMES: dict[str, str] = {"lumerai": "lumeri_script"}

# URI schemes found in saved entity references and MCP resource URIs.
LEGACY_URI_SCHEMES: dict[str, str] = {"gemia": "lumeri"}

# Dotted primitive ids found in saved or cached plans.
LEGACY_REGISTRY_PREFIXES: dict[str, str] = {"gemia.": "lumeri."}


def apply_legacy_env(environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Copy ``GEMIA_*`` / ``LUMERAI_*`` settings to ``LUMERI_*``.

    A ``LUMERI_*`` value that is already set always wins. Returns the names
    that were filled in.
    """
    env = os.environ if environ is None else environ
    filled: list[str] = []
    for key in list(env):
        for prefix in LEGACY_ENV_PREFIXES:
            if key.startswith(prefix) and len(key) > len(prefix):
                current = CURRENT_ENV_PREFIX + key[len(prefix):]
                if current not in env:
                    env[current] = env[key]
                    filled.append(current)
                break
    return filled


def current_import_name(module: str) -> str:
    """Map an old import path (``lumerai.x``) to the current one."""
    root, sep, rest = (module or "").partition(".")
    current = LEGACY_IMPORT_NAMES.get(root)
    if current is None:
        return module
    return f"{current}{sep}{rest}"


def current_uri(uri: str) -> str:
    """Map an old URI scheme (``gemia://...``) to the current one."""
    scheme, sep, rest = (uri or "").partition("://")
    if not sep:
        return uri
    current = LEGACY_URI_SCHEMES.get(scheme.lower())
    if current is None:
        return uri
    return f"{current}://{rest}"


def current_registry_id(fqn: str) -> str:
    """Map an old dotted primitive id (``gemia.video...``) to the current one."""
    value = fqn or ""
    for old, new in LEGACY_REGISTRY_PREFIXES.items():
        if value.startswith(old):
            return new + value[len(old):]
    return fqn


__all__ = [
    "CURRENT_ENV_PREFIX",
    "LEGACY_ENV_PREFIXES",
    "LEGACY_IMPORT_NAMES",
    "LEGACY_REGISTRY_PREFIXES",
    "LEGACY_URI_SCHEMES",
    "apply_legacy_env",
    "current_import_name",
    "current_registry_id",
    "current_uri",
]
