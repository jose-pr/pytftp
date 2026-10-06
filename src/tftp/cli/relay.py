"""``pytftp relay``: forward TFTP to upstream servers, packets unchanged.

pytftp relay 10.0.0.20
pytftp relay --route-subnet 10.1.0.0/16=10.1.0.5 --route-prefix windows/=wds.lan 10.0.0.20
"""

from __future__ import annotations

import json as _json
import typing as _ty

from ..relay._core import TFTPRelay
from ..relay._routing import RouteTable, by_prefix, by_subnet
from ..relay._session import RelaySummary
from ..server._session import PortRange
from ._common import Traced, bind_failure, error, flag
from ._signals import shutdown_on_signal

__all__ = ["RelayCmd"]


class RelayCmd(Traced):
    """Relay requests to upstream TFTP servers (transparent: packets unchanged)."""

    _parsername_ = "relay"

    upstream: _ty.Optional[str] = None
    "Upstream server (host[:port]) for a request no route matches. Default: none (an unmatched request is refused)"
    ("upstream",)

    route_subnet: _ty.List[str] = []
    "CIDR=HOST[:PORT]: clients in CIDR go to HOST; repeatable, longest prefix wins"
    ("--route-subnet",)

    route_prefix: _ty.List[str] = []
    "PREFIX=HOST[:PORT]: filenames starting with PREFIX go to HOST; repeatable"
    ("--route-prefix",)

    interface: _ty.Optional[str] = None
    "Listen on this network adapter (name, MAC or address); IPv4 unless --listen is '::'. Default: the --listen address"
    ("--interface",)

    listen: _ty.Optional[str] = None
    "Address to listen on. Default: '::', IPv6 and IPv4 where dual-stack works"
    ("--listen", "-l")

    port: int = 69
    "UDP port to listen on"
    ("--port", "-p")

    idle_timeout: float = 30.0
    "End a relayed transfer after this many seconds without traffic"
    ("--idle-timeout",)

    max_sessions: int = 0
    "Concurrent transfers; 0 is unlimited"
    ("--max-sessions",)

    port_range: _ty.Optional[str] = None
    "LOW:HIGH: take transfer ports from this range (for firewalls). Default: any free port"
    ("--port-range",)

    def __call__(self) -> _ty.Optional[int]:
        try:
            routes = []
            if self.route_prefix:
                with flag("--route-prefix"):
                    routes.append(by_prefix(self.route_prefix))
            if self.route_subnet:
                with flag("--route-subnet"):
                    routes.append(by_subnet(self.route_subnet))
            if not routes and not self.upstream:
                raise ValueError("give an upstream server, or routes")
            route = RouteTable(routes, default=self.upstream)
            ports = None
            if self.port_range:
                with flag("--port-range"):
                    ports = PortRange.parse(self.port_range)
        except ValueError as exc:
            error("error: %s" % exc)
            return 2
        on_end = _print_summary if self.json_out else None
        try:
            relay = TFTPRelay(
                route,
                host=self.listen,
                port=self.port,
                idle_timeout=self.idle_timeout,
                max_sessions=self.max_sessions or None,
                on_session_end=on_end,
                port_range=ports,
                interface=self.interface,
            )
            relay.bind()
        except OSError as exc:
            return bind_failure(exc, self.listen or self.interface or "::", self.port)
        try:
            relay.trace = self._tracer()  # last: the capture file is created once the relay can run
        except OSError as exc:
            relay.close()
            error("error: %s" % exc)
            return 1
        logger = self._logger_
        address = relay.server_address
        assert address is not None  # bound above
        logger.info("relaying on %s port %d", address[0], address[1])
        try:
            with shutdown_on_signal(relay):
                relay.serve_forever()
        except KeyboardInterrupt:  # no handler could be installed (not the main thread)
            pass
        finally:
            relay.close()
            self._close_trace()
            logger.info("relayed: %s", relay.stats_snapshot())
        return None


def _print_summary(summary: RelaySummary) -> None:
    print(_json.dumps(summary.to_dict()), flush=True)
