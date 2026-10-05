"""Choosing an upstream server for a request.

A route is any callable ``route(request, context) -> Upstream | None``.
``None`` refuses the request (the client gets ERROR 2). These helpers build
the common ones; :class:`RouteTable` combines them, first match wins.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, NamedTuple, Optional, Sequence, Tuple, Union

if TYPE_CHECKING:
    from netimps import HostLike, Interface, IPAddressLike, IPNetworkLike

__all__ = ["Upstream", "upstream", "RouteTable", "by_subnet", "by_prefix", "by_interface", "RouteFunction"]


class Upstream(NamedTuple):
    """Where to forward: an upstream server's host and request port.

    ``host`` is whatever was given: a name or address string, an
    ``ipaddress`` address, or a ``netimps.Host``.
    """

    host: "HostLike"
    port: int = 69


UpstreamLike = Union[Upstream, "HostLike", Tuple["HostLike", int]]
RouteFunction = Callable[[Any, Any], Optional[UpstreamLike]]


def upstream(value: UpstreamLike) -> Upstream:
    """An :class:`Upstream` from ``"host"``, ``"host:port"``, ``"[v6]:port"``,
    an address or ``netimps.Host`` (port 69 unless its text has one), or
    ``(host, port)``."""
    if isinstance(value, Upstream):
        return value
    if isinstance(value, tuple):
        return Upstream(value[0], int(value[1]))
    from netimps import split_host

    host, port = split_host(value, default_port=69)
    return Upstream(host, port or 69)


def by_subnet(
    table: "dict[IPNetworkLike, UpstreamLike] | Sequence[Tuple[IPNetworkLike, UpstreamLike]]",
) -> RouteFunction:
    """Route by the client's address: ``{"10.1.0.0/16": "10.1.0.5", ...}``.

    Keys are anything ``netimps.parse(..., IPNetwork)`` takes: CIDR strings,
    ``ipaddress`` networks, interfaces (their network) or addresses (a /32 or
    /128). The most specific (longest prefix) matching network wins. A v4
    client seen as ``::ffff:a.b.c.d`` matches v4 networks.
    """
    from netimps import IPNetwork, parse

    items = table.items() if isinstance(table, dict) else table
    networks = sorted(
        ((parse(net, IPNetwork), upstream(target)) for net, target in items),
        key=lambda pair: pair[0].prefixlen,
        reverse=True,
    )

    def route(request: Any, context: Any) -> Optional[Upstream]:
        from netimps import unmap

        address = unmap(context.peer[0])
        for network, target in networks:
            if address.version == network.version and address in network:
                return target
        return None

    return route


def by_prefix(table: "dict[str, UpstreamLike] | Sequence[Tuple[str, UpstreamLike]]") -> RouteFunction:
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


def by_interface(table: "dict[Union[int, Interface, IPAddressLike], UpstreamLike]") -> RouteFunction:
    """Route by arrival: an interface index (``int``), a ``netimps.Interface``,
    or the local address the request was sent to (an address string or
    object, an ``ipaddress`` interface, a ``netimps.Host``).

    Needs pktinfo; without it neither is known and nothing matches.
    """
    from netimps import Host, Interface, split_zone, unmap

    by_index: Dict[int, Upstream] = {}
    by_address: Dict[Any, Upstream] = {}
    for key, target in table.items():
        if isinstance(key, bool):
            raise TypeError("by_interface keys are indexes, interfaces or addresses, not bool")
        if isinstance(key, int):
            by_index[key] = upstream(target)
        elif isinstance(key, Interface):
            by_index[key.index] = upstream(target)
        else:
            address = Host(key).ip()  # an ipaddress interface counts as its address
            if address is None:
                raise ValueError("by_interface: cannot resolve %r" % (key,))
            by_address[unmap(address)] = upstream(target)

    def route(request: Any, context: Any) -> Optional[Upstream]:
        if context.interface_index and context.interface_index in by_index:
            return by_index[context.interface_index]
        if context.local_address is not None and by_address:
            return by_address.get(unmap(split_zone(context.local_address)[0]))
        return None

    return route


class RouteTable:
    """Try ``routes`` in order; the first that returns an upstream wins.

    ``default`` (optional) answers when none does.
    """

    def __init__(self, routes: Iterable[RouteFunction], default: Optional[UpstreamLike] = None) -> None:
        self.routes = list(routes)
        self.default = upstream(default) if default is not None else None

    def __call__(self, request: Any, context: Any) -> Optional[Upstream]:
        for route in self.routes:
            target = route(request, context)
            if target is not None:
                return upstream(target)
        return self.default
