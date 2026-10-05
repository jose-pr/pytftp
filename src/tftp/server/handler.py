"""Server request handlers: what a request maps to.

A handler is any object with ``open_read(context)`` and
``open_write(context, size)``. :class:`FileSystemHandler` serves a directory;
subclass it, or write a handler from scratch, to serve generated content
(a per-client boot menu, a firmware image chosen by MAC) or to route uploads
somewhere other than a disk.
"""

from __future__ import annotations

import copy
import os
import shutil
import sys
import tempfile
from typing import Any, BinaryIO, Optional, Tuple

from ..exceptions import TFTPError
from ..packet import ErrorCode, Request

try:  # Python 3.8+: typing.Protocol
    from typing import Protocol
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore[assignment,misc]

__all__ = ["Handler", "RequestContext", "FileSystemHandler", "AtomicWriter"]

_WINDOWS = sys.platform == "win32"
#: Read buffer of a served file: an open file costs this much until it ends,
#: and a request nothing follows up is held open for a while.
_READ_BUFFER = 16 * 1024
_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"] + ["COM%d" % i for i in range(1, 10)] + ["LPT%d" % i for i in range(1, 10)]
)


class RequestContext:
    """Everything known about a request when a handler is asked to open it.

    :ivar request: the parsed RRQ/WRQ (``filename``, ``mode``, ``options``).
    :ivar peer: the client's ``(host, port, ...)``.
    :ivar local_address: the address the request was sent to, when the
        platform reports it (see ``Server.supports_pktinfo``), else ``None``.
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
        request: Request,
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

    def with_filename(self, filename: str) -> "RequestContext":
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
        return "RequestContext(%s %r from %s to %s)" % (
            "RRQ" if self.request.is_read else "WRQ",
            self.filename,
            self.peer[:2],
            self.local_address,
        )


class Handler(Protocol):
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

    def open_read(self, context: RequestContext) -> BinaryIO: ...

    def open_write(self, context: RequestContext, size: Optional[int]) -> Any: ...


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
                raise TFTPError(ErrorCode.FILE_EXISTS)
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


class FileSystemHandler:
    """Serves the files under ``root``.

    :param root: the directory served. Requests can never reach outside it:
        ``..`` components are refused, and the resolved path (symlinks
        included) must still be inside ``root``.
    :param writable: accept WRQ at all.
    :param create: a WRQ may create a file that does not exist.
    :param overwrite: a WRQ may replace a file that exists.
    :param backslash: treat ``\\`` in filenames as a separator, as Windows
        boot loaders send them (``\\boot\\bcd``).
    :param max_upload: refuse uploads announced (``tsize``) or grown larger
        than this many bytes, with ERROR 3.

    Uploads go through :class:`AtomicWriter`, so a failed or partial upload
    leaves no trace, and directories are never created.
    """

    _tftp_fast_open_ = True  # local files: the server opens them inline

    def __init__(
        self,
        root: "str | os.PathLike[str]",
        *,
        writable: bool = False,
        create: bool = True,
        overwrite: bool = False,
        backslash: bool = True,
        max_upload: Optional[int] = None,
    ) -> None:
        self.root = os.path.realpath(os.fspath(root))
        if not os.path.isdir(self.root):
            raise NotADirectoryError(self.root)
        self.writable = writable
        self.create = create
        self.overwrite = overwrite
        self.backslash = backslash
        self.max_upload = max_upload

    def resolve(self, filename: str) -> str:
        """The local path for ``filename``, or :class:`TFTPError` (2) if it escapes."""
        name = filename.replace("\\", "/") if self.backslash else filename
        parts = []
        for part in name.split("/"):
            if part in ("", "."):
                continue
            if part == ".." or "\0" in part:
                raise TFTPError(ErrorCode.ACCESS_VIOLATION)
            if _WINDOWS:
                if ":" in part or part.split(".", 1)[0].upper() in _RESERVED:
                    raise TFTPError(ErrorCode.ACCESS_VIOLATION)
            parts.append(part)
        if not parts:
            raise TFTPError(ErrorCode.FILE_NOT_FOUND)
        path = os.path.join(self.root, *parts)
        real = os.path.realpath(path)
        try:
            inside = os.path.commonpath([real, self.root]) == self.root
        except ValueError:  # different drives on Windows
            inside = False
        if not inside:
            raise TFTPError(ErrorCode.ACCESS_VIOLATION)
        return real

    def open_read(self, context: RequestContext) -> BinaryIO:
        if context.listing and _is_root(context.filename, self.backslash):
            path = self.root
        else:
            path = self.resolve(context.filename)
        if context.listing and os.path.isdir(path):
            from ..listing import DirectoryListing

            return DirectoryListing(path, self.root)  # type: ignore[return-value]
        if not os.path.isfile(path):
            raise TFTPError(ErrorCode.FILE_NOT_FOUND)
        return open(path, "rb", buffering=_READ_BUFFER)

    def open_write(self, context: RequestContext, size: Optional[int]) -> Any:
        if not self.writable:
            raise TFTPError(ErrorCode.ACCESS_VIOLATION, "server is read-only")
        path = self.resolve(context.filename)
        if os.path.isdir(path):
            raise TFTPError(ErrorCode.ACCESS_VIOLATION)
        exists = os.path.exists(path)
        if exists and not self.overwrite:
            raise TFTPError(ErrorCode.FILE_EXISTS)
        if not exists and not self.create:
            raise TFTPError(ErrorCode.FILE_NOT_FOUND)
        directory = os.path.dirname(path)
        if not os.path.isdir(directory):
            raise TFTPError(ErrorCode.FILE_NOT_FOUND, "directory not found")
        if size is not None:
            if self.max_upload is not None and size > self.max_upload:
                raise TFTPError(ErrorCode.DISK_FULL, "file too large")
            try:
                if shutil.disk_usage(directory).free < size:
                    raise TFTPError(ErrorCode.DISK_FULL)
            except OSError:
                pass
        writer = AtomicWriter(path, overwrite=self.overwrite)
        if self.max_upload is not None:
            return _Capped(writer, self.max_upload)
        return writer


def _is_root(filename: str, backslash: bool) -> bool:
    name = filename.replace("\\", "/") if backslash else filename
    return all(part in ("", ".") for part in name.split("/"))


class _Capped:
    """Refuses to grow an upload past ``limit`` bytes."""

    _tftp_copies_ = True

    def __init__(self, inner: AtomicWriter, limit: int) -> None:
        self._inner = inner
        self._left = limit

    def write(self, data) -> int:
        self._left -= len(data)
        if self._left < 0:
            raise TFTPError(ErrorCode.DISK_FULL, "file too large")
        return self._inner.write(data)

    def close(self) -> None:
        self._inner.close()

    def abort(self) -> None:
        self._inner.abort()
