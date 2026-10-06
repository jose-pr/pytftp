"""``pytftp relay``: forward TFTP to upstream servers, packets unchanged.

pytftp relay 10.0.0.20
pytftp relay --route-subnet 10.1.0.0/16=10.1.0.5 --route-prefix windows/=wds.lan 10.0.0.20
"""

from __future__ import annotations

import json as _json
import logging as _logging
import typing as _ty

from ..relay import TFTPRelay, RouteTable, by_prefix, by_subnet
from .common import Traced, bind_failure, error, port_range, shutdown_on_signal

__all__ = ["RelayCmd"]


def _pairs(values: _ty.List[str], flag: str) -> _ty.List[_ty.Tuple[str, str]]:
    pairs = []
    for value in values:
        key, sep, target = value.partition("=")
        if not sep or not target:
            raise ValueError("%s expects KEY=HOST[:PORT], got %r" % (flag, value))
        pairs.append((key, target))
    return pairs


class RelayCmd(Traced):
    """Relay requests to upstream TFTP servers (transparent: packets unchanged)."""

    _parsername_ = "relay"

    upstream: _ty.Optional[str] = None
    "Default upstream server (host[:port]) when no route matches"
    ("upstream",)

    route_subnet: _ty.List[str] = []
    "CIDR=HOST[:PORT]: clients in CIDR go to HOST; repeatable, longest prefix wins"
    ("--route-subnet",)

    route_prefix: _ty.List[str] = []
    "PREFIX=HOST[:PORT]: filenames starting with PREFIX go to HOST; repeatable"
    ("--route-prefix",)

    interface: _ty.Optional[str] = None
    "Listen on this network adapter (name, MAC or address); IPv4 unless --listen is '::'"
    ("--interface",)

    listen: _ty.Optional[str] = None
    "Address to listen on (default '::')"
    ("--listen", "-l")

    port: int = 69
    "UDP port"
    ("--port", "-p")

    idle_timeout: float = 30.0
    "End a relayed transfer after this many seconds without traffic"
    ("--idle-timeout",)

    max_sessions: int = 0
    "Concurrent transfers; 0 is unlimited"
    ("--max-sessions",)

    port_range: _ty.Optional[str] = None
    "LOW:HIGH: take transfer ports from this range (for firewalls)"
    ("--port-range",)

    def __call__(self) -> "int | None":
        try:
            routes = []
            if self.route_prefix:
                routes.append(by_prefix(_pairs(self.route_prefix, "--route-prefix")))
            if self.route_subnet:
                routes.append(by_subnet(_pairs(self.route_subnet, "--route-subnet")))
            if not routes and not self.upstream:
                raise ValueError("give an upstream server, or routes")
            route = RouteTable(routes, default=self.upstream)
            ports = port_range(self.port_range)
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
        logger = _logging.getLogger("tftp")
        address = relay.server_address
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


def _print_summary(summary: _ty.Any) -> None:
    record = summary._asdict()
    record["client"] = list(summary.client[:2])
    record["upstream"] = list(summary.upstream[:2])
    print(_json.dumps(record), flush=True)
