"""What ``pytftp serve`` serves: a directory, an HTTP(S) gateway or an upstream TFTP server."""

from __future__ import annotations

import os as _os
import typing as _ty

from ..backends._case import CaseInsensitive
from ..backends._filesystem import FilesystemBackend
from ..backends._http import HTTPBackend
from ..backends._per_client import PerClient
from ..backends._proxy import UpstreamBackend
from ..backends._remap import Remap
from ..server._handler import TFTPHandler
from ._common import Base, flag

__all__ = ["Content"]


class Content(Base):
    """The flags that choose what a server serves, and the handler they build."""

    root: str = "."
    "Directory to serve (ignored with --http or --upstream)"
    ("root",)

    http: _ty.Optional[str] = None
    "Serve from this HTTP(S) base URL instead of a directory"
    ("--http",)

    upstream: _ty.Optional[str] = None
    "Serve from this TFTP server (host[:port]): a terminating proxy"
    ("--upstream",)

    write: bool = False
    "Accept uploads"
    ("--write", "-W")

    no_create: bool = False
    "Uploads may only replace existing files (with --overwrite)"
    ("--no-create",)

    overwrite: bool = False
    "Uploads may replace existing files"
    ("--overwrite",)

    per_client: bool = False
    "Serve ROOT/<client address>/ to a client that has one (IPv6 ':' written '-'), else ROOT"
    ("--per-client",)

    ignore_case: bool = False
    "Find files whatever the case of the requested name"
    ("--ignore-case",)

    remap: _ty.List[str] = []
    "REGEX=REPLACEMENT: rewrite requested names (first matching rule); repeatable"
    ("--remap",)

    def _handler(self) -> TFTPHandler:
        """The handler the flags describe; ``ValueError`` for a combination that serves nothing."""
        handler = self._source()
        if not self.remap:
            return handler
        with flag("--remap"):
            return Remap(handler, self.remap)

    def _source(self) -> TFTPHandler:
        if self.http and self.upstream:
            raise ValueError("give --http or --upstream, not both")
        if (self.http or self.upstream) and (self.per_client or self.ignore_case):
            raise ValueError("--per-client and --ignore-case serve a directory")
        if self.http:
            return HTTPBackend(self.http, writable=self.write)
        if self.upstream:
            return UpstreamBackend(self.upstream, writable=self.write)
        if not _os.path.isdir(self.root):
            raise ValueError("not a directory: %s" % self.root)
        kind = CaseInsensitive if self.ignore_case else FilesystemBackend

        def make(directory: str) -> TFTPHandler:
            return kind(directory, writable=self.write, create=not self.no_create, overwrite=self.overwrite)

        return PerClient(self.root, make) if self.per_client else make(self.root)

    def _described(self) -> str:
        """What is served, for the line that says the server is up."""
        return self.http or (self.upstream and "upstream " + self.upstream) or _os.path.abspath(self.root)
