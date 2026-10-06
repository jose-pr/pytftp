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
    "Directory to serve; not used with --http or --upstream"
    ("root",)

    http: _ty.Optional[str] = None
    "Serve from this HTTP(S) base URL instead of a directory. Default: a directory"
    ("--http",)

    upstream: _ty.Optional[str] = None
    "Serve from this TFTP server (host[:port]), as a terminating proxy. Default: a directory"
    ("--upstream",)

    write: bool = False
    "Accept uploads. Default: read-only"
    ("--write", "-W")

    no_create: bool = False
    "Uploads may only replace existing files (with --overwrite). Default: they may create"
    ("--no-create",)

    overwrite: bool = False
    "Uploads may replace existing files. Default: an existing file is refused"
    ("--overwrite",)

    max_upload: _ty.Optional[int] = None
    "Largest upload accepted, in bytes (a directory only). Default: no limit"
    ("--max-upload",)

    per_client: bool = False
    "Serve ROOT/<client address>/ to a client that has one (IPv6 ':' written '-'), else ROOT. This is not isolation: a client with no directory sees ROOT, other clients' directories included (see --per-client-only)"
    ("--per-client",)

    per_client_only: bool = False
    "Serve only ROOT/<client address>/ and give a client with no directory nothing; implies --per-client. Default: off"
    ("--per-client-only",)

    ignore_case: bool = False
    "Find files whatever the case of the requested name. Default: the exact name"
    ("--ignore-case",)

    remap: _ty.List[str] = []
    "REGEX=REPLACEMENT: rewrite requested names (first matching rule); repeatable. Default: names as requested"
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
        if (self.http or self.upstream) and (
            self.per_client or self.per_client_only or self.ignore_case or self.max_upload is not None
        ):
            raise ValueError(
                "--per-client, --per-client-only, --ignore-case and --max-upload serve a directory"
            )
        if self.max_upload is not None and self.max_upload < 0:
            raise ValueError("--max-upload is a number of bytes, not %d" % self.max_upload)
        if self.http:
            return HTTPBackend(self.http, writable=self.write)
        if self.upstream:
            return UpstreamBackend(self.upstream, writable=self.write)
        if not _os.path.isdir(self.root):
            raise ValueError("not a directory: %s" % self.root)
        kind = CaseInsensitive if self.ignore_case else FilesystemBackend

        def make(directory: str) -> TFTPHandler:
            return kind(
                directory,
                writable=self.write,
                create=not self.no_create,
                overwrite=self.overwrite,
                max_upload=self.max_upload,
            )

        if self.per_client or self.per_client_only:
            return PerClient(self.root, make, fallback=not self.per_client_only)
        return make(self.root)

    def _described(self) -> str:
        """What is served, for the line that says the server is up."""
        return self.http or (self.upstream and "upstream " + self.upstream) or _os.path.abspath(self.root)
