"""The transparent relay's rules: what both of its drivers share, and nothing that waits.

There is no standard TFTP relay (nothing like DHCP's); this is an
application-level one in the RTEMS-proxy style:

    client:C --RRQ--> relay:69                (request, forwarded byte for byte)
    relay:U  --RRQ--> upstream:69             (from a fresh upstream-side port)
    upstream:S <-> relay:U                    (the upstream's TID, learned)
    relay:D  <-> client:C                     (a fresh client-side TID)

Datagrams cross unchanged, so options and extensions the library does not
understand still work end to end. To give each side its own block size or
window instead, terminate both sessions with
:class:`tftp.backends.UpstreamBackend`.

:class:`RelayBase` decides whether a request becomes a relayed transfer,
learns the upstream's TID, answers a stray, ends a transfer and reports it.
:class:`tftp.relay.TFTPRelay` (a ``selectors`` loop) and
:class:`tftp.relay.AsyncTFTPRelay` (asyncio) only wait for sockets and timers
and call it, so the two cannot drift apart. Every number an unauthenticated
peer can push against is here, once.
"""

from __future__ import annotations

import socket
import sys
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Tuple, Union

if TYPE_CHECKING:
    from netimps import HostLike, InterfaceLike

from .._sockets import local_towards, same_host, sockaddr
from ..capture._hook import HookGuard, guard
from ..capture._events import PacketEvent, new_session_id
from ..exceptions import TFTPDecodeError, TFTPError
from ..packet._enums import TFTPErrorCode, TFTPOpcode
from ..packet._codec import RequestPacket, decode
from ..packet._codec import _encode_error
from ..server._handler import TFTPRequestContext, is_coroutine_callable
from ..server._listener import Arrival
from ..server._policy import TFTPServerLimits
from ..server._session import PortAllocator, PortRangeLike, as_port_range, bind_transfer
from ._routing import Upstream, UpstreamLike
from ..server._stats import RELAY_COUNTERS, TFTPStats
from .._loggers import RELAY as log
from ._session import RelaySession, RelaySummary

__all__ = ["RelayBase", "RelayLegs"]

#: How often idle, lifetime and linger deadlines are checked.
_TICK = 0.25
#: Datagrams read per readiness event before yielding to the others.
_DRAIN = 64
#: How long a resolved upstream name is kept.
_RESOLVE_TTL = 60.0
#: Concurrent relayed transfers by default: two sockets each, so 500 sockets, as a server's default holds.
DEFAULT_MAX_SESSIONS = 250
#: ``select()`` on Windows watches 512 descriptors: two per transfer, and the listener and a wake socket.
_SELECT_MAX_SESSIONS = 255
IDLE_TIMEOUT = 30.0
MAX_DURATION = 3600.0
LINGER = 2.0


class RelayLegs:
    """The two sockets of one relayed transfer, and what its driver keeps for watching them.

    ``down`` faces the client, ``up`` the upstream; ``watch`` is the driver's own.
    """

    __slots__ = ("down", "up", "watch")

    def __init__(self, down: socket.socket, up: socket.socket) -> None:
        self.down = down
        self.up = up
        self.watch: Any = None


class RelayBase:
    """Configuration, admission, forwarding and reporting shared by the relay front ends.

    A front end provides ``_unwatch(session)``: stop receiving on the
    session's two sockets and close them.
    """

    _service = "relay"
    #: Whether ``route`` is an ``async def`` (the asyncio relay) or a plain function.
    _coroutine_route = False

    def __init__(
        self,
        route: Any,
        *,
        host: Optional[HostLike] = None,
        port: int = 69,
        idle_timeout: float = IDLE_TIMEOUT,
        max_duration: float = MAX_DURATION,
        linger: float = LINGER,
        upstream_src: Optional[HostLike] = None,
        limits: Optional[TFTPServerLimits] = None,
        max_sessions: Optional[int] = DEFAULT_MAX_SESSIONS,
        ignore_broadcast: bool = True,
        reply_from_request_address: bool = True,
        trace: Optional[Callable[[PacketEvent], Any]] = None,
        on_session_end: Optional[Callable[[RelaySummary], Any]] = None,
        port_range: Optional[PortRangeLike] = None,
        interface: InterfaceLike = None,
    ) -> None:
        self.route: Any
        if callable(route):
            if is_coroutine_callable(route) != self._coroutine_route:
                raise TypeError(
                    "%s takes %s route; %r is %s"
                    % (
                        type(self).__name__,
                        "a coroutine (async def)" if self._coroutine_route else "a plain",
                        route,
                        "a plain function" if self._coroutine_route else "a coroutine function",
                    )
                )
            self.route = route
        else:
            fixed = Upstream.parse(route)
            if self._coroutine_route:

                async def fixed_route(request: RequestPacket, context: TFTPRequestContext) -> Upstream:
                    return fixed

                self.route = fixed_route
            else:
                self.route = lambda request, context: fixed
        self.idle_timeout = idle_timeout
        self.max_duration = max_duration
        self.linger = linger
        self.upstream_src = upstream_src
        self.limits = limits or TFTPServerLimits()
        if sys.platform == "win32":
            if max_sessions is None:
                max_sessions = DEFAULT_MAX_SESSIONS
            elif max_sessions > _SELECT_MAX_SESSIONS:
                raise ValueError(
                    "max_sessions=%d is more than the %d transfers select() can watch on Windows "
                    "(two sockets each)" % (max_sessions, _SELECT_MAX_SESSIONS)
                )
        self.max_sessions = max_sessions
        self.ignore_broadcast = ignore_broadcast
        self.trace = trace
        self._trace_guard: Optional[HookGuard] = None
        self._local_cache: Dict[Tuple, Tuple] = {}
        self.on_session_end = on_session_end
        #: Ports for both of a transfer's sockets (:class:`PortRange`), or ``None``.
        self.port_range = as_port_range(port_range)
        self._ports = PortAllocator(self.port_range) if self.port_range is not None else None
        #: Counters since start (:class:`TFTPStats`).
        self.stats = TFTPStats(*RELAY_COUNTERS)
        self._listen_on = (host, port, reply_from_request_address, interface)
        self._listener: Any = None
        self._address: Optional[Tuple[Any, ...]] = None
        self._sessions: Dict[Tuple[str, int], RelaySession] = {}
        #: Requests whose route is still being asked: the asyncio relay's, empty on the blocking one.
        self._waiting: Dict[Tuple[str, int], Any] = {}
        self._resolved: Dict["HostLike", Tuple[Any, float]] = {}

    # -- properties -------------------------------------------------------------------

    @property
    def server_address(self) -> Optional[Tuple[Any, ...]]:
        """The bound listening address, or ``None`` before :meth:`bind`."""
        return self._address

    @property
    def has_pktinfo(self) -> Optional[bool]:
        """Replies come from the request's own destination address; ``None`` before :meth:`bind`."""
        return None if self._listener is None else self._listener.has_pktinfo

    @property
    def active_sessions(self) -> int:
        return len(self._sessions)

    def stats_snapshot(self) -> Dict[str, int]:
        """Every counter plus ``active``, for metrics."""
        snapshot = self.stats.snapshot()
        snapshot["active"] = len(self._sessions)
        return snapshot

    # -- what a front end provides ------------------------------------------------------

    def _unwatch(self, session: RelaySession) -> None:  # pragma: no cover - front ends
        raise NotImplementedError

    # -- admission -----------------------------------------------------------------------

    def _held(self) -> int:
        """What ``max_sessions`` bounds: transfers running and requests still waiting for a route."""
        return len(self._sessions) + len(self._waiting)

    def _refuse(self, sender: Tuple[Any, ...], code: int, message: str) -> None:
        self.stats.add("refused")
        self._listener.reply_error(sender, code, message)

    def _admit(
        self, arrival: Arrival, now: float
    ) -> Optional[Tuple[Tuple[str, int], RequestPacket, TFTPRequestContext]]:
        """What a datagram on the listening port asks for, or ``None`` when it was dropped, passed on or refused.

        Returns the client's key, the decoded request and its context: the
        caller routes it. A repeated request is passed to the upstream while
        that has not answered.
        """
        from netimps import unmap

        data, sender, local, ifindex = arrival[:4]
        if len(data) < 2 or data[0] != 0 or data[1] not in (TFTPOpcode.RRQ, TFTPOpcode.WRQ):
            return None
        if self.ignore_broadcast and self._listener.is_broadcast(arrival):
            return None
        key = (str(unmap(sender[0])), sender[1])
        existing = self._sessions.get(key)
        if existing is not None:
            if existing.upstream_tid is None:  # still unanswered: pass the retry on
                self._emit(existing, self._listener.sock, data, sender, "in", "client")
                self._send(existing, existing.driver.up, data, existing.upstream)
            return None
        if key in self._waiting:
            return None  # its route is still being asked
        self.stats.add("requests")
        if len(data) > self.limits.max_request_size:
            self._refuse(sender, TFTPErrorCode.ILLEGAL_OPERATION, "request too large")
            return None
        try:
            request = decode(data)
            assert isinstance(request, RequestPacket)
            self.limits.check(request)
        except TFTPDecodeError as exc:
            self._refuse(sender, TFTPErrorCode.ILLEGAL_OPERATION, str(exc))
            return None
        except TFTPError as exc:
            self._refuse(sender, exc.code, exc.message)
            return None
        if self.max_sessions is not None and self._held() >= self.max_sessions:
            self._refuse(sender, TFTPErrorCode.NOT_DEFINED, "relay busy")
            return None
        context = TFTPRequestContext(request, sender, local_address=local, interface_index=ifindex)
        context.interface = arrival.interface
        return key, request, context

    @staticmethod
    def _target(chosen: Optional[UpstreamLike]) -> Upstream:
        """The upstream a route chose; ``None`` is a refusal (ERROR 2)."""
        if chosen is None:
            raise TFTPError(TFTPErrorCode.ACCESS_VIOLATION, "no route")
        return Upstream.parse(chosen)

    def _refuse_route(self, context: TFTPRequestContext, sender: Tuple[Any, ...], exc: Exception) -> None:
        """A route (or the lookup of its answer) gave no upstream: the client is told why."""
        if isinstance(exc, TFTPError):
            log.info("%r refused: %s", context, exc)
            self._refuse(sender, exc.code, exc.message)
        else:
            log.error("route failed for %r", context, exc_info=exc)
            self._refuse(sender, TFTPErrorCode.NOT_DEFINED, "relay error")

    def _known(self, target: Upstream, now: float) -> Any:
        """The address a name was resolved to less than ``_RESOLVE_TTL`` ago, or ``None``."""
        cached = self._resolved.get(target.host)
        return cached[0] if cached is not None and cached[1] > now else None

    def _address_of(
        self, target: Upstream, address: Any, now: float, remember: bool = True
    ) -> Tuple[int, Tuple[Any, ...]]:
        """The socket family and address to forward to; a fresh answer is remembered."""
        if address is None:
            raise TFTPError(TFTPErrorCode.NOT_DEFINED, "upstream unresolvable")
        if remember:
            self._resolved[target.host] = (address, now + _RESOLVE_TTL)
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        return family, sockaddr(address, target.port)

    def _resolve(self, target: Upstream, now: float) -> Tuple[int, Tuple[Any, ...]]:
        """:meth:`_address_of` with a blocking lookup of a name that is not remembered."""
        cached = self._known(target, now)
        if cached is not None:
            return self._address_of(target, cached, now, remember=False)
        from netimps import Host

        return self._address_of(target, Host(target.host).ip(), now)

    # -- one transfer's start -------------------------------------------------------------

    def _bind_pair(
        self, arrival: Arrival, family: int
    ) -> Optional[Tuple[socket.socket, Tuple[Any, ...], socket.socket]]:
        """The two sockets of a transfer and the client's address on the first, or ``None`` once refused."""
        sender = arrival.sender
        try:
            down, client = self._listener.reply_socket(arrival, self._ports)
        except OSError as exc:
            log.warning("no transfer socket for %s: %s", sender[:2], exc)
            self._refuse(sender, TFTPErrorCode.NOT_DEFINED, "relay busy")
            return None
        try:
            source = self.upstream_src or ("::" if family == socket.AF_INET6 else "0.0.0.0")
            up = bind_transfer(source, family, self._ports)
        except OSError:
            down.close()
            self._refuse(sender, TFTPErrorCode.NOT_DEFINED, "relay error")
            return None
        return down, client, up

    def _enroll(
        self,
        key: Tuple[str, int],
        request: RequestPacket,
        context: TFTPRequestContext,
        upstream: Tuple[Any, ...],
        pair: Tuple[socket.socket, Tuple[Any, ...], socket.socket],
        now: float,
    ) -> RelaySession:
        """Register a transfer over the sockets of :meth:`_bind_pair`; the driver then watches them."""
        down, client, up = pair
        session = RelaySession(new_session_id("r"), client, key, upstream, request, context, now)
        session.driver = RelayLegs(down, up)
        self._sessions[key] = session
        self.stats.add("started")
        return session

    def _begin(self, session: RelaySession, arrival: Arrival) -> None:
        """Forward the request, byte for byte, from the upstream-side socket."""
        data, sender = arrival.data, arrival.sender
        request = session.request
        log.debug(
            "[%s] %s %r: %s -> %s",
            session.id,
            "RRQ" if request.is_read else "WRQ",
            request.filename,
            session.client[:2],
            session.upstream,
        )
        self._emit(session, self._listener.sock, data, sender, "in", "client")
        self._send(session, session.driver.up, data, session.upstream)

    # -- forwarding -------------------------------------------------------------------------

    def _emit(
        self,
        session: Optional[RelaySession],
        sock: socket.socket,
        data: Union[bytes, bytearray, memoryview],
        peer: Tuple[Any, ...],
        direction: str,
        leg: str,
    ) -> None:
        self._trace_guard = trace = guard(self.trace, log, self._trace_guard)
        if trace is None:
            return
        try:
            local = local_towards(sock, peer, self._local_cache)
        except OSError:
            local = ()
        trace(
            PacketEvent(
                time.time(),
                direction,
                local,
                peer,
                bytes(data),
                "relay",
                None if session is None else session.id,
                leg,
            )
        )

    def _send(
        self,
        session: RelaySession,
        sock: socket.socket,
        data: Union[bytes, bytearray, memoryview],
        peer: Tuple[Any, ...],
        direction: str = "out",
    ) -> None:
        try:
            sock.sendto(data, peer)
        except OSError:
            return  # loss; the peers' own retransmissions recover
        self._emit(
            session, sock, data, peer, direction, "client" if sock is session.driver.down else "upstream"
        )

    def _forward(
        self, session: RelaySession, from_client: bool, data: bytes, addr: Tuple[Any, ...], now: float
    ) -> None:
        """One datagram that arrived on a transfer's client-facing (``from_client``) or upstream-facing socket."""
        if session.closed:
            return
        legs = session.driver
        if not from_client:
            if session.upstream_tid is None:
                if not same_host(addr, session.upstream):
                    return  # not the server we asked
                session.upstream_tid = addr  # RFC 1350 section 4: learn its TID
            if addr[0] != session.upstream_tid[0] or addr[1] != session.upstream_tid[1]:
                self._send(session, legs.up, _encode_error(TFTPErrorCode.UNKNOWN_TID), addr)
                return
            self._emit(session, legs.up, data, addr, "in", "upstream")
            session.observe(data, False, now, self.linger)
            self._send(session, legs.down, data, session.client)
        else:
            if addr[0] != session.client[0] or addr[1] != session.client[1]:
                self._send(session, legs.down, _encode_error(TFTPErrorCode.UNKNOWN_TID), addr)
                return
            self._emit(session, legs.down, data, addr, "in", "client")
            if session.upstream_tid is None:
                return  # nothing to forward to yet
            session.observe(data, True, now, self.linger)
            self._send(session, legs.up, data, session.upstream_tid)

    # -- ending ---------------------------------------------------------------------------------

    def _discard(self, key: Tuple[str, int]) -> None:
        """Drop a session whose opening failed half-way."""
        session = self._sessions.get(key)
        if session is not None:
            self._end(session, "error", time.monotonic())

    def _sweep(self, now: float) -> None:
        for session in list(self._sessions.values()):
            if now >= session.deadline(self.idle_timeout, self.max_duration):
                self._end(session, session.expiry_reason(now, self.idle_timeout, self.max_duration), now)

    def _end_all(self, now: float) -> None:
        """Serving stops: both ends of every transfer get ERROR 0, and each transfer ends as ``"shutdown"``."""
        for session in list(self._sessions.values()):
            legs = session.driver
            for sock, peer in ((legs.down, session.client), (legs.up, session.upstream_tid)):
                if peer is not None:
                    self._send(session, sock, _encode_error(0, "relay shutting down"), peer, "out")
            self._end(session, "shutdown", now)

    def _end(self, session: RelaySession, reason: str, now: float) -> None:
        if session.closed:
            return
        session.closed = True
        session.reason = reason
        self._sessions.pop(session.key, None)
        self._unwatch(session)
        summary = session.summary(now)
        self.stats.add("completed" if reason == "complete" else "failed")
        self.stats.add("bytes_to_clients", summary.bytes_to_client)
        self.stats.add("bytes_from_clients", summary.bytes_from_client)
        log.info(
            "[%s] %s %r %s <-> %s: %s, %d/%d bytes",
            session.id,
            summary.operation,
            summary.filename,
            summary.client[:2],
            summary.upstream[:2],
            reason,
            summary.bytes_to_client,
            summary.bytes_from_client,
        )
        if self.on_session_end is not None:
            try:
                self.on_session_end(summary)
            except Exception:
                log.exception("on_session_end callback failed")
