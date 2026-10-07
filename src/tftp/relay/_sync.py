"""The blocking relay: :class:`RelayBase` over one thread and one ``selectors`` loop."""

from __future__ import annotations

import contextlib
import selectors
import socket
import time
from typing import TYPE_CHECKING, Any, Callable, Optional, Tuple, Union

if TYPE_CHECKING:
    from netimps import HostLike, InterfaceLike

from ..capture._events import PacketEvent
from ..packet._codec import RequestPacket
from ..server._handler import TFTPRequestContext
from ..server._listener import Arrival, Listener
from ..server._policy import TFTPServerLimits
from ..server._session import PortRangeLike
from ..server._sync import SelectorService
from ._core import _DRAIN, _TICK, DEFAULT_MAX_SESSIONS, IDLE_TIMEOUT, LINGER, MAX_DURATION, RelayBase
from ._routing import RouteFunction, UpstreamLike
from ._session import RelaySession, RelaySummary
from .._loggers import RELAY as log

__all__ = ["TFTPRelay"]

_RECV = 65536


class TFTPRelay(SelectorService, RelayBase):
    """Forward TFTP requests to upstream servers, transparently.

    :param route: an upstream (``"host"``, ``"host:port"``, ``(host, port)``,
        :class:`Upstream`) for every request, or a route callable
        ``route(request, context) -> upstream | None`` (see
        :mod:`tftp.relay`). ``None`` refuses with ERROR 2. The callable is a
        plain function: a coroutine function is a ``TypeError``, which
        :class:`AsyncTFTPRelay` takes.
    :param host, port: where to listen for requests (as for ``TFTPServer``).
    :param idle_timeout: end a transfer after this long without traffic.
        Keep it above the largest timeout a peer may negotiate times its
        retries.
    :param max_duration: end any transfer after this long.
    :param linger: after a recognised final DATA/ACK exchange (or an ERROR,
        up to 1 s), keep forwarding this long for retransmissions.
    :param upstream_src: local address to send upstream from (a string,
        an ``ipaddress`` address or a ``netimps.Host``).
    :param max_sessions: concurrent relayed transfers; requests beyond it get
        ERROR 0 "relay busy". The default is 250 on every platform (two
        sockets each); ``None`` is unlimited, except on Windows, where it is
        250 and a number above 255 is a ``ValueError`` (``select()``'s limit).
    :param limits, ignore_broadcast, reply_from_request_address:
        as for ``TFTPServer``.
    :param trace: ``trace(PacketEvent)`` for every datagram received or
        sent, with ``role="relay"`` and ``leg`` ``"client"``/``"upstream"``.
    :param on_session_end: ``on_session_end(RelaySummary)`` when a relayed
        transfer ends.
    :param port_range: ports for both sockets of each relayed transfer (the
        client side and the upstream side), as for ``TFTPServer``.
    :param interface: listen on one network adapter, as for ``TFTPServer``.

    The constructor opens nothing: :meth:`bind` does, and :meth:`start`,
    :meth:`serve_forever` and ``with`` call it. The lifecycle is
    :class:`TFTPServer`'s: ``shutdown()`` never blocks, ``wait_closed()``
    returns once serving has stopped, ``close()`` is final.
    """

    _service = "relay"

    def __init__(
        self,
        route: Union[RouteFunction, UpstreamLike],
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
        super().__init__(
            route,
            host=host,
            port=port,
            idle_timeout=idle_timeout,
            max_duration=max_duration,
            linger=linger,
            upstream_src=upstream_src,
            limits=limits,
            max_sessions=max_sessions,
            ignore_broadcast=ignore_broadcast,
            reply_from_request_address=reply_from_request_address,
            trace=trace,
            on_session_end=on_session_end,
            port_range=port_range,
            interface=interface,
        )
        self._selector: Any = None
        self._wake_r: Any = None
        self._wake_w: Any = None
        self._buf = bytearray(_RECV)
        self._init_service()

    # -- lifecycle ---------------------------------------------------------------------

    def _acquire(self) -> None:
        with contextlib.ExitStack() as stack:
            host, port, pktinfo, interface = self._listen_on
            listener = Listener(host, port, pktinfo=pktinfo, interface=interface)
            stack.callback(listener.close)
            selector = selectors.DefaultSelector()
            stack.callback(selector.close)
            wake_r, wake_w = socket.socketpair()
            stack.callback(wake_r.close)
            stack.callback(wake_w.close)
            wake_r.setblocking(False)
            wake_w.setblocking(False)
            selector.register(listener.sock, selectors.EVENT_READ, None)
            selector.register(wake_r, selectors.EVENT_READ, self)
            stack.pop_all()
        self._listener = listener
        self._address = listener.sock.getsockname()
        self._selector = selector
        self._wake_r, self._wake_w = wake_r, wake_w

    def _wake(self) -> None:
        try:
            self._wake_w.send(bytes(1))
        except OSError:
            pass

    def _free(self) -> None:
        if self._selector is not None:
            self._selector.close()
        if self._listener is not None:
            self._listener.close()
        for sock in (self._wake_r, self._wake_w):
            if sock is not None:
                sock.close()

    def _loop(self) -> None:
        clock = time.monotonic
        next_tick = clock() + _TICK
        try:
            while not self._stopping:
                for key, _ in self._selector.select(max(0.0, next_tick - clock())):
                    data = key.data
                    try:
                        if data is None:
                            self._on_request(clock())
                        elif data is self:
                            try:
                                while self._wake_r.recv(64):
                                    pass
                            except OSError:
                                pass
                        else:
                            session, leg = data
                            self._on_datagram(session, leg, clock())
                    except Exception:
                        log.exception("unexpected failure in the event loop (ending that transfer)")
                        if isinstance(data, tuple):
                            self._end(data[0], "error", clock())
                now = clock()
                if now >= next_tick:
                    try:
                        self._sweep(now)
                    except Exception:
                        log.exception("unexpected failure sweeping idle transfers")
                    next_tick = now + _TICK
        finally:
            self._end_all(clock())

    # -- requests and datagrams -------------------------------------------------------

    def _on_request(self, now: float) -> None:
        for _ in range(_DRAIN):
            arrival = self._listener.recv()
            if arrival is None:
                return
            key = None
            try:
                admitted = self._admit(arrival, now)
                if admitted is None:
                    continue
                key = admitted[0]
                self._open(arrival, *admitted, now)
            except Exception:  # pragma: no cover - a bug, not a client error
                log.exception("relaying a request from %s failed", arrival.sender[:2])
                if key is not None:
                    self._discard(key)

    def _open(
        self,
        arrival: Arrival,
        key: Tuple[str, int],
        request: RequestPacket,
        context: TFTPRequestContext,
        now: float,
    ) -> None:
        """Route an admitted request, and start its transfer: both sockets watched, the request forwarded."""
        try:
            target = self._target(self.route(request, context))
            family, upstream = self._resolve(target, now)
        except Exception as exc:
            self._refuse_route(context, arrival.sender, exc)
            return
        pair = self._bind_pair(arrival, family)
        if pair is None:
            return
        session = self._enroll(key, request, context, upstream, pair, now)
        self._selector.register(pair[0], selectors.EVENT_READ, (session, "down"))
        self._selector.register(pair[2], selectors.EVENT_READ, (session, "up"))
        self._begin(session, arrival)

    def _on_datagram(self, session: RelaySession, leg: str, now: float) -> None:
        if session.closed:
            return
        legs = session.driver
        sock = legs.down if leg == "down" else legs.up
        buf = self._buf
        for _ in range(_DRAIN):
            try:
                n, addr = sock.recvfrom_into(buf)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                continue
            self._forward(session, leg == "down", bytes(buf[:n]), addr, now)

    def _unwatch(self, session: RelaySession) -> None:
        legs = session.driver
        for sock in (legs.down, legs.up):
            try:
                self._selector.unregister(sock)
            except (KeyError, ValueError):
                pass
            sock.close()
