"""The listening sockets: where requests arrive, and what is known about each."""

from __future__ import annotations

import errno
import socket
from typing import TYPE_CHECKING, Any, Callable, List, NamedTuple, Optional, Tuple

if TYPE_CHECKING:
    from netimps import Datagram, Interface, ListenLike, UDPEndpoint

from .._loggers import SERVER as log
from ..packet._codec import _encode_error
from ._session import PortAllocator

__all__ = ["Listener", "Arrival"]

_RECV_SIZE = 65536
#: One datagram in this many that a limited endpoint refuses is logged (the first one always is).
_DROP_LOG_EVERY = 256
#: Tries at finding one port that both families have free, for ``port=0``.
_PORT_TRIES = 5


class Arrival(NamedTuple):
    """One datagram on a listening socket.

    ``local`` is the address it was sent to and ``ifindex`` the interface it
    arrived on, when known; else ``None``/0. ``datagram`` is netimps'
    ``Datagram``, ``interface`` its resolved ``Interface`` (or ``None``) and
    ``endpoint`` the ``UDPEndpoint`` it arrived on, which answers it.
    """

    data: bytes
    sender: Tuple[Any, ...]
    local: Optional[str]
    ifindex: int
    datagram: Optional[Datagram] = None
    interface: Optional[Interface] = None
    endpoint: Optional[UDPEndpoint] = None


def _refused(exc: OSError) -> bool:
    """A bind failure that is not a host without IPv6: a taken port or a forbidden one."""
    from netimps import AddressInUseError

    return isinstance(exc, (AddressInUseError, PermissionError)) or exc.errno in (
        errno.EADDRINUSE,
        errno.EACCES,
    )


def _close_all(endpoints: "List[UDPEndpoint]") -> None:
    for endpoint in endpoints:
        try:
            endpoint.close()
        except Exception:  # cleanup must not mask the error that called for it
            log.debug("closing a listening socket failed", exc_info=True)


def _every_address(port: int) -> "List[UDPEndpoint]":
    """Both families' wildcards on one port; the IPv6 one is left out on a host with no IPv6."""
    from netimps import bind_listen

    last: Optional[OSError] = None
    for _ in range(_PORT_TRIES if port == 0 else 1):
        opened = list(bind_listen("*", port))
        try:
            # A port the kernel chose for the first is the port of the second.
            opened.extend(bind_listen("::", opened[0].socket.getsockname()[1] if port == 0 else port))
        except OSError as exc:
            if not _refused(exc):
                return opened  # no IPv6 on this host: IPv4 alone
            _close_all(opened)
            last = exc
            continue
        return opened
    assert last is not None
    raise last


class Listener:
    """The well-known-port sockets of a server: one for each address a listen specification names.

    :param listen: a netimps listen specification; ``None`` is every address of
        both families (``"*"`` and ``"::"``).
    :param port: the port a binding that names none gets.
    :param pktinfo: report each request's destination address, through
        :class:`netimps.UDPEndpoint`, where the platform allows it; replies
        then leave from that address. A socket limited to adapters always
        reports it: its limit is checked on the arrival interface.
    """

    def __init__(self, listen: "ListenLike", port: int, pktinfo: bool = True) -> None:
        from netimps import UDPEndpoint, bind_listen, is_wildcard

        opened = _every_address(port) if listen is None else list(bind_listen(listen, port))
        try:
            endpoints: "List[UDPEndpoint]" = []
            for endpoint in opened:
                want = pktinfo or bool(endpoint.interfaces)
                if endpoint.has_pktinfo != want:
                    # An address-bound socket asks for no packet info and a wildcard always does.
                    endpoint = UDPEndpoint(endpoint.socket, pktinfo=want, interfaces=endpoint.interfaces)
                endpoint.socket.setblocking(False)
                endpoints.append(endpoint)
            #: The endpoints, in the order of the specification.
            self.endpoints: Tuple[UDPEndpoint, ...] = tuple(endpoints)
            #: Where each one is bound.
            self.addresses: Tuple[Tuple[Any, ...], ...] = tuple(e.socket.getsockname() for e in endpoints)
            self._bound = tuple(
                None if is_wildcard(address[0]) else address[0].partition("%")[0]
                for address in self.addresses
            )
        except BaseException:
            _close_all(opened)
            raise
        #: Datagrams a limited endpoint refused, since the listener opened.
        self.not_admitted = 0
        self._next = 0

    @property
    def has_pktinfo(self) -> bool:
        """Every endpoint reports its requests' destination."""
        return all(endpoint.has_pktinfo for endpoint in self.endpoints)

    def receive(self, endpoint: UDPEndpoint) -> Optional[Arrival]:
        """The next admitted datagram on ``endpoint``, or ``None`` when none is waiting.

        A datagram the endpoint does not admit is dropped and counted.
        """
        while True:
            try:
                # The interface lookup is cached per endpoint by netimps.
                datagram = endpoint.recv(_RECV_SIZE)
            except (BlockingIOError, InterruptedError):
                return None
            except OSError:
                # Something unreadable (an ICMP error surfacing): skip it.
                return Arrival(b"", ("", 0), None, 0)
            arrival = self.arrival(datagram, endpoint)
            if arrival is not None:
                return arrival

    def recv(self) -> Optional[Arrival]:
        """The next admitted datagram on any endpoint, or ``None`` when none is waiting.

        The endpoints are taken in turn, so a busy one does not starve the rest.
        """
        endpoints = self.endpoints
        count = len(endpoints)
        for step in range(count):
            index = (self._next + step) % count
            arrival = self.receive(endpoints[index])
            if arrival is not None:
                self._next = (index + 1) % count
                return arrival
        return None

    def arrival(self, datagram: Datagram, endpoint: UDPEndpoint) -> Optional[Arrival]:
        """An :class:`Arrival` from a datagram of ``endpoint``, or ``None`` (counted) when it does not admit it."""
        if endpoint.interfaces and not endpoint.admits(datagram):
            self.not_admitted += 1
            if self.not_admitted % _DROP_LOG_EVERY == 1:
                log.debug(
                    "dropped a datagram from %s that arrived on interface %d, outside the limit (%d so far)",
                    datagram.sender[:2],
                    datagram.interface_index,
                    self.not_admitted,
                )
            return None
        local = datagram.destination
        if local is None:
            # Without packet info an address-bound socket still knows where it was addressed.
            local = self._bound[self.endpoints.index(endpoint)]
        return Arrival(
            datagram.data,
            datagram.sender,
            None if local is None else str(local),
            datagram.interface_index,
            datagram,
            datagram.interface,
            endpoint,
        )

    @staticmethod
    def is_broadcast(arrival: Arrival) -> bool:
        """Sent to a broadcast (limited or subnet) or multicast address (RFC 1123 4.2.3.5)."""
        datagram = arrival.datagram
        local = None if datagram is None else datagram.destination
        if local is None:
            return False  # without pktinfo every request looks unicast
        from netimps import is_broadcast, is_multicast

        # cache=True only matters when the arrival interface did not resolve:
        # then at most one adapter listing per second, not one per request.
        return is_multicast(local) or is_broadcast(local, arrival.interface, cache=True)

    @staticmethod
    def socket_of(arrival: Arrival) -> socket.socket:
        """The socket ``arrival`` came in on."""
        return Listener.endpoint_of(arrival).socket

    @staticmethod
    def endpoint_of(arrival: Arrival) -> UDPEndpoint:
        endpoint = arrival.endpoint
        if endpoint is None:
            raise ValueError("an arrival that carries no endpoint has nothing to reply from")
        return endpoint

    def reply_socket(
        self, arrival: Arrival, ports: "Optional[PortAllocator]" = None
    ) -> Tuple[socket.socket, Tuple[Any, ...]]:
        """A non-blocking socket for one transfer, bound to the address the
        request was sent to (``UDPEndpoint.reply_socket``), and the peer to
        send to in that socket's family.

        With ``ports`` it takes a free port from the range;
        :class:`netimps.AddressInUseError` when none is free.
        """
        datagram = arrival.datagram
        if datagram is None:
            raise ValueError("an arrival that carries no datagram has no sender to reply to")
        sock = self.endpoint_of(arrival).reply_socket(datagram, port=ports.ordered() if ports else 0)
        if ports is not None:
            ports.taken(sock.getsockname()[1])
        sock.setblocking(False)
        return sock, datagram.reply_address

    def reply_error(self, arrival: Arrival, code: int, message: str) -> None:
        """Answer a request with an ERROR from the socket it arrived on."""
        try:
            self.endpoint_of(arrival).socket.sendto(_encode_error(code, message), arrival.sender)
        except OSError:
            pass

    async def serve(self, arrived: Callable[[Arrival], None], drain: int) -> None:
        """Pass every admitted arrival to ``arrived``, receiving on all endpoints at once.

        One task for each endpoint; the first to fail ends the rest and raises. At most
        ``drain`` datagrams are taken from one endpoint per wakeup.
        """
        import asyncio

        tasks = [
            asyncio.ensure_future(self._serve_on(endpoint, arrived, drain)) for endpoint in self.endpoints
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _serve_on(self, endpoint: UDPEndpoint, arrived: Callable[[Arrival], None], drain: int) -> None:
        # An ICMP error surfacing on the listener is skipped, as the synchronous
        # server does; closing the endpoint ends the loop quietly.
        async for datagram in endpoint.datagrams(_RECV_SIZE, on_error=lambda exc: isinstance(exc, OSError)):
            arrival = self.arrival(datagram, endpoint)
            if arrival is not None:
                arrived(arrival)
            # Whatever else is already queued on this endpoint, without another wait.
            for _ in range(drain - 1):
                arrival = self.receive(endpoint)
                if arrival is None:
                    break
                arrived(arrival)

    def close(self) -> None:
        """Close every endpoint (stopping netimps' async notifier too), whatever one of them raises."""
        failure: Optional[BaseException] = None
        for endpoint in self.endpoints:
            try:
                endpoint.close()
            except BaseException as exc:
                failure = failure or exc
        if failure is not None:
            raise failure

    async def aclose(self) -> None:
        """:meth:`close` for a coroutine: the reader threads are awaited, not joined on the loop."""
        failure: Optional[BaseException] = None
        for endpoint in self.endpoints:
            try:
                await endpoint.aclose()
            except BaseException as exc:
                failure = failure or exc
        if failure is not None:
            raise failure
