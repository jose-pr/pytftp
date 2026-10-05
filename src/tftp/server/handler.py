"""Server request handlers: what a request maps to.

A handler is any object with ``open_read(context)`` and
``open_write(context, size)``. :class:`~tftp.backends.FilesystemBackend` serves a
directory; subclass it, or write a handler from scratch, to serve generated
content (a per-client boot menu, a firmware image chosen by MAC) or to route
uploads somewhere other than a disk.
"""

from __future__ import annotations

import copy
import os
import tempfile
from typing import Any, BinaryIO, Optional, Tuple

from ..exceptions import TFTPError
from ..packet import TFTPErrorCode, RequestPacket

try:  # Python 3.8+: typing.Protocol
    from typing import Protocol
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore[assignment,misc]

__all__ = ["TFTPHandler", "TFTPRequestContext", "AtomicWriter"]


class TFTPRequestContext:
    """Everything known about a request when a handler is asked to open it.

    :ivar request: the parsed RRQ/WRQ (``filename``, ``mode``, ``options``).
    :ivar peer: the client's ``(host, port, ...)``.
    :ivar local_address: the address the request was sent to, when the
        platform reports it (see ``TFTPServer.supports_pktinfo``), else ``None``.
    :ivar interface_index: the interface it arrived on, or ``0``.
    :ivar interface: that interface as a ``netimps.Interface`` (name,
        addresses, MTU), or ``None`` when unknown.
    :ivar listing: an RRQ asking for a directory listing (``x-list``) that
        the server's policy allows; a handler may then answer a directory
        with a :class:`tftp.listing.DirectoryListing`.
    """

    __slots__ = ("request", "peer", "local_address", "interface_index", "interface", "listing")

    def __init__(
        self,
        request: RequestPacket,
        peer: Tuple[Any, ...],
        local_address: Optional[str] = None,
        interface_index: int = 0,
    ) -> None:
        self.request = request
        self.peer = peer
        self.local_address = local_address
        self.interface_index = interface_index
        self.interface: Any = None
        self.listing = False

    def with_filename(self, filename: str) -> "TFTPRequestContext":
        """A copy of this context for a request naming ``filename`` instead.

        Everything else is carried over (the listing flag, the interface), so
        a wrapper that rewrites names does not have to know the fields.
        """
        clone = copy.copy(self)
        clone.request = self.request._replace(filename=filename, raw=b"")
        return clone

    @property
    def filename(self) -> str:
        return self.request.filename

    @property
    def mode(self) -> str:
        return self.request.mode

    @property
    def options(self) -> dict:
        return self.request.options

    def __repr__(self) -> str:
        return "TFTPRequestContext(%s %r from %s to %s)" % (
            "RRQ" if self.request.is_read else "WRQ",
            self.filename,
            self.peer[:2],
            self.local_address,
        )


class TFTPHandler(Protocol):
    """What a server needs from a handler.

    ``open_read`` returns a binary reader (``readinto`` or ``read``, plus
    ``close``). A reader exposing an integer ``size`` attribute, or a real
    file descriptor, lets the server answer ``tsize``.

    ``open_write`` returns a binary writer (``write`` and ``close``).
    ``close`` is called once the last block is written and before it is
    acknowledged, so a failing ``close`` reaches the client as an ERROR. If
    the writer has ``abort``, a failed transfer calls that instead of
    ``close``. ``size`` is the client's announced ``tsize``, or ``None``.

    Either may raise :class:`TFTPError` to refuse with a specific code, or
    ``OSError``, which is mapped by errno (``ENOENT`` -> file not found,
    ``EACCES`` -> access violation, ``ENOSPC`` -> disk full...).

    Both run on the server's event loop thread, so they should not block for
    long.
    """

    def open_read(self, context: TFTPRequestContext) -> BinaryIO: ...

    def open_write(self, context: TFTPRequestContext, size: Optional[int]) -> Any: ...


class AtomicWriter:
    """Writes to a temporary file beside ``path`` and renames it on ``close``.

    A partial upload therefore never replaces or appears as ``path``;
    :meth:`abort` deletes the temporary file.
    """

    _tftp_copies_ = True  # write() copies its argument (see as_write)

    def __init__(self, path: str, overwrite: bool = True) -> None:
        self.path = path
        self.overwrite = overwrite
        directory, name = os.path.split(path)
        fd, self._tmp = tempfile.mkstemp(prefix=".%s." % name, suffix=".part", dir=directory or ".")
        self._file = os.fdopen(fd, "wb")
        self.closed = False

    def write(self, data) -> int:
        return self._file.write(data)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self._file.close()
            if not self.overwrite and os.path.exists(self.path):
                raise TFTPError(TFTPErrorCode.FILE_EXISTS)
            os.replace(self._tmp, self.path)
        except BaseException:
            self._discard()
            raise

    def abort(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self._file.close()
        finally:
            self._discard()

    def _discard(self) -> None:
        try:
            os.unlink(self._tmp)
        except OSError:
            pass
