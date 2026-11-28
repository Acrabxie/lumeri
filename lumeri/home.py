"""Where Lumeri keeps a person's files on this machine: ``~/.lumeri``.

They used to live in ``~/.gemia``. The release before this one read either
home and still wrote the old one; this one writes ``~/.lumeri`` and moves
whatever is left in ``~/.gemia`` across the first time it is used, one
top-level entry (``memory``, ``skills``, ...) at a time. An entry already
in ``~/.lumeri`` is never overwritten: the old one then stays where it was.

One entry changes its name as it moves. The engine's settings and keys were
``~/.gemia/config.json``, but ``~/.lumeri/config.json`` is already the desktop
app's and the CLI's older public config, so in ``~/.lumeri`` the engine's file
is :data:`CONFIG_FILE`. Ask for it by that name.
"""
from __future__ import annotations

import contextlib
import errno
import os
import shutil
from pathlib import Path

from .legacy_names import DATA_HOME, LEGACY_DATA_HOME

CONFIG_FILE = "engine-config.json"
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
    return home()


def _legacy_entry(name: str) -> Path:
    return legacy_home() / _LEGACY_ENTRY_NAMES.get(name, name)


def _has_legacy_entry(name: str) -> bool:
    """False for ``config.json``: ``~/.gemia/config.json`` is the engine's file, not that one."""
    return name in _LEGACY_ENTRY_NAMES or name not in _LEGACY_ENTRY_NAMES.values()


def move_from_legacy_home(name: str) -> Path:
    """Move the top-level entry *name* from ``~/.gemia`` to ``~/.lumeri``, and say where it is.

    Nothing moves when ``~/.lumeri`` already has it or ``~/.gemia`` does not;
    the answer is then ``~/.lumeri/<name>``. When the move fails, the entry
    stays whole in ``~/.gemia`` and the answer is that.
    """
    new, old = home() / name, _legacy_entry(name)
    if os.path.lexists(new) or not _has_legacy_entry(name) or not os.path.lexists(old):
        return new
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        os.rename(old, new)
    except FileNotFoundError:
        pass  # another process moved it first
    except OSError as exc:
        if exc.errno != errno.EXDEV or os.path.lexists(new):
            return new if os.path.lexists(new) else old
        return _copy_across(old, new)
    return new


def _copy_across(old: Path, new: Path) -> Path:
    """Move *old* to *new* on another file system, so *new* never holds half of it."""
    partial = new.with_name(f".{new.name}.moving")
    try:
        if partial.is_dir() and not partial.is_symlink():
            shutil.rmtree(partial)
        elif os.path.lexists(partial):
            partial.unlink()
        if old.is_dir() and not old.is_symlink():
            shutil.copytree(old, partial, symlinks=True)
        else:
            shutil.copy2(old, partial, follow_symlinks=False)
        os.rename(partial, new)
    except OSError:
        return old
    with contextlib.suppress(OSError):  # if it stays, both hold it; ~/.lumeri wins from here on
        shutil.rmtree(old) if old.is_dir() and not old.is_symlink() else old.unlink()
    return new


def data_path(*parts: str) -> Path:
    """Where the file or folder ``~/.lumeri/<parts>`` is, or goes.

    The first part is moved from ``~/.gemia`` if it is still there, so a
    folder such as ``memory`` is never split between the two homes.
    """
    if not parts:
        raise ValueError("data_path needs at least one part")
    return move_from_legacy_home(parts[0]).joinpath(*parts[1:])


def data_paths(*parts: str) -> tuple[Path, Path]:
    """``<home>/<parts>`` in both homes, ``~/.lumeri`` first."""
    if not parts:
        raise ValueError("data_paths needs at least one part")
    return home().joinpath(*parts), _legacy_entry(parts[0]).joinpath(*parts[1:])


__all__ = ["CONFIG_FILE", "data_home", "data_path", "data_paths", "home", "homes", "legacy_home", "move_from_legacy_home"]
