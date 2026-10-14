"""The settings file of the root: how duho's loader reads it here, and the command word before any option parses."""

from __future__ import annotations

import io as _io
import os as _os
import pathlib as _pathlib
import stat as _stat
import typing as _ty

from .._extras import PKTCAP_LINE, have_pktcap

__all__ = ["MAX_OCTETS", "command_word", "read_settings"]

#: The most octets read from a settings file.
MAX_OCTETS = 65536

#: The root's options that take the next word as their value.
_VALUED = ("--loglevel", "--config")

#: The commands whose tables need the ``pktcap`` extra.
_NEEDS_PKTCAP = ("capture", "replay")


def command_word(tokens: _ty.Sequence[str]) -> _ty.Optional[str]:
    """The command's name from the words before it: the first that is no option of the root and no value of one."""
    skip = False
    for word in tokens:
        if skip:
            skip = False
        elif word == "--":
            return None
        elif word.startswith("--"):
            option = word.split("=", 1)[0]
            skip = "=" not in word and any(len(option) > 2 and name.startswith(option) for name in _VALUED)
        elif word.startswith("-") and word != "-":
            skip = word[1:].lstrip("vq") == "c"
        else:
            return word
    return None


def _octets(path: _pathlib.Path, trusted_only: bool) -> bytes:
    """The file's octets, at most :data:`MAX_OCTETS`.

    The user's own file is checked on POSIX when ``trusted_only``: a regular file that is the user's or root's
    and that everyone may not write, since a run as root must not take its settings from another user's file.
    """
    descriptor = _os.open(str(path), _os.O_RDONLY | getattr(_os, "O_BINARY", 0))
    try:
        info = _os.fstat(descriptor)
        owner = getattr(_os, "geteuid", None)
        if trusted_only and owner is not None:
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


def read_settings(path: _pathlib.Path, source: str) -> _ty.Any:
    """The settings ``path`` holds, for duho's layout: top-level keys for the root, a table for each command.

    ``source`` is where duho got the path: ``"default"`` is the user's own file, which is checked first; any
    other is a file the user named, read as it is. Duho's ``_config_validate_`` refuses what nothing has, a
    ``config`` key among it. This refuses a table for a command whose extra is missing.

    :raises ValueError: a file that is not trusted or over :data:`MAX_OCTETS`, or a table as above, each with one
        line that names the file and holds no value of it. A document that is not valid is duho's
        ``ConfigError``, a ``ValueError`` whose message holds the position and never a value.
    """
    from duho.config import backend_for

    stream = _io.BytesIO(_octets(path, source == "default"))
    stream.name = str(path)  # the file is read once, here, and named for duho's backend
    data = backend_for(path).load(stream)
    if hasattr(data, "keys") and not have_pktcap():
        for name in _NEEDS_PKTCAP:
            if name in data:
                raise ValueError("%s: the table [%s]: %s" % (path, name, PKTCAP_LINE))
    return data
