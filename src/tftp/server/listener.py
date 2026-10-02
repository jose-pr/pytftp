"""The listening socket: where requests arrive, and what is known about each."""

from __future__ import annotations

import errno
import socket
import sys
from typing import Any, NamedTuple, Optional, Tuple

from .._sockets import disable_connreset, is_wildcard
from ..packet import encode_error

__all__ = ["Listener", "Arrival"]

_RECV_SIZE = 65536


class Arrival(NamedTuple):
    """One datagram on the listening socket.

    ``local`` is the address it was sent to and ``ifindex`` the interface it
    arrived on, when the platform reports them (pktinfo); else ``None``/0.
    """

    data: bytes
    sender: Tuple[Any, ...]
    local: Optional[str]
    ifindex: int


def _bind(host: str, port: int) -> socket.socket:
    from netimps import bind, normalize_host

    # netimps maps reuse_address to SO_EXCLUSIVEADDRUSE on Windows, which
    # stops another process taking the port over -- wanted. On POSIX it means
    # SO_REUSEADDR, which on a UDP socket lets a second server share the port
    # and silently take half the requests -- not wanted.
    plain = {"reuse_address": sys.platform == "win32"}
    if host in ("", "::"):
        try:
            sock = bind(
                "::",
                port,
                family=socket.AF_INET6,
                # Explicit: Windows defaults to v6-only, Linux to dual-stack.
                options=[(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)],
                **plain,
            )
        except OSError as exc:
            if exc.errno in (errno.EADDRINUSE, errno.EACCES):
                raise
            # No IPv6 on this host: IPv4 alone.
            sock = bind("0.0.0.0", port, family=socket.AF_INET, **plain)
    else:
        host = normalize_host(host)[0]
        family = socket.getaddrinfo(host, port, 0, socket.SOCK_DGRAM, 0, socket.AI_PASSIVE)[0][0]
        sock = bind(host, port, family=family, **plain)
    disable_connreset(sock)
    return sock


class Listener:
    """The server's well-known-port socket.

    :param pktinfo: report each request's destination address, through
        :class:`netimps.UdpEndpoint`, where the platform allows it.
    """

    def __init__(self, host: str, port: int, pktinfo: bool = True) -> None:
        self.sock = _bind(host, port)
        self.family = self.sock.family
        try:
            self.v6only = self.family == socket.AF_INET6 and bool(
                self.sock.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY)
            )
        except (AttributeError, OSError):
            self.v6only = True
        bound = self.sock.getsockname()[0]
        #: The listening address, or ``None`` when it is a wildcard.
        self.host: Optional[str] = None if is_wildcard(bound) else bound
        self._endpoint = None
        if pktinfo:
            # Imported here so a client-only user never loads it.
            from netimps import UdpEndpoint

            self._endpoint = UdpEndpoint(self.sock, pktinfo=True)
        self.sock.setblocking(False)

    @property
    def supports_pktinfo(self) -> bool:
        return self._endpoint is not None and self._endpoint.supports_pktinfo

    @property
    def dual_stack(self) -> bool:
        return self.family == socket.AF_INET6 and not self.v6only

    def recv(self) -> Optional[Arrival]:
        """The next datagram, or ``None`` when none is waiting."""
        endpoint = self._endpoint
        try:
            if endpoint is not None and endpoint.supports_pktinfo:
                datagram = endpoint.recv(_RECV_SIZE, resolve_interface=False)
                local = datagram.local_address
                return Arrival(
                    datagram.data,
                    datagram.sender,
                    None if local is None else str(local),
                    datagram.interface_index,
                )
            data, sender = self.sock.recvfrom(_RECV_SIZE)
            return Arrival(data, sender, None, 0)
        except (BlockingIOError, InterruptedError):
            return None
        except OSError:
            # Something unreadable (an ICMP error surfacing): skip it.
            return Arrival(b"", ("", 0), None, 0)

    def reply_error(self, sender: Tuple[Any, ...], code: int, message: str) -> None:
        """Answer a request with an ERROR from the listening socket itself."""
        try:
            self.sock.sendto(encode_error(code, message), sender)
        except OSError:
            pass

    def close(self) -> None:
        self.sock.close()
