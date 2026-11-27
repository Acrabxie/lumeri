"""Where Lumeri keeps a person's files on this machine.

They used to live in ``~/.gemia``; they are moving to ``~/.lumeri``, beside
the ``config.toml`` and ``lucs`` folder already there (see
:mod:`lumeri.legacy_names` for the two steps). Each top-level entry, such as
``memory``, is found in whichever home holds it, ``~/.lumeri`` first. An
entry in neither goes where this release writes new files: still
``~/.gemia``, so the release before this one finds it.

One entry changes its name as it moves. The engine's settings and keys were
``~/.gemia/config.json``, but ``~/.lumeri/config.json`` is already the desktop
app's and the CLI's older public config, so in ``~/.lumeri`` the engine's file
is :data:`CONFIG_FILE`. Ask for it by that name in either home.
"""
from __future__ import annotations

from pathlib import Path

from .legacy_names import DATA_HOME, LEGACY_DATA_HOME, WRITES_NEW_NAMES

#: The engine's settings and API keys, in ``~/.lumeri``.
CONFIG_FILE = "engine-config.json"

# A top-level entry whose name in ~/.gemia differs from its name in ~/.lumeri.
_LEGACY_ENTRY_NAMES = {CONFIG_FILE: "config.json"}


def home() -> Path:
    """``~/.lumeri``."""
    return Path.home() / DATA_HOME


def legacy_home() -> Path:
    """``~/.gemia``, where a person's files were before ``~/.lumeri``."""
    return Path.home() / LEGACY_DATA_HOME


def homes() -> tuple[Path, Path]:
    """Both homes, ``~/.lumeri`` first: for anything that has to cover both, such as a denylist."""
    return home(), legacy_home()


def data_home() -> Path:
    """The home a new top-level entry goes in."""
    return home() if WRITES_NEW_NAMES else legacy_home()


def _legacy_entry(name: str) -> Path:
    """The top-level entry *name* as ``~/.gemia`` holds it."""
    return legacy_home() / _LEGACY_ENTRY_NAMES.get(name, name)


def data_path(*parts: str) -> Path:
    """Where the file or folder ``<home>/<parts>`` is, or goes.

    The home is chosen by the first part alone, so a folder such as
    ``memory`` is never split between the two.
    """
    if not parts:
        raise ValueError("data_path needs at least one part")
    if (home() / parts[0]).exists():
        return home().joinpath(*parts)
    old = _legacy_entry(parts[0])
    if old.exists() or not WRITES_NEW_NAMES:
        return old.joinpath(*parts[1:])
    return home().joinpath(*parts)


def data_paths(*parts: str) -> tuple[Path, Path]:
    """``<parts>`` in both homes, ``~/.lumeri`` first."""
    if not parts:
        raise ValueError("data_paths needs at least one part")
    return home().joinpath(*parts), _legacy_entry(parts[0]).joinpath(*parts[1:])


__all__ = ["CONFIG_FILE", "data_home", "data_path", "data_paths", "home", "homes", "legacy_home"]
