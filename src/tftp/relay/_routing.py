"""Choosing an upstream server for a request.

A route is any callable ``route(request, context) -> Upstream | None``.
``None`` refuses the request (the client gets ERROR 2). These helpers build
the common ones; :class:`RouteTable` combines them, first match wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, Optional, Sequence, Tuple, Union

if TYPE_CHECKING:
    from netimps import HostLike, Interface, IPNetworkLike

from ..exceptions import TFTPValueError
from ..packet._codec import RequestPacket
from ..server._handler import TFTPRequestContext

__all__ = ["Upstream", "RouteTable", "by_subnet", "by_prefix", "by_interface", "RouteFunction"]


def _items(table: Any) -> Iterable[Tuple[Any, Any]]:
    """The ``(key, target)`` pairs of a routing table.

    A dict, or a sequence of pairs and ``"KEY=HOST[:PORT]"`` text (split at the first ``=``).
    """
    if isinstance(table, dict):
        return table.items()
    pairs = []
    for item in table:
        if isinstance(item, str):
            key, sep, target = item.partition("=")
            if not sep or not target:
                raise TFTPValueError("a route is KEY=HOST[:PORT], not %r" % item)
            item = (key, target)
        pairs.append(item)
    return pairs


@dataclass(frozen=True)
class Upstream:
    """Where to forward: an upstream server's host and request port.

    An immutable value that equals only another ``Upstream``. ``host`` is
    whatever was given: a name or address string, an ``ipaddress`` address, or
    a ``netimps.Host``; ``port`` is an ``int`` in 1..65535 (``TypeError`` for
    another type, :class:`TFTPValueError` outside the range).
    """

    host: "HostLike"
    port: int = 69

    def __post_init__(self) -> None:
        if isinstance(self.port, bool) or not isinstance(self.port, int):
            raise TypeError("an upstream port is an int, not %s" % type(self.port).__name__)
        if not 1 <= self.port <= 65535:
            raise TFTPValueError("an upstream port is 1 to 65535, not %d" % self.port)

    @classmethod
    def parse(cls, value: "UpstreamLike") -> "Upstream":
        """An :class:`Upstream` from ``"host"``, ``"host:port"``, ``"[v6]:port"``,
        an address or ``netimps.Host`` (port 69 unless its text has one),
        ``(host, port)`` or an :class:`Upstream`.

        :raises TypeError: a host that is none of those.
        :raises ValueError: host text netimps cannot read, or a bad port.
        """
        if isinstance(value, Upstream):
            return value if isinstance(value, cls) else cls(value.host, value.port)
        if isinstance(value, tuple):
            return cls(value[0], value[1])
        from netimps import split_host

        host, port = split_host(value, default_port=69)
        return cls(host, port or 69)


UpstreamLike = Union[Upstream, "HostLike", Tuple["HostLike", int]]
RouteFunction = Callable[[RequestPacket, TFTPRequestContext], Optional[UpstreamLike]]


def by_subnet(
    table: Union[Dict[IPNetworkLike, UpstreamLike], Sequence[Union[str, Tuple[IPNetworkLike, UpstreamLike]]]],
) -> RouteFunction:
    """Route by the client's address: ``{"10.1.0.0/16": "10.1.0.5", ...}``.

    Keys are anything ``netimps.parse(..., IPNetwork)`` takes: CIDR strings,
    ``ipaddress`` networks, interfaces (their network) or addresses (a /32 or
    /128). The most specific (longest prefix) matching network wins. A v4
    client seen as ``::ffff:a.b.c.d`` matches v4 networks. A sequence may hold
    ``"CIDR=HOST[:PORT]"`` text in place of a pair.

    :raises TFTPValueError: text without ``=`` or without a target.
    """
    from netimps import IPNetwork, parse

    networks = sorted(
        ((parse(net, IPNetwork), Upstream.parse(target)) for net, target in _items(table)),
        key=lambda pair: pair[0].prefixlen,
        reverse=True,
    )

    def route(request: RequestPacket, context: TFTPRequestContext) -> Optional[Upstream]:
        from netimps import unmap

        address = unmap(context.peer[0])
        for network, target in networks:
            if address.version == network.version and address in network:
                return target
        return None

    return route


def by_prefix(
    table: Union[Dict[str, UpstreamLike], Sequence[Union[str, Tuple[str, UpstreamLike]]]],
) -> RouteFunction:
    """Route by filename prefix: ``{"windows/": "wds.lan", "": "default.lan"}``.

    The longest matching prefix wins; leading ``/`` and ``\\`` are ignored
    and ``\\`` counts as ``/``. A sequence may hold ``"PREFIX=HOST[:PORT]"``
    text in place of a pair.

    :raises TFTPValueError: text without ``=`` or without a target.
    """
    prefixes = sorted(
        ((p.replace("\\", "/").lstrip("/"), Upstream.parse(t)) for p, t in _items(table)),
        key=lambda x: -len(x[0]),
    )

    def route(request: RequestPacket, context: TFTPRequestContext) -> Optional[Upstream]:
        name = request.filename.replace("\\", "/").lstrip("/")
        for prefix, target in prefixes:
            if name.startswith(prefix):
                return target
        return None

    return route


def by_interface(table: Dict[Union[int, Interface, HostLike], UpstreamLike]) -> RouteFunction:
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
            by_index[key] = Upstream.parse(target)
        elif isinstance(key, Interface):
            by_index[key.index] = Upstream.parse(target)
        else:
            address = Host(key).ip()  # an ipaddress interface counts as its address
            if address is None:
                raise ValueError("by_interface: cannot resolve %r" % (key,))
            by_address[unmap(address)] = Upstream.parse(target)

    def route(request: RequestPacket, context: TFTPRequestContext) -> Optional[Upstream]:
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
        self.default = Upstream.parse(default) if default is not None else None

    def __call__(self, request: RequestPacket, context: TFTPRequestContext) -> Optional[Upstream]:
        for route in self.routes:
            target = route(request, context)
            if target is not None:
                return Upstream.parse(target)
        return self.default
