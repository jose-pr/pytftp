"""A handler that gives each client a directory named for its address."""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Optional, Tuple, Union

from ..exceptions import TFTPError
from ..packet._enums import TFTPErrorCode
from ..server._handler import TFTPChunkReader, TFTPHandler, TFTPReader, TFTPRequestContext, TFTPWriter

__all__ = ["PerClient"]


class PerClient:
    """Serves ``root/<client address>/`` to a client whose directory exists.

    A client with no directory of its own is served ``root`` itself by
    default, so it can read every other client's directory: that is a
    convenience for a fleet that mostly shares files, not isolation. With
    ``fallback=False`` such a client is answered ERROR 1 (file not found) to
    every request, and a client reaches only its own directory (what it names
    stays inside that directory, as :class:`FilesystemBackend` keeps it).

    The directory name is the client's address with each ``:`` written ``-``
    (``fe80--1``, valid on Windows), and an IPv4 client seen through a
    dual-stack socket is named by its IPv4 address (see :meth:`directory`).

    :param root: the directory holding one directory per client.
    :param make: ``make(directory)`` returns the handler for a directory, for
        example ``lambda d: FilesystemBackend(d, writable=True)``; it is called
        once for each directory a client is served from. It should return a
        handler that opens quickly (a local directory): this one opens inline
        on the server's thread.
    :param fallback: serve ``root`` itself to a client with no directory.
    """

    opens_fast = True

    def __init__(
        self,
        root: Union[str, "os.PathLike[str]"],
        make: Callable[[str], TFTPHandler],
        *,
        fallback: bool = True,
    ) -> None:
        self.root = os.path.realpath(os.fspath(root))
        self.make = make
        self.fallback = fallback
        self._handlers: Dict[str, TFTPHandler] = {}

    @staticmethod
    def directory(peer: Tuple[Any, ...]) -> str:
        """The directory name for the client at ``peer`` (``(host, port, ...)``)."""
        from netimps import split_zone, unmap

        return str(unmap(split_zone(str(peer[0]))[0])).replace(":", "-")

    def handler_for(self, context: TFTPRequestContext) -> TFTPHandler:
        """The handler of the directory ``context``'s client is served from.

        :raises TFTPError: ERROR 1, for a client with no directory when
            ``fallback`` is false.
        """
        directory = os.path.join(self.root, self.directory(context.peer))
        if not os.path.isdir(directory):
            if not self.fallback:
                raise TFTPError(TFTPErrorCode.FILE_NOT_FOUND)
            directory = self.root
        handler = self._handlers.get(directory)
        if handler is None:
            handler = self._handlers[directory] = self.make(directory)
        return handler

    def open_read(self, context: TFTPRequestContext) -> Union[TFTPReader, TFTPChunkReader]:
        return self.handler_for(context).open_read(context)

    def open_write(self, context: TFTPRequestContext, size: Optional[int]) -> TFTPWriter:
        return self.handler_for(context).open_write(context, size)

    def __repr__(self) -> str:
        return "PerClient(%r, fallback=%r)" % (self.root, self.fallback)
