"""``pytftp serve``: a directory, an HTTP(S) gateway, or a terminating proxy.

pytftp serve /srv/tftp --write
pytftp serve --http https://images.example.com/pxe/
pytftp serve --upstream 10.0.0.20 --compat pxe
pytftp serve /srv/tftp --per-client --ignore-case --remap '^/?pxelinux/=boot/'
"""

from __future__ import annotations

import json as _json
import logging as _logging
import os as _os
import typing as _ty

from ..options import LISTING_OPTIONS, PROFILES, STANDARD_OPTIONS, ServerOptions
from ..result import TransferResult
from ..server import TFTPServer, ServerLimits
from .common import (
    PROFILE_NAMES,
    Choice,
    Traced,
    bind_failure,
    error,
    port_range,
    result_json,
    shutdown_on_signal,
)

__all__ = ["Serve"]


class Serve(Traced):
    """Serve files until interrupted."""

    _parsername_ = "serve"
    _parseraliases_ = ["server"]

    root: str = "."
    "Directory to serve (ignored with --http or --upstream)"
    ("root",)

    http: _ty.Optional[str] = None
    "Serve from this HTTP(S) base URL instead of a directory"
    ("--http",)

    upstream: _ty.Optional[str] = None
    "Serve from this TFTP server (host[:port]): a terminating proxy"
    ("--upstream",)

    interface: _ty.Optional[str] = None
    "Listen on this network adapter (name, MAC or address); IPv4 unless --listen is '::'"
    ("--interface",)

    listen: _ty.Optional[str] = None
    "Address to listen on; default '::', IPv6 and IPv4 where dual-stack works"
    ("--listen", "-l")

    port: int = 69
    "UDP port"
    ("--port", "-p")

    write: bool = False
    "Accept uploads"
    ("--write", "-W")

    no_create: bool = False
    "Uploads may only replace existing files (with --overwrite)"
    ("--no-create",)

    overwrite: bool = False
    "Uploads may replace existing files"
    ("--overwrite",)

    timeout: float = 1.0
    "Seconds before retransmitting, unless a client negotiates its own"
    ("--timeout", "-t")

    retries: int = 5
    "Retransmissions before abandoning a transfer"
    ("--retries", "-r")

    compat: _ty.Annotated[_ty.Optional[str], Choice(*PROFILE_NAMES)] = None
    "Negotiate as this compatibility profile (replaces the option flags below)"
    ("--compat",)

    max_blksize: int = 65464
    "Largest blksize granted"
    ("--max-blksize",)

    max_windowsize: int = 64
    "Largest windowsize granted"
    ("--max-windowsize",)

    allow: _ty.List[str] = []
    "Also accept this extension option (blksize2, utimeout, rollover, cookie, mstfwindow); repeatable"
    ("--allow",)

    refuse: _ty.List[str] = []
    "Never acknowledge this option, e.g. windowsize for broken firmware; repeatable"
    ("--refuse",)

    listing: bool = False
    "Answer directory listings and modification times (pytftp's x-list/x-mtime, for 'pytftp ls')"
    ("--listing",)

    fit_mtu: bool = False
    "Lower blksize to fit the arrival interface's MTU (no IP fragments)"
    ("--fit-mtu",)

    max_sessions: int = 500
    "Concurrent transfers; 0 is unlimited (510 at most on Windows)"
    ("--max-sessions",)

    max_per_client: int = 0
    "Concurrent transfers per client address; 0 is unlimited"
    ("--max-per-client",)

    port_range: _ty.Optional[str] = None
    "LOW:HIGH: take transfer ports from this range (for firewalls)"
    ("--port-range",)

    per_client: bool = False
    "Serve ROOT/<client address>/ to a client when it exists (IPv6 ':' written '-')"
    ("--per-client",)

    ignore_case: bool = False
    "Find files whatever the case of the requested name"
    ("--ignore-case",)

    remap: _ty.List[str] = []
    "REGEX=REPLACEMENT: rewrite requested names (first matching rule); repeatable"
    ("--remap",)

    def _handler(self) -> _ty.Any:
        from .handlers import Remap, parse_rule

        rules = [parse_rule(rule) for rule in self.remap]
        handler = self._source()
        return Remap(handler, rules) if rules else handler

    def _source(self) -> _ty.Any:
        if self.http and self.upstream:
            raise ValueError("give --http or --upstream, not both")
        if (self.http or self.upstream) and (self.per_client or self.ignore_case):
            raise ValueError("--per-client and --ignore-case serve a directory")
        if self.http:
            from ..backends import HttpHandler

            return HttpHandler(self.http, writable=self.write)
        if self.upstream:
            from ..backends import UpstreamHandler

            return UpstreamHandler(self.upstream, writable=self.write)
        if not _os.path.isdir(self.root):
            raise ValueError("not a directory: %s" % self.root)
        from ..server import FileSystemHandler
        from .handlers import CaseInsensitive, PerClient

        kind = CaseInsensitive if self.ignore_case else FileSystemHandler

        def make(directory: str) -> _ty.Any:
            return kind(directory, writable=self.write, create=not self.no_create, overwrite=self.overwrite)

        # Always a handler object: --remap wraps it.
        return PerClient(self.root, make) if self.per_client else make(self.root)

    def _options(self) -> ServerOptions:
        listing = LISTING_OPTIONS if self.listing else frozenset()
        if self.compat:
            profile = PROFILES[self.compat].server
            if not listing:
                return profile
            return ServerOptions(
                max_blksize=profile.max_blksize,
                max_windowsize=profile.max_windowsize,
                max_window_bytes=profile.max_window_bytes,
                allowed=profile.allowed | listing,
                refused=profile.refused,
                fit_mtu=profile.fit_mtu,
                registry=profile.registry,
            )
        return ServerOptions(
            max_blksize=self.max_blksize,
            max_windowsize=self.max_windowsize,
            allowed=STANDARD_OPTIONS | set(self.allow) | listing,
            refused=self.refuse,
            fit_mtu=self.fit_mtu,
        )

    def __call__(self) -> "int | None":
        try:
            handler = self._handler()
            options = self._options()
            ports = port_range(self.port_range)
        except ValueError as exc:
            error("error: %s" % exc)
            return 2
        on_complete: _ty.Optional[_ty.Callable[[TransferResult], None]] = None
        if self.json_out:
            on_complete = _print_json
        try:
            server = TFTPServer(
                handler,
                self.listen,
                self.port,
                writable=self.write,
                create=not self.no_create,
                overwrite=self.overwrite,
                timeout=self.timeout,
                retries=self.retries,
                options=options,
                max_sessions=self.max_sessions or None,
                limits=ServerLimits(max_sessions_per_client=self.max_per_client or None),
                on_complete=on_complete,
                trace=self._tracer(),
                port_range=ports,
                interface=self.interface,
            )
        except OSError as exc:
            return bind_failure(exc, self.listen or self.interface or "::", self.port)
        logger = _logging.getLogger("tftp")
        address = server.server_address
        source = self.http or (self.upstream and "upstream " + self.upstream) or _os.path.abspath(self.root)
        logger.info(
            "serving %s on %s port %d%s%s",
            source,
            address[0],
            address[1],
            " (dual-stack)" if server.dual_stack else "",
            "" if server.supports_pktinfo else " (replies from the routing table's source address)",
        )
        try:
            with shutdown_on_signal(server):
                server.serve_forever()
        except KeyboardInterrupt:  # no handler could be installed (not the main thread)
            pass
        finally:
            server.close()
            self._close_trace()
            logger.info("served: %s", server.stats_snapshot())
        return None


def _print_json(result: TransferResult) -> None:
    print(_json.dumps(result_json(result)), flush=True)
