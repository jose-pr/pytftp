"""``TftpUriPath``: the ``tftp://`` scheme for pathlib_next's ``UriPath``.

Registered both by class definition and through the ``pathlib_next.schemes``
entry point, so ``pathlib_next.uri.UriPath("tftp://host/boot/x")`` returns a
``TftpUriPath`` without importing ``tftp`` first, and ``copy()``/``move()``
cross between ``tftp:``, ``file:``, ``http:``, ``s3:``...
"""

from __future__ import annotations

from typing import Any, Tuple

from pathlib_next.uri import UriPath

from ..client import Client
from .local import tftp_open, tftp_stat

__all__ = ["TftpUriPath", "TftpBackend"]


class TftpBackend:
    """Per-endpoint state of a ``TftpUriPath``: the :class:`Client` settings to use.

    Created from the URI's host and port with default settings, or supplied
    with ``path.with_options(blksize=8192, windowsize=16)`` /
    ``path.with_backend(TftpBackend(client))``.
    """

    __slots__ = ("client", "__weakref__")

    def __init__(self, client: Client) -> None:
        self.client = client


class TftpUriPath(UriPath):
    """A file named by a ``tftp://host[:port]/path[;mode=netascii]`` URI (RFC 3617).

    The filename sent is the path after the authority's ``/``; a
    ``;mode=netascii`` suffix on the last segment selects the mode. Same
    operations as :class:`TftpPath`: whole-file reads and writes, ``stat()``
    by size probe, ``exists()``; no listing, deleting or renaming.
    """

    __SCHEMES = ("tftp",)
    __slots__ = ()

    def _initbackend(self) -> TftpBackend:
        source = self.source
        return TftpBackend(Client(source.host, source.port or 69))

    def with_options(self, **client_options: Any) -> "TftpUriPath":
        """This path with a ``Client`` built from ``client_options`` (blksize, windowsize...)."""
        source = self.source
        return self.with_backend(TftpBackend(Client(source.host, source.port or 69, **client_options)))

    def with_client(self, client: Client) -> "TftpUriPath":
        return self.with_backend(TftpBackend(client))

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

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        return False

    def _open(self, mode: str = "r", buffering: int = -1):
        filename, transfer_mode = self._target()
        return tftp_open(self.backend.client, filename, transfer_mode, mode, self)

    def _listdir(self):
        raise NotImplementedError("TFTP cannot list directories")
