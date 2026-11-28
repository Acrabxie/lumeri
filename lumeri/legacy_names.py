"""Old names that Lumeri still accepts when it reads.

The engine package used to be called ``gemia`` and the script runtime
``lumerai``. Code now says ``lumeri`` and ``lumeri_script``. This module is
the one place that still knows the old spellings, so settings, scripts and
saved data written before the rename keep working.

Old names are only mapped to current ones on the way in. The names in
:data:`KEPT_NAMES` stayed in their old spelling for one more release, because
files, projects and exports held them and the release before had to read
them back. Lumeri now writes them in their new spelling, and turns an old one
into the new one where it reads it.
"""
from __future__ import annotations

import os
from collections.abc import MutableMapping

LEGACY_ENV_PREFIXES: tuple[str, ...] = ("GEMIA_", "LUMERAI_")
CURRENT_ENV_PREFIX = "LUMERI_"

# Python import roots used by scripts written before the rename.
LEGACY_IMPORT_NAMES: dict[str, str] = {"lumerai": "lumeri_script"}

# URI schemes found in MCP resource URIs.
LEGACY_URI_SCHEMES: dict[str, str] = {"gemia": "lumeri"}

# Dotted primitive ids found in saved or cached plans.
LEGACY_REGISTRY_PREFIXES: dict[str, str] = {"gemia.": "lumeri."}

# Names files written before this release may hold, old spelling to new.
KEPT_NAMES: dict[str, str] = {
    # A project file's "schema".
    "gemia.project": "lumeri.project",
    # A project's "generator", the scheme of a saved media reference
    # (gemia://media/...) and the prefix of USD and OpenXR identifiers.
    "gemia": "lumeri",
    # The title of a session nobody has named yet.
    "Gemia Session": "Lumeri Session",
    # The script engine, in saved plans and in what a script made.
    "lumerai_script": "lumeri_script",
    # Where a media asset's identity came from.
    "gemia_media_library": "lumeri_media_library",
    # OpenTimelineIO package metadata keys and the default timeline name.
    "gemia_package": "lumeri_package",
    "gemia_clip_id": "lumeri_clip_id",
    "gemia_timeline_package": "lumeri_timeline_package",
    # The default USD stage id.
    "gemia_stage": "lumeri_stage",
    # The OpenXR delivery profile and its action set.
    "gemia_openxr_spatial_delivery": "lumeri_openxr_spatial_delivery",
    "gemia_spatial_review": "lumeri_spatial_review",
    "Gemia Spatial Review": "Lumeri Spatial Review",
}
_OLD_SPELLING: dict[str, str] = {new: old for old, new in KEPT_NAMES.items()}

# The folder in the home directory that held a person's files before
# ``~/.lumeri``; :mod:`lumeri.home` moves what is left there on first use.
LEGACY_DATA_HOME = ".gemia"
DATA_HOME = ".lumeri"


def apply_legacy_env(environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Copy ``GEMIA_*`` / ``LUMERAI_*`` settings to ``LUMERI_*``.

    A ``LUMERI_*`` value that is set and not empty always wins, as the old
    ``LUMERI_X or GEMIA_X`` fallbacks did. Returns the names that were
    filled in.
    """
    env = os.environ if environ is None else environ
    filled: list[str] = []
    for key in list(env):
        for prefix in LEGACY_ENV_PREFIXES:
            if key.startswith(prefix) and len(key) > len(prefix):
                current = CURRENT_ENV_PREFIX + key[len(prefix):]
                if not env.get(current) and current not in filled:
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


def current(value: str) -> str:
    """*value* in its new spelling, if it is a kept name; otherwise *value*."""
    return KEPT_NAMES.get(value, value) if isinstance(value, str) else value


def spellings(name: str) -> tuple[str, str]:
    """The new and the old spelling of the kept name *name* (given new), for a reader that looks up both."""
    return name, _OLD_SPELLING[name]


def current_prefixed(value: str, name: str, separator: str) -> str:
    """*value* with its kept-name prefix (``name`` + *separator*) in the new spelling.

    For identifiers such as ``gemia://media/...`` or ``gemia:usd_stage:...``.
    Anything that does not start with either spelling comes back unchanged.
    """
    if not isinstance(value, str):
        return value
    for spelling in spellings(name):
        head = spelling + separator
        if value.startswith(head):
            return name + separator + value[len(head):]
    return value


__all__ = [
    "CURRENT_ENV_PREFIX",
    "DATA_HOME",
    "KEPT_NAMES",
    "LEGACY_DATA_HOME",
    "LEGACY_ENV_PREFIXES",
    "LEGACY_IMPORT_NAMES",
    "LEGACY_REGISTRY_PREFIXES",
    "LEGACY_URI_SCHEMES",
    "apply_legacy_env",
    "current",
    "current_import_name",
    "current_prefixed",
    "current_registry_id",
    "current_uri",
    "spellings",
]
