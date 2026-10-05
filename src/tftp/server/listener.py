"""The listening socket: where requests arrive, and what is known about each."""

from __future__ import annotations

import errno
import socket
from typing import TYPE_CHECKING, Any, NamedTuple, Optional, Tuple

if TYPE_CHECKING:
    from netimps import Host, InterfaceLike, IPAddressLike

from ..packet.codec import _encode_error
from .session import PortAllocator

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


def _bind_interface(
    host: "IPAddressLike | Host | None", port: int, interface: "InterfaceLike"
) -> socket.socket:
    """Listen on ``interface``'s address: IPv4 when it has one, unless ``host`` is a
    wildcard naming the family (``"0.0.0.0"`` or ``"::"``)."""
    from netimps import Host, bind, is_wildcard

    plain = {"interface": interface}
    if not host:
        try:
            return bind("", port, family=socket.AF_INET, **plain)
        except ValueError:  # no IPv4 address on that adapter
            return bind("", port, family=socket.AF_INET6, **plain)
    address = Host(host).ip()
    if address is None or not is_wildcard(host):
        raise ValueError(
            "give host or interface, not both (host may be '0.0.0.0' or '::' to pick the family)"
        )
    return bind("", port, family=socket.AF_INET6 if address.version == 6 else socket.AF_INET, **plain)


def _bind(host: "IPAddressLike | Host | None", port: int, interface: "InterfaceLike" = None) -> socket.socket:
    from netimps import Host, bind, is_wildcard, split_host

    if interface is not None:
        return _bind_interface(host, port, interface)
    # netimps' defaults keep the port exclusive for a datagram socket on every
    # platform (no SO_REUSEADDR on POSIX, SO_EXCLUSIVEADDRUSE on Windows) and
    # stop Windows reporting an ICMP port-unreachable as a receive error.
    if host:
        host = split_host(host)[0]  # "[::1]" -> "::1"
    address = Host(host).ip() if host else None
    if not host or (is_wildcard(host) and address is not None and address.version == 6):
        try:
            sock = bind(
                "::",
                port,
                # Explicit: Windows defaults to v6-only, Linux to dual-stack.
                options=[(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)],
            )
        except OSError as exc:
            if exc.errno in (errno.EADDRINUSE, errno.EACCES):
                raise
            # No IPv6 on this host: IPv4 alone.
            sock = bind("0.0.0.0", port)
    else:
        if address is None:
            raise socket.gaierror("cannot resolve %r" % (host,))
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        sock = bind(host, port, family=family)
    return sock


class Listener:
    """The server's well-known-port socket.

    :param pktinfo: report each request's destination address, through
        :class:`netimps.UDPEndpoint`, where the platform allows it; replies
        then leave from that address.
    :param interface: listen on this adapter's address (see ``TFTPServer``).
    """

    def __init__(
        self,
        host: "IPAddressLike | Host | None",
        port: int,
        pktinfo: bool = True,
        interface: "InterfaceLike" = None,
    ) -> None:
        self.sock = _bind(host, port, interface)
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
        from netimps import UDPEndpoint

        self.endpoint = UDPEndpoint(self.sock, pktinfo=pktinfo)
        self.sock.setblocking(False)

    @property
    def has_pktinfo(self) -> bool:
        return self.endpoint.has_pktinfo

    @property
    def is_dual_stack(self) -> bool:
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
        local = datagram.destination
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
        local = None if datagram is None else datagram.destination
        if local is None:
            return False  # without pktinfo every request looks unicast
        from netimps import is_broadcast, is_multicast

        # cache=True only matters when the arrival interface did not resolve:
        # then at most one adapter listing per second, not one per request.
        return is_multicast(local) or is_broadcast(local, arrival.interface, cache=True)

    def reply_socket(
        self, arrival: Arrival, ports: "Optional[PortAllocator]" = None
    ) -> Tuple[socket.socket, Tuple[Any, ...]]:
        """A non-blocking socket for one transfer, bound to the address the
        request was sent to (``UDPEndpoint.reply_socket``), and the peer to
        send to in that socket's family.

        With ``ports`` it takes a free port from the range;
        :class:`netimps.AddressInUseError` when none is free.
        """
        sock = self.endpoint.reply_socket(arrival.datagram, port=ports.ordered() if ports else 0)
        if ports is not None:
            ports.taken(sock.getsockname()[1])
        sock.setblocking(False)
        # The sender in the family netimps chose: a v4 client of a dual-stack
        # listener is answered from a plain v4 socket, at its plain address.
        return sock, arrival.datagram.reply_address

    def reply_error(self, sender: Tuple[Any, ...], code: int, message: str) -> None:
        """Answer a request with an ERROR from the listening socket itself."""
        try:
            self.sock.sendto(_encode_error(code, message), sender)
        except OSError:
            pass

    def close(self) -> None:
        self.endpoint.close()  # stops netimps' async notifier too, then the socket
