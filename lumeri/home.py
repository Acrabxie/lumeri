"""Where Lumeri keeps a person's files on this machine.

They used to live in ``~/.gemia``; they are moving to ``~/.lumeri``, beside
the ``config.toml`` and ``lucs`` folder already there (see
:mod:`lumeri.legacy_names` for the two steps). Each top-level entry, such as
``config.json`` or ``memory``, is found in whichever home holds it,
``~/.lumeri`` first. An entry in neither goes where this release writes new
files: still ``~/.gemia``, so the release before this one finds it.
"""
from __future__ import annotations

from pathlib import Path

from .legacy_names import DATA_HOME, LEGACY_DATA_HOME, WRITES_NEW_NAMES


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


def data_path(*parts: str) -> Path:
    """Where the file or folder ``<home>/<parts>`` is, or goes.

    The home is chosen by the first part alone, so a folder such as
    ``memory`` is never split between the two.
    """
    if not parts:
        raise ValueError("data_path needs at least one part")
    for root in homes():
        if (root / parts[0]).exists():
            return root.joinpath(*parts)
    return data_home().joinpath(*parts)


def data_paths(*parts: str) -> tuple[Path, Path]:
    """``<home>/<parts>`` in both homes, ``~/.lumeri`` first."""
    return tuple(root.joinpath(*parts) for root in homes())  # type: ignore[return-value]


__all__ = ["data_home", "data_path", "data_paths", "home", "homes", "legacy_home"]
