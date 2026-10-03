"""The listening socket: where requests arrive, and what is known about each."""

from __future__ import annotations

import errno
import socket
from typing import TYPE_CHECKING, Any, NamedTuple, Optional, Tuple

if TYPE_CHECKING:
    from netimps import Host, IPAddressLike

from ..packet import encode_error
from .session import PortRange, bind_transfer

__all__ = ["Listener", "Arrival"]

_RECV_SIZE = 65536


class Arrival(NamedTuple):
    """One datagram on the listening socket.

    ``local`` is the address it was sent to and ``ifindex`` the interface it
    arrived on, when the platform reports them (pktinfo); else ``None``/0.
    ``datagram`` is netimps' ``Datagram`` and ``interface`` its resolved
    ``Interface`` (or ``None``).
    """

    data: bytes
    sender: Tuple[Any, ...]
    local: Optional[str]
    ifindex: int
    datagram: Any = None
    interface: Any = None


def _bind(host: "IPAddressLike | Host | None", port: int) -> socket.socket:
    from netimps import bind, get_ip, is_wildcard, normalize_host

    # netimps' defaults keep the port exclusive for a datagram socket on every
    # platform (no SO_REUSEADDR on POSIX, SO_EXCLUSIVEADDRUSE on Windows).
    plain = {"connreset": False}
    if host:
        host = normalize_host(host)[0]  # "[::1]" -> "::1"
    address = get_ip(host) if host else None
    if not host or (is_wildcard(host) and address is not None and address.version == 6):
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
        if address is None:
            raise socket.gaierror("cannot resolve %r" % (host,))
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        sock = bind(host, port, family=family, **plain)
    return sock


class Listener:
    """The server's well-known-port socket.

    :param pktinfo: report each request's destination address, through
        :class:`netimps.UdpEndpoint`, where the platform allows it; replies
        then leave from that address.
    """

    def __init__(self, host: "IPAddressLike | Host | None", port: int, pktinfo: bool = True) -> None:
        self.sock = _bind(host, port)
        self.family = self.sock.family
        try:
            self.v6only = self.family == socket.AF_INET6 and bool(
                self.sock.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY)
            )
        except (AttributeError, OSError):
            self.v6only = True
        from netimps import is_wildcard

        bound = self.sock.getsockname()[0]
        #: The listening address, or ``None`` when it is a wildcard.
        self.host: Optional[str] = None if is_wildcard(bound) else bound
        from netimps import UdpEndpoint

        self.endpoint = UdpEndpoint(self.sock, pktinfo=pktinfo)
        self.sock.setblocking(False)

    @property
    def supports_pktinfo(self) -> bool:
        return self.endpoint.supports_pktinfo

    @property
    def dual_stack(self) -> bool:
        return self.family == socket.AF_INET6 and not self.v6only

    def recv(self) -> Optional[Arrival]:
        """The next datagram, or ``None`` when none is waiting."""
        try:
            # The interface lookup is cached per endpoint by netimps.
            datagram = self.endpoint.recv(_RECV_SIZE)
        except (BlockingIOError, InterruptedError):
            return None
        except OSError:
            # Something unreadable (an ICMP error surfacing): skip it.
            return Arrival(b"", ("", 0), None, 0)
        return self.arrival(datagram)

    @staticmethod
    def arrival(datagram: Any) -> Arrival:
        """An :class:`Arrival` from a netimps ``Datagram``."""
        local = datagram.local_address
        return Arrival(
            datagram.data,
            datagram.sender,
            None if local is None else str(local),
            datagram.interface_index,
            datagram,
            datagram.interface,
        )

    @staticmethod
    def is_broadcast(arrival: Arrival) -> bool:
        """Sent to a broadcast (limited or subnet) or multicast address (RFC 1123 4.2.3.4)."""
        datagram = arrival.datagram
        local = None if datagram is None else datagram.local_address
        if local is None:
            return False  # without pktinfo every request looks unicast
        from netimps import is_broadcast, is_multicast, unmap

        # unmap: netimps.is_multicast misses a v4-mapped group before Python 3.13
        # (netimps finding 2026-10-03_reply_socket_v4_client_on_dual_stack_listener, item 3).
        return is_multicast(unmap(local)) or is_broadcast(local, arrival.interface)

    def reply_socket(
        self, arrival: Arrival, ports: "Optional[PortRange]" = None
    ) -> Tuple[socket.socket, Tuple[Any, ...]]:
        """A non-blocking socket for one transfer, bound to the address the
        request was sent to (``UdpEndpoint.reply_socket``), and the peer to
        send to in that socket's family.

        With ``ports`` it takes a free port from the range;
        :class:`netimps.AddressInUseError` when none is free.
        """
        from netimps import unmap

        sender = arrival.sender
        plain = unmap(sender[0])
        v4_via_v6 = self.family == socket.AF_INET6 and plain.version == 4
        if v4_via_v6 and arrival.local is None:
            # Without pktinfo netimps answers a v4-mapped sender from a v6-only
            # socket, which cannot reach it (netimps finding
            # 2026-10-03_reply_socket_v4_client_on_dual_stack_listener).
            sock = bind_transfer("0.0.0.0", socket.AF_INET, ports)
        else:
            sock = self.endpoint.reply_socket(arrival.datagram, port=ports.ordered() if ports else 0)
            if ports is not None:
                ports.taken(sock.getsockname()[1])
            sock.setblocking(False)
        # A v4 client answered from a plain v4 socket is addressed as v4.
        peer = (str(plain), sender[1]) if v4_via_v6 and sock.family == socket.AF_INET else sender
        return sock, peer

    def reply_error(self, sender: Tuple[Any, ...], code: int, message: str) -> None:
        """Answer a request with an ERROR from the listening socket itself."""
        try:
            self.sock.sendto(encode_error(code, message), sender)
        except OSError:
            pass

    def close(self) -> None:
        self.endpoint.close()  # stops netimps' async notifier too, then the socket
