"""Socket plumbing shared by the client and the server."""

from __future__ import annotations

import ipaddress
import socket
import sys
from typing import Any, Optional, Tuple

__all__ = ["udp_socket", "disable_connreset", "resolve", "unmap", "is_wildcard", "fit_window"]

_WINDOWS = sys.platform == "win32"


def disable_connreset(sock: socket.socket) -> None:
    """Stop Windows reporting ICMP port-unreachable on later receives.

    Windows delivers it as ``ConnectionResetError`` on an *unconnected* UDP
    socket, aborting a receive loop over a packet the peer simply did not
    want; every other platform only reports it on connected sockets.
    """
    if _WINDOWS:
        ioctl = getattr(socket, "SIO_UDP_CONNRESET", None)
        if ioctl is not None:
            try:
                sock.ioctl(ioctl, False)
            except OSError:
                pass


def udp_socket(family: int, bind: Optional[Tuple[Any, ...]] = None) -> socket.socket:
    """A UDP socket of ``family``, bound to ``bind`` when given."""
    sock = socket.socket(family, socket.SOCK_DGRAM)
    try:
        disable_connreset(sock)
        if bind is not None:
            sock.bind(bind)
    except BaseException:
        sock.close()
        raise
    return sock


#: Upper bound for buffer growth; the kernel may cap lower (Linux rmem_max).
_MAX_BUFFER = 8 << 20


def fit_window(sock: socket.socket, blksize: int, windowsize: int) -> None:
    """Grow the socket buffers to hold two windows of DATA.

    Default UDP buffers are small (64 KiB on Windows), so a window of large
    blocks overflows them and the tail of every window is dropped, costing a
    full timeout each time. Best effort: the kernel may grant less.
    """
    if windowsize <= 1 and blksize <= 8192:
        return
    want = min(2 * max(windowsize, 1) * (blksize + 64), _MAX_BUFFER)
    for option in (socket.SO_RCVBUF, socket.SO_SNDBUF):
        try:
            if sock.getsockopt(socket.SOL_SOCKET, option) < want:
                sock.setsockopt(socket.SOL_SOCKET, option, want)
        except OSError:
            pass


def resolve(host: str, port: int, family: int = 0) -> Tuple[int, Tuple[Any, ...]]:
    """``(family, sockaddr)`` for a UDP peer; the first address wins.

    ``host`` may carry a port (``"[::1]:6969"``, ``"server:6969"``), which
    overrides ``port``.
    """
    from netimps import normalize_host

    host, given = normalize_host(host, port)
    infos = socket.getaddrinfo(host, given, family, socket.SOCK_DGRAM)
    if not infos:  # pragma: no cover - getaddrinfo raises instead
        raise OSError("cannot resolve %r" % host)
    fam, _, _, _, sockaddr = infos[0]
    return fam, sockaddr


def unmap(host: str) -> str:
    """``::ffff:a.b.c.d`` -> ``a.b.c.d``; anything else unchanged.

    Parsed rather than matched as text: before Python 3.13, ``ipaddress``
    writes a mapped address in hex (``::ffff:7f00:2``), not dotted form.
    """
    if not host.lower().startswith("::ffff:"):
        return host
    try:
        mapped = ipaddress.IPv6Address(host.split("%", 1)[0]).ipv4_mapped
    except ValueError:
        return host
    return host if mapped is None else str(mapped)


def is_wildcard(host: str) -> bool:
    if host in ("", "0.0.0.0", "::"):
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_unspecified
    except ValueError:
        return False
