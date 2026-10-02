"""Directory listings for the ``x-list`` extension (pytftp's own, not an RFC).

TFTP has no way to list a directory. A client that asks for one sends an
RRQ for the directory with ``x-list=1``; a server that agrees answers with
``x-list=1`` in its OACK and transfers a listing instead of a file. A server
that does not know the option ignores it (RFC 2347), so a directory then
reads as "file not found", and a file is transferred without the option
acknowledged -- which is how a client tells it was not a directory.

The listing is UTF-8 text, one entry per line::

    <type> <size> <mtime> <name>

``type`` is ``f`` (file) or ``d`` (directory); ``size`` is in bytes (0 for a
directory); ``mtime`` is whole seconds since the epoch, or ``-`` when
unknown; ``name`` runs to the end of the line, with ``%``, CR and LF written
``%25``, ``%0D`` and ``%0A``.
"""

from __future__ import annotations

import io
import os
import re
from typing import Iterable, List, NamedTuple, Optional

__all__ = ["ListEntry", "format_listing", "parse_listing", "DirectoryListing", "LIST_OPTION", "MTIME_OPTION"]

#: The option asking for a listing; its value is the format version, ``1``.
LIST_OPTION = "x-list"
#: The option asking for (RRQ) a file's modification time, in its OACK.
MTIME_OPTION = "x-mtime"

_ESCAPES = (("%", "%25"), ("\r", "%0D"), ("\n", "%0A"))


class ListEntry(NamedTuple):
    """One directory entry of an ``x-list`` listing."""

    name: str
    is_dir: bool
    size: int
    mtime: Optional[int] = None


def _escape(name: str) -> str:
    for char, code in _ESCAPES:
        name = name.replace(char, code)
    return name


_CODES = re.compile("%(25|0[dD]|0[aA])")
_DECODED = {"25": "%", "0d": "\r", "0a": "\n"}


def _unescape(name: str) -> str:
    return _CODES.sub(lambda match: _DECODED[match.group(1).lower()], name)


def format_listing(entries: Iterable[ListEntry]) -> bytes:
    """The wire form of ``entries``."""
    lines = []
    for entry in entries:
        mtime = "-" if entry.mtime is None else str(int(entry.mtime))
        lines.append(
            "%s %d %s %s\n"
            % ("d" if entry.is_dir else "f", 0 if entry.is_dir else entry.size, mtime, _escape(entry.name))
        )
    return "".join(lines).encode("utf-8", "surrogateescape")


def parse_listing(data: bytes) -> List[ListEntry]:
    """Entries from a listing; malformed lines are skipped."""
    entries = []
    for line in data.decode("utf-8", "surrogateescape").split("\n"):
        fields = line.split(" ", 3)
        if len(fields) != 4 or fields[0] not in ("f", "d") or not fields[3]:
            continue
        kind, size, mtime, name = fields
        try:
            entries.append(
                ListEntry(_unescape(name), kind == "d", int(size), None if mtime == "-" else int(mtime))
            )
        except ValueError:
            continue
    return entries


class DirectoryListing(io.BytesIO):
    """A readable listing of ``directory``, as a handler returns it from ``open_read``.

    The server acknowledges ``x-list`` only for a stream marked this way
    (``_tftp_listing_``). Entries whose resolved path leaves ``root``
    (symlinks pointing outside what is served) are left out, as are
    in-progress uploads (``.name.*.part``). ``size`` and ``mtime`` let the
    server answer ``tsize`` and ``x-mtime``.
    """

    _tftp_listing_ = True

    def __init__(self, directory: str, root: Optional[str] = None) -> None:
        root = os.path.realpath(root or directory)
        entries = []
        with os.scandir(directory) as scan:
            for entry in scan:
                name = entry.name
                if name.startswith(".") and name.endswith(".part"):
                    continue
                try:
                    if entry.is_symlink():
                        real = os.path.realpath(entry.path)
                        if os.path.commonpath([real, root]) != root:
                            continue
                    info = entry.stat()
                    is_dir = entry.is_dir()
                except (OSError, ValueError):
                    continue
                entries.append(ListEntry(name, is_dir, info.st_size, int(info.st_mtime)))
        entries.sort(key=lambda e: e.name)
        data = format_listing(entries)
        super().__init__(data)
        self.size = len(data)
        self.mtime: Optional[int] = int(os.stat(directory).st_mtime)
