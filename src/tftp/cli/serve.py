"""``pytftp serve``."""

from __future__ import annotations

import json as _json
import logging as _logging
import os as _os
import typing as _ty

from ..options import ServerOptions
from ..result import TransferResult
from ..server import Server
from .common import Base, error, result_json

__all__ = ["Serve"]


class Serve(Base):
    """Serve a directory until interrupted."""

    _parsername_ = "serve"
    _parseraliases_ = ["server"]

    root: str = "."
    "Directory to serve"
    ("root",)

    listen: str = "::"
    "Address to listen on; '::' is IPv6 and IPv4 where dual-stack works"
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

    max_blksize: int = 65464
    "Largest blksize granted"
    ("--max-blksize",)

    max_windowsize: int = 64
    "Largest windowsize granted"
    ("--max-windowsize",)

    max_sessions: int = 0
    "Concurrent transfers; 0 is unlimited"
    ("--max-sessions",)

    def __call__(self) -> "int | None":
        if not _os.path.isdir(self.root):
            error("error: not a directory: %s" % self.root)
            return 2
        on_complete: _ty.Optional[_ty.Callable[[TransferResult], None]] = None
        if self.json_out:
            on_complete = _print_json

        try:
            server = Server(
                self.root,
                self.listen,
                self.port,
                writable=self.write,
                create=not self.no_create,
                overwrite=self.overwrite,
                timeout=self.timeout,
                retries=self.retries,
                options=ServerOptions(max_blksize=self.max_blksize, max_windowsize=self.max_windowsize),
                max_sessions=self.max_sessions or None,
                on_complete=on_complete,
            )
        except OSError as exc:
            hint = None
            try:
                from netimps import bind_error_hint

                hint = bind_error_hint(exc, self.port)
            except ImportError:  # pragma: no cover
                pass
            error("error: cannot listen on %s port %d: %s" % (self.listen, self.port, hint or exc))
            return 1
        logger = _logging.getLogger("tftp")
        address = server.server_address
        logger.info(
            "serving %s on %s port %d%s%s",
            _os.path.abspath(self.root),
            address[0],
            address[1],
            " (dual-stack)" if server.dual_stack else "",
            "" if server.supports_pktinfo else " (replies from the routing table's source address)",
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.close()
        return None


def _print_json(result: TransferResult) -> None:
    print(_json.dumps(result_json(result)), flush=True)
