"""Socket buffer sizing for windowed transfers -- TFTP's arithmetic over netimps."""

from __future__ import annotations

import ipaddress
import socket
from typing import Any, Dict, Optional, Tuple, Union

__all__ = ["fit_window", "sockaddr", "same_host", "local_towards"]

#: Upper bound for buffer growth; the kernel may grant less (Linux rmem_max).
_MAX_BUFFER = 8 << 20


def fit_window(sock: socket.socket, blksize: int, windowsize: int) -> Tuple[int, int]:
    """Grow the socket buffers to hold two windows of DATA; returns what was granted.

    Default UDP buffers are small (64 KiB on Windows), so a window of large
    blocks overflows them and the tail of every window is dropped, costing a
    full timeout each time. The kernel may grant less than asked; netimps
    logs that shortfall, and the grant is returned.
    """
    if windowsize <= 1 and blksize <= 8192:
        return (0, 0)
    from netimps import set_buffer_size

    want = min(2 * max(windowsize, 1) * (blksize + 64), _MAX_BUFFER)
    try:
        granted = set_buffer_size(sock, receive=want, send=want)
    except OSError:
        return (0, 0)
    return granted


def local_towards(sock: socket.socket, peer: Tuple, cache: "Optional[Dict[Tuple, Tuple]]" = None) -> Tuple:
    """``sock``'s address as ``peer`` sees it: ``getsockname()``, with a wildcard host replaced.

    A socket bound to the wildcard has no address of its own; the one the
    route to ``peer`` picks is (netimps ``get_source_ip``), as plain IPv4 for a
    mapped address. ``cache`` remembers the answer per (socket address, peer).
    """
    import ipaddress

    local = sock.getsockname()
    try:
        if not ipaddress.ip_address(local[0].partition("%")[0]).is_unspecified:
            return local
        zone = peer[3] if len(peer) > 3 and peer[3] else 0
        key = (local[0], local[1], peer[0], zone)
        if cache is not None and key in cache:
            return cache[key]
        from netimps import get_source_ip, unmap

        dst = "%s%%%d" % (str(unmap(peer[0].partition("%")[0])), zone) if zone else str(unmap(peer[0]))
        source = get_source_ip(dst, peer[1] or 80, ipv6=":" in dst)
        if source is None:
            return local
        source = unmap(source)
        found = (str(source), local[1]) if source.version == 4 else (str(source),) + tuple(local[1:])
        if cache is not None:
            if len(cache) >= 256:
                cache.clear()
            cache[key] = found
        return found
    except (OSError, ValueError, TypeError, IndexError):
        return local


def _zone_index(zone: str) -> int:
    """A zone written as a number or, where the host knows it, as an interface name."""
    if zone.isascii() and zone.isdigit():
        return int(zone)
    return socket.if_nametoindex(zone)


def sockaddr(address: Union[ipaddress.IPv4Address, ipaddress.IPv6Address], port: int) -> Tuple[Any, ...]:
    """The destination to send to: ``(host, port)``, or for IPv6 ``(host, port, 0, scope_id)``.

    ``address`` is an ``ipaddress`` object. A zone (``fe80::1%10``) becomes the
    scope id as a number, which every platform and both asyncio loops accept,
    where the zone in the host text is refused by some of them.
    """
    if address.version == 4:
        return (str(address), port)
    host, _, zone = str(address).partition("%")
    return (host, port, 0, _zone_index(zone) if zone else 0)


def _identity(addr: Tuple) -> Tuple:
    """``(packed address, scope id)`` of a socket address, however its text is spelt."""
    import ipaddress

    host = addr[0]
    scope = addr[3] if len(addr) > 3 else 0
    host, _, zone = host.partition("%")
    if zone and not scope:
        try:
            scope = _zone_index(zone)
        except (OSError, ValueError):
            pass
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return (host, scope)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (ip.packed, scope)


def same_host(a: Tuple, b: Tuple) -> bool:
    """Whether two socket addresses name one host, whatever the port.

    Compared by packed address, so ``::ffff:10.0.0.1`` equals ``10.0.0.1``
    and no spelling of the text matters. Scope ids are compared when both
    addresses carry one: a receiver reports a link-local sender with its
    scope id and never in the text, while one that names a server by its bare
    address has none, and an absent scope id says nothing about the link
    (the address still has to match).
    """
    host_a, scope_a = _identity(a)
    host_b, scope_b = _identity(b)
    if host_a != host_b:
        return False
    return not scope_a or not scope_b or scope_a == scope_b
