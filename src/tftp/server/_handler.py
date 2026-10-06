"""Server request handlers: what a request maps to.

A handler is any object with ``open_read(context)`` and
``open_write(context, size)``. :class:`~tftp.backends.FilesystemBackend` serves a
directory; subclass it, or write a handler from scratch, to serve generated
content (a per-client boot menu, a firmware image chosen by MAC) or to route
uploads somewhere other than a disk.
"""

from __future__ import annotations

import concurrent.futures
import copy
import dataclasses
import functools
import inspect
import os
import secrets
import stat
import tempfile
from typing import TYPE_CHECKING, Any, Mapping, Optional, Protocol, Tuple, Union

if TYPE_CHECKING:
    from netimps import Interface

from ..exceptions import TFTPError
from ..packet._enums import TFTPErrorCode
from ..packet._codec import RequestPacket

__all__ = [
    "AsyncTFTPHandler",
    "AsyncTFTPReader",
    "AsyncTFTPWriter",
    "AtomicWriter",
    "TFTPChunkReader",
    "TFTPHandler",
    "TFTPReader",
    "TFTPRequestContext",
    "TFTPWriter",
    "ThreadedHandler",
]


class TFTPRequestContext:
    """Everything known about a request when a handler is asked to open it.

    :ivar request: the parsed RRQ/WRQ (``filename``, ``mode``, ``options``).
    :ivar peer: the client's ``(host, port, ...)``.
    :ivar local_address: the address the request was sent to, when the
        platform reports it (see ``TFTPServer.has_pktinfo``), else ``None``.
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
        *,
        local_address: Optional[str] = None,
        interface_index: int = 0,
    ) -> None:
        self.request = request
        self.peer = peer
        self.local_address = local_address
        self.interface_index = interface_index
        self.interface: Optional[Interface] = None
        self.listing = False

    def with_filename(self, filename: str) -> "TFTPRequestContext":
        """A copy of this context for a request naming ``filename`` instead.

        Everything else is carried over (the listing flag, the interface), so
        a wrapper that rewrites names does not have to know the fields.
        """
        clone = copy.copy(self)
        clone.request = dataclasses.replace(self.request, filename=filename, raw=b"")
        return clone

    @property
    def filename(self) -> str:
        return self.request.filename

    @property
    def mode(self) -> str:
        return self.request.mode

    @property
    def options(self) -> Mapping[str, str]:
        return self.request.options

    def __repr__(self) -> str:
        return "TFTPRequestContext(%s %r from %s to %s)" % (
            "RRQ" if self.request.is_read else "WRQ",
            self.filename,
            self.peer[:2],
            self.local_address,
        )


class TFTPReader(Protocol):
    """A binary source a synchronous server reads a download from.

    Required: ``readinto(buffer)`` and ``close()``. A source with ``read(size)``
    instead is a :class:`TFTPChunkReader`.

    Optional members, each read once per transfer when present:

    * ``size`` -- the total length, an integer, which answers ``tsize``;
    * ``mtime`` -- the modification time in seconds, which answers ``x-mtime``;
    * ``set_wakeup(callback)`` -- stores a callable the source calls, from any
      thread, when a ``readinto`` that raised :class:`tftp.WouldBlock` can
      make progress;
    * ``lists_directories`` -- ``True`` on a source that holds a directory
      listing, which is what makes the server acknowledge ``x-list``.

    ``readinto`` may raise :class:`tftp.WouldBlock` for "no data yet".
    """

    def readinto(self, buffer: Any, /) -> Optional[int]: ...

    def close(self) -> None: ...


class TFTPChunkReader(Protocol):
    """A :class:`TFTPReader` that has ``read(size)`` in place of ``readinto``."""

    def read(self, size: int, /) -> bytes: ...

    def close(self) -> None: ...


class TFTPWriter(Protocol):
    """A binary sink a synchronous server writes an upload into.

    Required: ``write(data)`` and ``close()``. ``close`` is called once the last
    block is written and before it is acknowledged, so a failing ``close``
    reaches the client as an ERROR; it may raise :class:`tftp.WouldBlock`
    while the data is still on its way elsewhere.

    Optional members:

    * ``abort()`` -- called instead of ``close`` when the transfer fails;
    * ``copies_writes`` -- ``True`` when ``write`` copies what it is given.
      Without it ``write`` receives ``bytes``, because the argument would
      otherwise be a view of a receive buffer that the next packet reuses
      (standard file objects always count as copying);
    * ``set_wakeup(callback)`` -- as for :class:`TFTPReader`.
    """

    def write(self, data: Any, /) -> object: ...

    def close(self) -> None: ...


class TFTPHandler(Protocol):
    """What a synchronous server needs from a handler.

    Both hooks run one request at a time, in a worker thread unless the handler
    has ``opens_fast = True`` (an optional attribute: opening is quick and never
    blocks, so it runs inline on the server's thread). ``open_read`` returns a
    :class:`TFTPReader` or :class:`TFTPChunkReader`; ``open_write`` returns a
    :class:`TFTPWriter`, given the client's announced ``tsize`` as ``size`` (or
    ``None``).

    Either hook may raise :class:`TFTPError` to refuse with a specific code, or
    ``OSError``, which is mapped by errno (``ENOENT`` -> file not found,
    ``EACCES`` -> access violation, ``ENOSPC`` -> disk full...).

    A hook is a plain function: a handler whose hooks are ``async def`` belongs
    to :class:`~tftp.AsyncTFTPServer`, and the synchronous server refuses it.
    """

    def open_read(self, context: TFTPRequestContext) -> Union[TFTPReader, TFTPChunkReader]: ...

    def open_write(self, context: TFTPRequestContext, size: Optional[int]) -> TFTPWriter: ...


class AsyncTFTPReader(Protocol):
    """An asynchronous source an :class:`~tftp.AsyncTFTPServer` reads a download from.

    Required: ``async read(size)`` returning ``b""`` at the end, and
    ``async close()``. Optional: ``size``, the total length, which answers
    ``tsize``. The server reads ahead into a bounded buffer, so a slow source
    slows the transfer instead of filling memory.
    """

    async def read(self, size: int, /) -> bytes: ...

    async def close(self) -> None: ...


class AsyncTFTPWriter(Protocol):
    """An asynchronous sink an :class:`~tftp.AsyncTFTPServer` writes an upload into.

    Required: ``async write(data)`` and ``async close()``, which is awaited
    before the last block is acknowledged, so a failing ``close`` reaches the
    client as an ERROR. The server buffers a bounded amount ahead of the sink;
    ``data`` is always ``bytes``. An abandoned upload cancels the write in
    progress and does not call ``close``.
    """

    async def write(self, data: bytes, /) -> object: ...

    async def close(self) -> None: ...


class AsyncTFTPHandler(Protocol):
    """What an :class:`~tftp.AsyncTFTPServer` needs from a handler: coroutine hooks.

    ``async open_read(context)`` returns an :class:`AsyncTFTPReader` and
    ``async open_write(context, size)`` an :class:`AsyncTFTPWriter`. Both run as
    tasks on the server's event loop and may raise what :class:`TFTPHandler`'s
    hooks may. A synchronous handler is given to the server through
    :class:`ThreadedHandler`.
    """

    async def open_read(self, context: TFTPRequestContext) -> AsyncTFTPReader: ...

    async def open_write(self, context: TFTPRequestContext, size: Optional[int]) -> AsyncTFTPWriter: ...


def has_coroutine_hooks(handler: Any) -> bool:
    """Whether ``handler.open_read`` is an ``async def``, looking through a bound method or partial.

    Asked once, when a server is built, to refuse a handler of the other kind.
    """
    function = getattr(handler, "open_read", None)
    function = getattr(function, "__func__", function)
    while isinstance(function, functools.partial):
        function = function.func
    code = getattr(function, "__code__", None)
    return code is not None and bool(code.co_flags & inspect.CO_COROUTINE)


class ThreadedHandler:
    """Gives a synchronous handler to :class:`~tftp.AsyncTFTPServer`.

    ``open_read`` and ``open_write`` of ``handler`` run in ``executor`` (the
    loop's default one when ``None``), so a handler that blocks never stalls
    the loop, unless the handler has ``opens_fast = True``, when they run on
    the loop. The streams ``handler`` returns are synchronous and are read and
    written on the loop thread, as a synchronous server's are on its own.
    """

    def __init__(
        self, handler: TFTPHandler, *, executor: Optional[concurrent.futures.Executor] = None
    ) -> None:
        if has_coroutine_hooks(handler):
            raise TypeError(
                "ThreadedHandler wraps a synchronous handler; %r has coroutine hooks" % (handler,)
            )
        self.handler = handler
        self.executor = executor
        self.opens_fast = bool(getattr(handler, "opens_fast", False))

    async def open_read(self, context: TFTPRequestContext) -> Union[TFTPReader, TFTPChunkReader]:
        if self.opens_fast:
            return self.handler.open_read(context)
        import asyncio  # not at module level: a synchronous caller never needs it

        return await asyncio.get_running_loop().run_in_executor(
            self.executor, self.handler.open_read, context
        )

    async def open_write(self, context: TFTPRequestContext, size: Optional[int]) -> TFTPWriter:
        if self.opens_fast:
            return self.handler.open_write(context, size)
        import asyncio

        return await asyncio.get_running_loop().run_in_executor(
            self.executor, self.handler.open_write, context, size
        )


def _create_beside(directory: str, name: str, mode: int) -> Tuple[int, str]:
    """A new file ``.name.<random>.part`` in ``directory``, as ``(fd, path)``, made as ``open`` makes one."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    while True:
        tmp = os.path.join(directory, ".%s.%s.part" % (name, secrets.token_hex(4)))
        try:
            return os.open(tmp, flags, mode), tmp
        except FileExistsError:
            continue


class AtomicWriter:
    """Writes to a temporary file beside ``path`` and renames it on ``close``.

    A partial upload therefore never replaces or appears as ``path``;
    :meth:`abort` deletes the temporary file. The temporary file is private
    (mode 0600) unless ``mode`` is given: then it is created with that mode
    less the process's umask, and takes the permissions of the file it
    replaces. A process killed mid-transfer leaves ``.name.*.part`` beside
    ``path``.
    """

    copies_writes = True  # write() copies its argument (see as_write)

    def __init__(self, path: str, *, overwrite: bool = True, mode: Optional[int] = None) -> None:
        self.path = path
        self.overwrite = overwrite
        directory, name = os.path.split(path)
        if mode is None:
            fd, self._tmp = tempfile.mkstemp(prefix=".%s." % name, suffix=".part", dir=directory or ".")
        else:
            fd, self._tmp = _create_beside(directory or ".", name, mode)
            try:
                os.chmod(self._tmp, stat.S_IMODE(os.stat(path).st_mode))
            except OSError:  # nothing to take the permissions of
                pass
        self._file = os.fdopen(fd, "wb")
        self.closed = False

    def write(self, data: Union[bytes, bytearray, memoryview]) -> int:
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
