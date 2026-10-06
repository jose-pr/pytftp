"""``TFTPURIPath``: the ``tftp://`` scheme for pathlib_next's ``UriPath``.

Registered both by class definition and through the ``pathlib_next.schemes``
entry point, so ``pathlib_next.uri.UriPath("tftp://host/boot/x")`` returns a
``TFTPURIPath`` without importing ``tftp`` first, and ``copy()``/``move()``
cross between ``tftp:``, ``file:``, ``http:``, ``s3:``...
"""

from __future__ import annotations

import sys
from typing import Any, Dict, Mapping, Optional, Tuple
from urllib.parse import quote

from pathlib_next.uri import UriPath

from ..client import TFTPClient
from ..exceptions import TFTPValueError
from ..uri import _client_keywords_over, _decode, _normal_mode, _normal_options, _split_parameters
from .local import check_client, tftp_open, tftp_scandir, tftp_stat, tftp_unlink

__all__ = ["TFTPURIPath"]


class _TFTPBackend:
    """Per-endpoint state of a ``TFTPURIPath``: the :class:`TFTPClient` settings to use.

    Created from the URI's host and port with default settings, or supplied
    with ``path.with_options(blksize=8192, windowsize=16)`` /
    ``path.with_backend(_TFTPBackend(client))``. ``explicit`` holds the keywords
    the client was built from, which win over a URI's own options; ``None``
    for a client that was supplied, which a URI's options never change.
    """

    __slots__ = ("client", "explicit", "__weakref__")

    def __init__(self, client: TFTPClient, explicit: Optional[Mapping[str, Any]] = None) -> None:
        self.client = check_client(client)
        self.explicit = None if explicit is None else dict(explicit)


class TFTPURIPath(UriPath):
    """A file named by a ``tftp://host[:port]/path[;mode=netascii]`` URI (RFC 3617).

    The filename sent is the path after the authority's ``/``; a
    ``;mode=netascii`` suffix on the last segment selects the mode. Transfer
    options follow the grammar of :class:`tftp.TFTPURL`: ``;name=value``
    parameters on the last segment, or a ``?name=value&name=value`` query. The
    path is decoded before it is read, so a ``;`` in a file name or in a
    parameter's value cannot be told from a delimiter: spell such a value in
    the query. Options built with ``with_options`` win over the URI's own,
    and a client given to ``with_client`` is used as it is. Same
    operations as :class:`TFTPPath`: whole-file reads and writes, ``stat()``
    by probe, ``exists()``, and listing against a server speaking ``x-list``;
    no deleting or renaming.
    """

    __SCHEMES = ("tftp",)
    __slots__ = ()

    def _initbackend(self) -> _TFTPBackend:
        source = self.source
        return _TFTPBackend(TFTPClient(source.host, source.port or 69), {})

    def with_options(self, **client_options: Any) -> "TFTPURIPath":
        """This path with a ``TFTPClient`` built from ``client_options`` (blksize, windowsize...).

        A keyword given here wins over the option of the same name in the URI.
        """
        source = self.source
        client = TFTPClient(source.host, source.port or 69, **client_options)
        return self.with_backend(_TFTPBackend(client, client_options))

    def with_client(self, client: TFTPClient) -> "TFTPURIPath":
        return self.with_backend(_TFTPBackend(client))

    def _parts(self) -> Tuple[str, str, Dict[str, str]]:
        """``(filename, transfer mode, options)`` from the URI path and query.

        The first ``;`` of the last segment starts its parameters, and the
        query is read as the ``?`` spelling; both are read by the one splitter
        :class:`tftp.TFTPURL` uses. Raises :class:`TFTPValueError` for text
        that grammar refuses.
        """
        if self.fragment:
            raise TFTPValueError("a tftp:// URI has no fragment: %r" % self.fragment)
        head, slash, last = self.path.rpartition("/")
        raw = quote(last, safe=";=")
        if self.query:
            raw = "%s?%s" % (raw, self.query)
        quoted, mode, options = _split_parameters(raw)
        filename = head + slash + _decode(quoted)
        normal = _normal_options(options)
        return (
            filename[1:] if filename.startswith("/") else filename,
            "octet" if mode is None else _normal_mode(mode),
            dict(normal),
        )

    def _client_for(self, options: Mapping[str, str]) -> TFTPClient:
        """The backend's client, rebuilt with the URI's ``options`` under its own keywords."""
        backend = self.backend
        if not options or backend.explicit is None:
            return backend.client
        source = self.source
        return TFTPClient(source.host, source.port or 69, **_client_keywords_over(options, backend.explicit))

    @property
    def filename(self) -> str:
        return self._parts()[0]

    @property
    def transfer_mode(self) -> str:
        return self._parts()[1]

    def stat(self, *, follow_symlinks: bool = True):
        filename, mode, options = self._parts()
        return tftp_stat(self._client_for(options), filename, mode, self)

    def _open(self, mode: str = "r", buffering: int = -1):
        filename, transfer_mode, options = self._parts()
        return tftp_open(self._client_for(options), filename, transfer_mode, mode, self)

    def unlink(self, missing_ok: bool = False) -> None:
        tftp_unlink(self, missing_ok, sys._getframe(1))

    def _scandir(self):
        filename, _, options = self._parts()
        return tftp_scandir(self._client_for(options), filename, self)

    def _listdir(self):
        for name, _ in self._scandir():
            yield name
