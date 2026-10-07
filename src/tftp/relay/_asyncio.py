"""The asyncio relay: :class:`RelayBase` over an event loop.

Requests arrive through the listener's ``UDPEndpoint`` (so replies keep pktinfo on
every loop, Windows' default Proactor loop included, which has no ``add_reader``).
Each transfer's two sockets are read through receive-only datagram transports and
written on the socket itself, so ``abort()`` never discards a last datagram. A
route, being a coroutine, and the lookup of a name run in a task the relay owns.
"""

from __future__ import annotations

import asyncio
import functools
import time
from typing import TYPE_CHECKING, Any, Callable, List, Optional, Set, Tuple, Union

if TYPE_CHECKING:
    from netimps import HostLike, InterfaceLike

from ..capture._events import PacketEvent
from ..packet._codec import RequestPacket
from ..server._asyncio import AsyncioService
from ..server._handler import TFTPRequestContext
from ..server._listener import Arrival, Listener
from ..server._policy import TFTPServerLimits
from ..server._session import PortRangeLike
from ._core import _DRAIN, _TICK, DEFAULT_MAX_SESSIONS, IDLE_TIMEOUT, LINGER, MAX_DURATION, RelayBase
from ._routing import AsyncRouteFunction, UpstreamLike
from ._session import RelaySession, RelaySummary
from .._loggers import RELAY as log

__all__ = ["AsyncTFTPRelay"]

#: ``RelayLegs.watch`` while the transports of a transfer are being made.
_OPENING = object()


class _Waiting:
    """A request whose route is being asked: who to answer, and the task asking."""

    __slots__ = ("sender", "task")

    def __init__(self, sender: Tuple[Any, ...], task: "asyncio.Future[None]") -> None:
        self.sender = sender
        self.task = task


class _LegProtocol(asyncio.DatagramProtocol):
    def __init__(self, relay: "AsyncTFTPRelay", session: RelaySession, from_client: bool) -> None:
        self.relay = relay
        self.session = session
        self.from_client = from_client

    def datagram_received(self, data: bytes, addr: Tuple[Any, ...]) -> None:
        self.relay._on_datagram(self.session, self.from_client, data, addr)

    def error_received(self, exc: Exception) -> None:
        pass


class AsyncTFTPRelay(AsyncioService, RelayBase):
    """:class:`tftp.relay.TFTPRelay` for asyncio; the same arguments, but ``route`` is a coroutine.

    ``route`` is an upstream (as for ``TFTPRelay``) or an ``async def``
    ``route(request, context) -> upstream | None``; a plain function is a
    ``TypeError``. The route and the lookup of an upstream's name (in the
    loop's executor, unless it is an address) are bounded together by
    ``idle_timeout`` and count against ``max_sessions``; one that outlasts it
    gets the client ERROR 0 "relay error". Use as ``async with
    AsyncTFTPRelay(...) as relay: await relay.serve_forever()``, or ``await
    relay.start()`` ... ``await relay.aclose()``.

    The lifecycle is :class:`tftp.AsyncTFTPServer`'s: ``bind()`` is plain,
    ``shutdown()`` never blocks and is safe from any thread, ``await
    wait_closed()`` returns once serving has stopped and ``await aclose()`` is
    final. Stopping ends every transfer in flight, both ends getting ERROR 0
    "relay shutting down".
    """

    _service = "relay"
    _log = log
    _drain = _DRAIN
    _coroutine_route = True

    def __init__(
        self,
        route: Union[AsyncRouteFunction, UpstreamLike],
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
        self._tasks: Set["asyncio.Future[None]"] = set()
        self._timer: Optional[asyncio.TimerHandle] = None
        self._ticking = False
        self._abandoning = False
        self._init_service()

    # -- lifecycle --------------------------------------------------------------------

    def _acquire(self) -> None:
        host, port, pktinfo, interface = self._listen_on
        listener = Listener(host, port, pktinfo=pktinfo, interface=interface)
        self._listener = listener
        self._address = listener.sock.getsockname()

    async def _free(self) -> None:
        if self._listener is not None:
            await self._listener.aclose()

    async def _listen(self) -> None:
        loop = self._loop
        assert loop is not None
        self._abandoning = False
        self._ticking = True
        self._timer = loop.call_later(_TICK, self._tick)
        try:
            await super()._listen()
        finally:
            self._ticking = False
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    async def _abandon_all(self) -> None:
        """Serving stops: a request still waiting for its route is refused, every transfer is ended."""
        self._abandoning = True
        for waiting in list(self._waiting.values()):
            self._refuse(waiting.sender, 0, "relay shutting down")
        self._waiting.clear()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._end_all(time.monotonic())
        await asyncio.sleep(0)  # the transports close their sockets on the next turn of the loop

    def _tick(self) -> None:
        self._timer = None
        try:
            self._sweep(time.monotonic())
        except Exception:
            log.exception("unexpected failure sweeping idle transfers")
        loop = self._loop
        if self._ticking and loop is not None:
            self._timer = loop.call_later(_TICK, self._tick)

    # -- requests ------------------------------------------------------------------------

    def _arrived(self, arrival: Arrival) -> None:
        try:
            admitted = self._admit(arrival, time.monotonic())
        except Exception:  # pragma: no cover - a bug, not a client error
            log.exception("relaying a request from %s failed", arrival.sender[:2])
            return
        if admitted is None:
            return
        key = admitted[0]
        task = asyncio.ensure_future(self._open(arrival, *admitted))
        self._waiting[key] = _Waiting(arrival.sender, task)
        self._tasks.add(task)
        task.add_done_callback(self._opened)

    def _opened(self, task: "asyncio.Future[None]") -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:  # pragma: no cover - _open handles its own
            log.error("relaying a request failed", exc_info=task.exception())

    async def _lookup(
        self, request: RequestPacket, context: TFTPRequestContext
    ) -> Tuple[int, Tuple[Any, ...]]:
        """Ask the route, then resolve its answer: a name in the loop's executor, an address at once."""
        target = self._target(await self.route(request, context))
        now = time.monotonic()
        cached = self._known(target, now)
        if cached is not None:
            return self._address_of(target, cached, now, remember=False)
        from netimps import Host

        host = Host(target.host)
        if host.is_address:
            address = host.ip()
        else:
            address = await asyncio.get_running_loop().run_in_executor(None, host.ip)
        return self._address_of(target, address, time.monotonic())

    async def _open(
        self,
        arrival: Arrival,
        key: Tuple[str, int],
        request: RequestPacket,
        context: TFTPRequestContext,
    ) -> None:
        """One request's route, lookup and transfer, ended when the transfer is watched or refused."""
        sender = arrival.sender
        session: Optional[RelaySession] = None
        ending = "error"
        try:
            try:
                family, upstream = await asyncio.wait_for(self._lookup(request, context), self.idle_timeout)
            except asyncio.TimeoutError:
                log.info("%r refused: no upstream within %s s", context, self.idle_timeout)
                self._refuse(sender, 0, "relay error")
                return
            except Exception as exc:
                self._refuse_route(context, sender, exc)
                return
            if self._abandoning:
                return
            pair = self._bind_pair(arrival, family)
            if pair is None:
                return
            self._waiting.pop(key, None)
            session = self._enroll(key, request, context, upstream, pair, time.monotonic())
            await self._attach(session)
            if not session.closed:
                self._begin(session, arrival)
        except asyncio.CancelledError:
            ending = "shutdown"
            raise
        except Exception:  # pragma: no cover - a bug, not a client error
            log.exception("relaying a request from %s failed", sender[:2])
        finally:
            self._waiting.pop(key, None)
            if session is not None and not session.closed and session.driver.watch is None:
                self._end(session, ending, time.monotonic())

    async def _attach(self, session: RelaySession) -> None:
        """Read each of the transfer's sockets through a transport that only receives."""
        legs = session.driver
        loop = asyncio.get_running_loop()
        legs.watch = _OPENING
        transports: List[asyncio.DatagramTransport] = []
        try:
            for sock, from_client in ((legs.down, True), (legs.up, False)):
                transport, _ = await loop.create_datagram_endpoint(
                    functools.partial(_LegProtocol, self, session, from_client), sock=sock
                )
                transports.append(transport)
        except BaseException:
            legs.watch = None
            for transport in transports:
                transport.abort()
            for sock in (legs.down, legs.up)[len(transports) :]:
                sock.close()
            raise
        legs.watch = transports
        if session.closed:  # ended while its transports were being made
            self._unwatch(session)

    # -- datagrams -----------------------------------------------------------------------

    def _on_datagram(
        self, session: RelaySession, from_client: bool, data: bytes, addr: Tuple[Any, ...]
    ) -> None:
        now = time.monotonic()
        try:
            self._forward(session, from_client, data, addr, now)
        except Exception:
            log.exception("unexpected failure in the event loop (ending that transfer)")
            self._end(session, "error", now)

    def _unwatch(self, session: RelaySession) -> None:
        legs = session.driver
        watch = legs.watch
        if watch is _OPENING:
            return  # _attach releases it when it returns
        legs.watch = None
        if watch:
            # The transports only receive: every datagram went out on the socket itself, so
            # abort() cannot discard a last ERROR or ACK still queued; it closes the socket.
            for transport in watch:
                transport.abort()
        else:
            legs.down.close()
            legs.up.close()
