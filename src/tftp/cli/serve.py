"""``pytftp serve``: a directory, an HTTP(S) gateway, or a terminating proxy.

pytftp serve /srv/tftp --write
pytftp serve --http https://images.example.com/pxe/
pytftp serve --upstream 10.0.0.20 --compat pxe
pytftp serve /srv/tftp --per-client --ignore-case --remap '^/?pxelinux/=boot/'
"""

from __future__ import annotations

import json as _json
import typing as _ty

from .._result import TransferResult
from ..server._policy import TFTPServerLimits
from ..server._session import PortRange
from ..server._sync import TFTPServer
from ._common import Traced, bind_failure, error, flag
from ._content import Content
from ._negotiation import Negotiation
from ._signals import shutdown_on_signal

__all__ = ["Serve"]


class Serve(Content, Negotiation, Traced):
    """Serve files until interrupted."""

    _parsername_ = "serve"
    _parseraliases_ = ["server"]

    listen: _ty.Optional[_ty.List[str]] = None
    "Where to listen: HOST:PORT, [V6]:PORT, *:PORT, an adapter name or a MAC; repeat the option or separate by commas. A name is never resolved. Default: every address, IPv4 and IPv6"
    ("--listen", "-l")

    port: int = 69
    "UDP port to listen on"
    ("--port", "-p")

    timeout: float = 1.0
    "Seconds before retransmitting, unless a client negotiates its own"
    ("--timeout", "-t")

    retries: int = 5
    "Retransmissions before abandoning a transfer"
    ("--retries", "-r")

    max_sessions: int = 500
    "Concurrent transfers; 0 is unlimited (510 at most on Windows)"
    ("--max-sessions",)

    max_per_client: int = 0
    "Concurrent transfers per client address; 0 is unlimited"
    ("--max-per-client",)

    max_duration: _ty.Optional[float] = None
    "Longest a transfer may run, in seconds. Default: no limit"
    ("--max-duration",)

    port_range: _ty.Optional[str] = None
    "LOW:HIGH: take transfer ports from this range (for firewalls). Default: any free port"
    ("--port-range",)

    def _server(self) -> TFTPServer:
        """The server the flags describe, not yet bound; ``ValueError`` for a flag it cannot honour."""
        if self.max_duration is not None and not self.max_duration > 0:
            raise ValueError("--max-duration is a number of seconds above 0, not %g" % self.max_duration)
        handler = self._handler()
        options = self._options()
        ports = None
        if self.port_range:
            with flag("--port-range"):
                ports = PortRange.parse(self.port_range)
        return TFTPServer(
            handler,
            listen=self.listen,
            port=self.port,
            writable=self.write,
            create=not self.no_create,
            overwrite=self.overwrite,
            timeout=self.timeout,
            retries=self.retries,
            options=options,
            max_sessions=self.max_sessions or None,
            limits=TFTPServerLimits(
                max_sessions_per_client=self.max_per_client or None, max_duration=self.max_duration
            ),
            on_complete=_print_json if self.json_out else None,
            port_range=ports,
        )

    def __call__(self) -> _ty.Optional[int]:
        if self._pcap_missing():
            return 1
        try:
            server = self._server()
        except ValueError as exc:
            error("error: %s" % exc)
            return 2
        try:
            server.bind()
        except ValueError as exc:  # a listen specification that names nothing bindable
            server.close()
            error("error: %s" % exc)
            return 2
        except OSError as exc:
            server.close()
            return bind_failure(exc, ", ".join(self.listen or ["*", "::"]), self.port)
        try:
            server.trace = self._tracer()  # last: the capture file is created once the server can run
        except OSError as exc:
            server.close()
            error("error: %s" % exc)
            return 1
        self._logger_.info(
            "serving %s on %s%s",
            self._described(),
            ", ".join("%s port %d" % address[:2] for address in server.server_addresses),
            "" if server.has_pktinfo else " (replies from the routing table's source address)",
        )
        try:
            with shutdown_on_signal(server):
                server.serve_forever()
        except KeyboardInterrupt:  # no handler could be installed (not the main thread)
            pass
        finally:
            server.close()
            self._close_trace()
            self._logger_.info("served: %s", server.stats_snapshot())
        return None


def _print_json(result: TransferResult) -> None:
    print(_json.dumps(result.to_dict()), flush=True)
