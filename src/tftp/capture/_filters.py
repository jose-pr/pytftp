"""A small filter language for packet events, in the spirit of pydhcp's.

Clauses ``key=value`` joined by ``and``; a comma inside a value means "any
of"; ``key!=value`` negates::

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
import ipaddress
import re
from typing import Any, Callable, List, Optional, Tuple

from ..exceptions import CaptureFilterError
from ._events import PacketEvent

__all__ = ["EventPredicate", "compile_filter", "CaptureFilterError", "FILTER_KEYS"]

EventPredicate = Callable[[PacketEvent], bool]
FILTER_KEYS = ("op", "host", "src", "dst", "port", "file", "block", "code", "session", "leg", "direction")
_AND = re.compile(r"\s+and\s+", re.IGNORECASE)


def _address_matcher(text: str) -> Callable[[Tuple[Any, ...]], bool]:
    """``10.0.0.0/8``, ``10.0.0.5``, ``10.0.0.5:69``, ``[::1]:69`` or ``:69``."""
    host, port = text, None
    if text.startswith(":"):
        host, port = "", text[1:]
    elif text.startswith("["):
        end = text.find("]")
        host, rest = text[1:end], text[end + 1 :]
        port = rest[1:] if rest.startswith(":") else None
    elif text.count(":") == 1:
        host, port = text.split(":")
    wanted_port = None
    if port:
        if not port.isdigit():
            raise CaptureFilterError("bad port in %r" % text)
        wanted_port = int(port)
    network = None
    if host:
        try:
            network = ipaddress.ip_network(host, strict=False)
        except ValueError as exc:
            raise CaptureFilterError("bad address in %r" % text) from exc

    def match(endpoint: Tuple[Any, ...]) -> bool:
        if not endpoint:
            return False
        if wanted_port is not None and endpoint[1] != wanted_port:
            return False
        if network is None:
            return True
        try:
            address = ipaddress.ip_address(str(endpoint[0]).split("%", 1)[0])
        except ValueError:
            return False
        if address.version == 6 and address.ipv4_mapped is not None and network.version == 4:
            address = address.ipv4_mapped
        return address.version == network.version and address in network

    return match


def _clause(key: str, values: List[str]) -> EventPredicate:
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
            raise CaptureFilterError("port must be a number") from exc
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
            raise CaptureFilterError("%s must be a number" % key) from exc
        if key == "block":
            return lambda e: e.block in numbers
        return lambda e: e.opcode == 5 and len(e.data) >= 4 and ((e.data[2] << 8) | e.data[3]) in numbers
    if key in ("session", "leg", "direction"):
        wanted = set(values)
        return lambda e: getattr(e, key) in wanted
    raise CaptureFilterError("unknown filter key %r (known: %s)" % (key, ", ".join(FILTER_KEYS)))


def compile_filter(text: Optional[str]) -> EventPredicate:
    """A predicate over :class:`PacketEvent`; empty or ``None`` matches everything."""
    if not text or not text.strip():
        return lambda event: True
    predicates: List[EventPredicate] = []
    for raw in _AND.split(text.strip()):
        clause = raw.strip()
        negate = "!=" in clause
        key, sep, value = clause.partition("!=" if negate else "=")
        key = key.strip().lower()
        if not sep or not key or not value.strip():
            raise CaptureFilterError("expected key=value, got %r" % clause)
        values = [v.strip() for v in value.split(",") if v.strip()]
        predicate = _clause(key, values)
        predicates.append((lambda p: lambda e: not p(e))(predicate) if negate else predicate)
    return lambda event: all(p(event) for p in predicates)
