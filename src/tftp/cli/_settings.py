"""The settings file of the root: where the user's own is, how it is read and what it may hold."""

from __future__ import annotations

import collections.abc as _abc
import io as _io
import os as _os
import pathlib as _pathlib
import stat as _stat
import typing as _ty

from .._extras import PKTCAP_LINE
from ._missing import MissingCapture, MissingReplay

__all__ = [
    "MAX_OCTETS",
    "choose_default",
    "default_path",
    "forget",
    "read_settings",
    "scan_root_options",
]

#: The most octets read from a settings file.
MAX_OCTETS = 65536

#: The root's options that take the next word as their value, and the one that names the file.
_VALUED = ("--loglevel", "--config")


def default_path() -> _ty.Optional[_pathlib.Path]:
    """``pytftp/pytftp.ini`` under ``%APPDATA%`` or ``$XDG_CONFIG_HOME``, else the home's own place.

    A relative variable is ignored, so neither can point into the working directory. ``None`` where the
    platform gives no home.
    """
    nt = _os.name == "nt"
    base = _os.environ.get("APPDATA" if nt else "XDG_CONFIG_HOME", "")
    if not _os.path.isabs(base):
        home = _os.path.expanduser("~")
        if home == "~":
            return None
        base = _os.path.join(home, "AppData", "Roaming") if nt else _os.path.join(home, ".config")
    return _pathlib.Path(base) / "pytftp" / "pytftp.ini"


def scan_root_options(tokens: _ty.Sequence[str]) -> _ty.Tuple[bool, _ty.Optional[str]]:
    """``(whether the file is named, the command's name)`` from the words before the command.

    The file is named by ``-c`` or ``--config`` (or an abbreviation of it, joined or not), or by
    ``PYTFTP_CONFIG``. The command is the first word that is no option of the root and no value of one.
    """
    named = bool(_os.environ.get("PYTFTP_CONFIG", "").strip())
    skip = False
    for word in tokens:
        if skip:
            skip = False
        elif word == "--":
            return named, None
        elif word.startswith("--"):
            option = word.split("=", 1)[0]
            valued = [name for name in _VALUED if len(option) > 2 and name.startswith(option)]
            if valued and valued[0] == "--config":
                named = True
            skip = bool(valued) and "=" not in word
        elif word.startswith("-") and word != "-":
            letters = word[1:].lstrip("vq")
            if letters.startswith("c"):
                named = True
                skip = letters == "c"
        else:
            return named, word
    return named, None


#: What this run set as the root's default file.
_state: _ty.Dict[str, _ty.Optional[str]] = {"default": None}


def choose_default(tokens: _ty.Sequence[str]) -> _ty.Optional[str]:
    """The user's own file as the root's default for this run; ``None`` when the user names one.

    Remembered, so that the loader knows which file to check.
    """
    _state["default"] = None
    named, _ = scan_root_options(tokens)
    path = None if named else default_path()
    if path is not None:
        _state["default"] = str(path)
    return _state["default"]


def forget() -> None:
    """End the run's state: no default file."""
    _state["default"] = None


def _octets(path: _pathlib.Path, named: bool) -> _ty.Optional[bytes]:
    """The file's octets; ``None`` when the default file is absent.

    The default file is checked on POSIX: a regular file that is the user's or root's and that everyone may
    not write, since a run as root must not take its settings from another user's file. A file the user
    named is read as it is.
    """
    flags = _os.O_RDONLY | getattr(_os, "O_BINARY", 0)
    try:
        descriptor = _os.open(str(path), flags)
    except (FileNotFoundError, NotADirectoryError):
        if named:
            raise
        return None
    try:
        info = _os.fstat(descriptor)
        owner = getattr(_os, "geteuid", None)
        if not named and owner is not None:
            problem = None
            if not _stat.S_ISREG(info.st_mode):
                problem = "it is not a regular file"
            elif info.st_uid not in (owner(), 0):
                problem = "another user owns it"
            elif info.st_mode & _stat.S_IWOTH:
                problem = "everyone may write it"
            if problem:
                raise ValueError("%s: not read: %s (name it with --config to read it)" % (path, problem))
        chunks: _ty.List[bytes] = []
        total = 0
        while total <= MAX_OCTETS:
            chunk = _os.read(descriptor, MAX_OCTETS + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    finally:
        _os.close(descriptor)
    if total > MAX_OCTETS:
        raise ValueError("%s: over %d octets" % (path, MAX_OCTETS))
    return b"".join(chunks)


def _document(octets: bytes, name: str) -> _io.BytesIO:
    """Octets read once, named, for ``duho.config``: the file is opened and checked here, not again there."""
    stream = _io.BytesIO(octets)
    stream.name = name
    return stream


def _shown(text: object) -> str:
    """``text`` from a file as one short quoted line: bounded, its control characters escaped."""
    shown = str(text)
    return repr(shown if len(shown) <= 40 else shown[:40] + "...")


def _fields(command: _ty.Any) -> _ty.Set[str]:
    return {field.name for field in command._getargs_()}


def _checked(data: _ty.Any, name: str, root: _ty.Any) -> _ty.Dict[str, _ty.Any]:
    """``data`` when every key is an option of the root or a table of a command that has it."""
    if not isinstance(data, _abc.Mapping):
        raise ValueError("%s: the top level must be a table" % name)
    commands = {command._parsername_: command for command in root._subcommands_}
    known = _fields(root) - {"config"}
    for key, value in data.items():
        command = commands.get(key)
        if command is None:
            if key not in known:
                raise ValueError(
                    "%s: unknown %s %s"
                    % (name, "table" if isinstance(value, _abc.Mapping) else "key", _shown(key))
                )
            continue
        if not isinstance(value, _abc.Mapping):
            raise ValueError("%s: %s must be a table" % (name, _shown(key)))
        if command in (MissingCapture, MissingReplay):
            raise ValueError("%s: the table [%s]: %s" % (name, key, PKTCAP_LINE))
        unknown = [item for item in value if item not in _fields(command)]
        if unknown:
            raise ValueError("%s: unknown key %s in [%s]" % (name, _shown(unknown[0]), key))
    return dict(data)


def read_settings(path: _pathlib.Path, root: _ty.Any) -> _ty.Dict[str, _ty.Any]:
    """The settings ``path`` holds, in duho's layout: top-level keys for ``root``, a table for each command.

    :raises ValueError: a key or table nobody has, a table of a command whose extra is missing, a file
        that is not trusted or over :data:`MAX_OCTETS`, each with one line that names the file and holds no text
        of it. A document that is not valid is duho's ``ConfigError``, a ``ValueError`` whose message holds the
        position and never the text.
    """
    from duho.config import backend_for

    octets = _octets(path, str(path) != _state["default"])
    if octets is None:
        return {}
    backend = backend_for(path)
    return _checked(backend.load(_document(octets, str(path))), str(path), root)
