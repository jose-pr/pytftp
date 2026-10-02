"""Choosing an upstream server for a request.

A route is any callable ``route(request, context) -> Upstream | None``.
``None`` refuses the request (the client gets ERROR 2). These helpers build
the common ones; :class:`RouteTable` combines them, first match wins.
"""

from __future__ import annotations

import ipaddress
from typing import Any, Callable, Iterable, NamedTuple, Optional, Sequence, Tuple, Union

__all__ = ["Upstream", "upstream", "RouteTable", "by_subnet", "by_prefix", "by_interface", "Route"]


class Upstream(NamedTuple):
    """Where to forward: an upstream server's host and request port."""

    host: str
    port: int = 69


UpstreamLike = Union[Upstream, str, Tuple[str, int]]
Route = Callable[[Any, Any], Optional[UpstreamLike]]


def upstream(value: UpstreamLike) -> Upstream:
    """``"host"``, ``"host:port"``, ``"[v6]:port"`` or ``(host, port)`` -> :class:`Upstream`."""
    if isinstance(value, Upstream):
        return value
    if isinstance(value, tuple):
        return Upstream(str(value[0]), int(value[1]))
    from netimps import normalize_host

    host, port = normalize_host(value, 69)
    return Upstream(host, port or 69)


def by_subnet(table: "dict[str, UpstreamLike] | Sequence[Tuple[str, UpstreamLike]]") -> Route:
    """Route by the client's address: ``{"10.1.0.0/16": "10.1.0.5", ...}``.

    The most specific (longest prefix) matching network wins. A v4 client
    seen as ``::ffff:a.b.c.d`` matches v4 networks.
    """
    items = table.items() if isinstance(table, dict) else table
    networks = sorted(
        ((ipaddress.ip_network(net, strict=False), upstream(target)) for net, target in items),
        key=lambda pair: pair[0].prefixlen,
        reverse=True,
    )

    def route(request: Any, context: Any) -> Optional[Upstream]:
        address = ipaddress.ip_address(str(context.peer[0]).split("%", 1)[0])
        if address.version == 6 and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        for network, target in networks:
            if address.version == network.version and address in network:
                return target
        return None

    return route


def by_prefix(table: "dict[str, UpstreamLike] | Sequence[Tuple[str, UpstreamLike]]") -> Route:
    """Route by filename prefix: ``{"windows/": "wds.lan", "": "default.lan"}``.

    The longest matching prefix wins; leading ``/`` and ``\\`` are ignored
    and ``\\`` counts as ``/``.
    """
    items = table.items() if isinstance(table, dict) else table
    prefixes = sorted(
        ((p.replace("\\", "/").lstrip("/"), upstream(t)) for p, t in items), key=lambda x: -len(x[0])
    )

    def route(request: Any, context: Any) -> Optional[Upstream]:
        name = request.filename.replace("\\", "/").lstrip("/")
        for prefix, target in prefixes:
            if name.startswith(prefix):
                return target
        return None

    return route


def by_interface(table: "dict[Union[int, str], UpstreamLike]") -> Route:
    """Route by arrival interface: index (int) or the address the request was sent to (str).

    Needs pktinfo; without it neither is known and nothing matches.
    """
    resolved = {key: upstream(target) for key, target in table.items()}

    def route(request: Any, context: Any) -> Optional[Upstream]:
        if context.interface_index and context.interface_index in resolved:
            return resolved[context.interface_index]
        if context.local_address is not None:
            return resolved.get(context.local_address.split("%", 1)[0])
        return None

    return route


class RouteTable:
    """Try ``routes`` in order; the first that returns an upstream wins.

    ``default`` (optional) answers when none does.
    """

    def __init__(self, routes: Iterable[Route], default: Optional[UpstreamLike] = None) -> None:
        self.routes = list(routes)
        self.default = upstream(default) if default is not None else None

    def __call__(self, request: Any, context: Any) -> Optional[Upstream]:
        for route in self.routes:
            target = route(request, context)
            if target is not None:
                return upstream(target)
        return self.default
