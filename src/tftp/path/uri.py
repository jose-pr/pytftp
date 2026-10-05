"""``TFTPURIPath``: the ``tftp://`` scheme for pathlib_next's ``UriPath``.

Registered both by class definition and through the ``pathlib_next.schemes``
entry point, so ``pathlib_next.uri.UriPath("tftp://host/boot/x")`` returns a
``TFTPURIPath`` without importing ``tftp`` first, and ``copy()``/``move()``
cross between ``tftp:``, ``file:``, ``http:``, ``s3:``...
"""

from __future__ import annotations

from typing import Any, Tuple

from pathlib_next.uri import UriPath

from ..client import TFTPClient
from .local import check_client, tftp_open, tftp_scandir, tftp_stat

__all__ = ["TFTPURIPath"]


class _TFTPBackend:
    """Per-endpoint state of a ``TFTPURIPath``: the :class:`TFTPClient` settings to use.

    Created from the URI's host and port with default settings, or supplied
    with ``path.with_options(blksize=8192, windowsize=16)`` /
    ``path.with_backend(_TFTPBackend(client))``.
    """

    __slots__ = ("client", "__weakref__")

    def __init__(self, client: TFTPClient) -> None:
        self.client = check_client(client)


class TFTPURIPath(UriPath):
    """A file named by a ``tftp://host[:port]/path[;mode=netascii]`` URI (RFC 3617).

    The filename sent is the path after the authority's ``/``; a
    ``;mode=netascii`` suffix on the last segment selects the mode. Same
    operations as :class:`TFTPPath`: whole-file reads and writes, ``stat()``
    by probe, ``exists()``, and listing against a server speaking ``x-list``;
    no deleting or renaming.
    """

    __SCHEMES = ("tftp",)
    __slots__ = ()

    def _initbackend(self) -> _TFTPBackend:
        source = self.source
        return _TFTPBackend(TFTPClient(source.host, source.port or 69))

    def with_options(self, **client_options: Any) -> "TFTPURIPath":
        """This path with a ``TFTPClient`` built from ``client_options`` (blksize, windowsize...)."""
        source = self.source
        return self.with_backend(_TFTPBackend(TFTPClient(source.host, source.port or 69, **client_options)))

    def with_client(self, client: TFTPClient) -> "TFTPURIPath":
        return self.with_backend(_TFTPBackend(client))

    def _target(self) -> Tuple[str, str]:
        """``(filename, transfer mode)`` from the URI path."""
        path = self.path
        mode = "octet"
        if ";" in path.rsplit("/", 1)[-1]:
            path, _, params = path.rpartition(";")
            key, _, value = params.partition("=")
            if key.lower() == "mode" and value.lower() in ("octet", "netascii"):
                mode = value.lower()
            else:
                path = "%s;%s" % (path, params)
        return (path[1:] if path.startswith("/") else path), mode

    @property
    def filename(self) -> str:
        return self._target()[0]

    @property
    def transfer_mode(self) -> str:
        return self._target()[1]

    def stat(self, *, follow_symlinks: bool = True):
        filename, mode = self._target()
        return tftp_stat(self.backend.client, filename, mode, self)

    def _open(self, mode: str = "r", buffering: int = -1):
        filename, transfer_mode = self._target()
        return tftp_open(self.backend.client, filename, transfer_mode, mode, self)

    def _scandir(self):
        return tftp_scandir(self.backend.client, self._target()[0], self)

    def _listdir(self):
        for name, _ in self._scandir():
            yield name
