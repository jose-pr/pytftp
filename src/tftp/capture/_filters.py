"""A small filter language for packet events.

The grammar is pktcap's (``pktcap.parse_capture_filter``): clauses ``key=value``
joined by ``and``, a comma inside a value meaning "any of", ``key!=value``
negating; there is no ``or``. What each key means is this module's::

    op=RRQ,WRQ and host=10.0.0.0/8
    file=*.efi and session!=c3
    op=ERROR and code=1,2
    src=:69 and dst=192.0.2.0/24

Keys:

=============  ==============================================================
``op``         opcode name: RRQ WRQ DATA ACK ERROR OACK
``host``       either end's address; an address or a CIDR network
``src``        the sender (address, CIDR, ``addr:port``, or ``:port``)
``dst``        the receiver (same forms)
``port``       either end's port
``file``       requested filename, shell-style pattern (requests only)
``block``      DATA/ACK block number
``code``       ERROR code
``session``    transfer id
``leg``        relay side: client, upstream
``direction``  in, out, seen
=============  ==============================================================
"""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING, Any, Callable, Optional, Sequence, Tuple

from ._events import PacketEvent

if TYPE_CHECKING:
    from pktcap import FilterClause

__all__ = ["EventPredicate", "compile_filter", "FILTER_KEYS"]

EventPredicate = Callable[[PacketEvent], bool]
FILTER_KEYS = ("op", "host", "src", "dst", "port", "file", "block", "code", "session", "leg", "direction")


def _port(text: str, whole: str) -> int:
    if not (text.isascii() and text.isdigit()) or int(text) > 65535:
        raise ValueError("bad port in %r" % whole)
    return int(text)


def _address_matcher(text: str) -> Callable[[Tuple[Any, ...]], bool]:
    """``10.0.0.0/8``, ``10.0.0.5``, ``10.0.0.5:69``, ``[::1]:69`` or ``:69``."""
    from netimps import IPNetwork, parse, split_host, split_zone, unmap

    wanted_port: Optional[int] = None
    network: Optional[IPNetwork] = None
    try:
        try:
            network = parse(text, IPNetwork)  # an address, or a network
        except ValueError:
            if text.startswith(":"):
                wanted_port = _port(text[1:], text)
            else:
                host, wanted_port = split_host(text)  # an address with a port
                network = parse(host, IPNetwork)
    except ValueError as exc:
        raise ValueError("bad address in %r" % text) from exc

    def match(endpoint: Tuple[Any, ...]) -> bool:
        if not endpoint:
            return False
        if wanted_port is not None and endpoint[1] != wanted_port:
            return False
        if network is None:
            return True
        try:
            host = split_zone(str(endpoint[0]))[0]
            address = unmap(host) if network.version == 4 else parse(host)
        except ValueError:
            return False
        return address.version == network.version and address in network

    return match


def _clause(key: str, values: Sequence[str]) -> EventPredicate:
    if key == "op":
        names = {v.upper() for v in values}
        return lambda e: e.opcode_name in names
    if key in ("host", "src", "dst"):
        matchers = [_address_matcher(v) for v in values]
        if key == "src":
            return lambda e: any(m(e.source) for m in matchers)
        if key == "dst":
            return lambda e: any(m(e.destination) for m in matchers)
        return lambda e: any(m(e.source) or m(e.destination) for m in matchers)
    if key == "port":
        try:
            ports = {int(v) for v in values}
        except ValueError as exc:
            raise ValueError("port must be a number") from exc
        return lambda e: bool(e.source and e.source[1] in ports) or bool(
            e.destination and e.destination[1] in ports
        )
    if key == "file":

        def file_match(e: PacketEvent) -> bool:
            if e.opcode not in (1, 2):
                return False
            try:
                name = e.decode().filename
            except ValueError:
                return False
            return any(fnmatch.fnmatchcase(name, v) for v in values)

        return file_match
    if key in ("block", "code"):
        try:
            numbers = {int(v) for v in values}
        except ValueError as exc:
            raise ValueError("%s must be a number" % key) from exc
        if key == "block":
            return lambda e: e.block in numbers
        return lambda e: e.opcode == 5 and len(e.data) >= 4 and ((e.data[2] << 8) | e.data[3]) in numbers
    if key in ("session", "leg", "direction"):
        wanted = set(values)
        return lambda e: getattr(e, key) in wanted
    raise ValueError("unknown filter key %r (known: %s)" % (key, ", ".join(FILTER_KEYS)))


def _build(clause: "FilterClause") -> EventPredicate:
    return _clause(clause.key.lower(), clause.values)


def compile_filter(text: Optional[str]) -> EventPredicate:
    """A predicate over :class:`PacketEvent`; empty or ``None`` matches everything.

    ``pktcap.CaptureFilterError`` (a ``ValueError``) for an expression that does
    not parse, an unknown key or a value that does not convert; it names the
    clause.
    """
    from pktcap import compile_capture_filter

    return compile_capture_filter(text, _build)
