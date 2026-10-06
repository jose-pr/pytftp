"""The asyncio server: :class:`tftp.TFTPServer`'s behaviour on an asyncio event loop.

The handler has coroutine hooks (``async def open_read``/``open_write``) and
returns asynchronous streams (:class:`AsyncTFTPReader`, :class:`AsyncTFTPWriter`),
which the engine is driven around with backpressure. A synchronous handler is
given through :class:`ThreadedHandler`, which opens in the loop's executor;
anything else is a ``TypeError`` when the server is built.

The listening socket keeps pktinfo (replies from the request's address) on
every loop, Windows' default Proactor loop included: it is read with netimps'
``UDPEndpoint.arecv``.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING, Any, Optional, Tuple, Union

if TYPE_CHECKING:
    from netimps import Host, IPAddressLike

from .._bridge import AsyncReaderBridge, AsyncWriterBridge
from ..packet._enums import TFTPErrorCode
from ..packet._codec import _encode_error
from .._loggers import SERVER as log
from ._core import ServerBase
from ._handler import ThreadedHandler, has_coroutine_hooks
from ._listener import _RECV_SIZE, Arrival
from ._session import Session

__all__ = ["AsyncTFTPServer"]


class _SessionProtocol(asyncio.DatagramProtocol):
    def __init__(self, server: "AsyncTFTPServer", session: Session) -> None:
        self.server = server
        self.session = session

    def datagram_received(self, data: bytes, addr: Tuple[Any, ...]) -> None:
        self.server._on_datagram(self.session, data, addr)

    def error_received(self, exc: Exception) -> None:
        pass


def _skippable(exc: BaseException) -> bool:
    return isinstance(exc, OSError)


class _Timer:
    """A session's pending timer: the handle and when it is due."""

    __slots__ = ("handle", "at", "transport")

    def __init__(self) -> None:
        self.handle: Optional[asyncio.TimerHandle] = None
        self.at: Optional[float] = None
        self.transport: Optional[asyncio.DatagramTransport] = None


class AsyncTFTPServer(ServerBase):
    """:class:`tftp.TFTPServer` for asyncio; same arguments except ``open_in_thread``/``workers``.

    ``root_or_handler`` is a directory, a handler with coroutine hooks
    (:class:`AsyncTFTPHandler`) or a synchronous handler wrapped in
    :class:`ThreadedHandler`; a bare synchronous handler raises ``TypeError``.
    Use as ``async with AsyncTFTPServer(...) as server: await server.serve_forever()``,
    or ``await server.start()`` ... ``await server.aclose()``.

    The constructor opens nothing: :meth:`bind` does, and :meth:`start`,
    :meth:`serve_forever` and ``async with`` call it. ``shutdown()`` never
    blocks and is safe from any thread; ``await wait_closed()`` returns once
    serving has stopped; ``await aclose()`` shuts down, waits and releases the
    sockets, and is final: :meth:`start` or :meth:`serve_forever` afterwards,
    or while serving, raises ``RuntimeError``.
    """

    def __init__(
        self,
        root_or_handler: Any,
        *,
        host: Optional[Union[IPAddressLike, Host]] = None,
        port: int = 69,
        **kwargs: Any,
    ) -> None:
        is_root = isinstance(root_or_handler, (str, os.PathLike))
        if (
            not is_root
            and not isinstance(root_or_handler, ThreadedHandler)
            and not has_coroutine_hooks(root_or_handler)
        ):
            raise TypeError(
                "%s has plain hooks; AsyncTFTPServer takes coroutine hooks, or a synchronous handler "
                "wrapped as ThreadedHandler(handler)" % type(root_or_handler).__name__
            )
        super().__init__(root_or_handler, host=host, port=port, **kwargs)
        if is_root:
            self.handler = ThreadedHandler(self.handler)
        # Only the adapter returns synchronous streams; a coroutine handler returns asynchronous ones.
        self._async_streams = not isinstance(self.handler, ThreadedHandler)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stopped: Optional[asyncio.Event] = None
        self._serve_ended: Optional["asyncio.Future[None]"] = None
        self._task: Optional["asyncio.Task[None]"] = None
        self._closing: Optional["asyncio.Task[None]"] = None
        self._serving = False
        self._closed = False

    # -- lifecycle --------------------------------------------------------------------

    def bind(self) -> None:
        """Bind the listening socket. Idempotent; raises what the bind raised.

        Raises ``RuntimeError`` after :meth:`aclose`.
        """
        if self._closed:
            raise RuntimeError("server is closed")
        if self._listener is None:
            self._listener = self._bind_listener()
            self._address = self._listener.sock.getsockname()

    def _claim(self) -> "asyncio.Future[None]":
        """Bind, then mark this task as the one that serves; the future is ready once listening."""
        self.bind()
        if self._serving:
            raise RuntimeError("server is already serving")
        loop = self._loop = asyncio.get_running_loop()
        self._stopped = asyncio.Event()
        self._serve_ended = loop.create_future()
        self._serving = True
        return loop.create_future()

    async def _serve(self, ready: "asyncio.Future[None]") -> None:
        loop = self._loop
        assert loop is not None and self._stopped is not None and self._serve_ended is not None
        listening = loop.create_task(self._listen())
        stop = loop.create_task(self._stopped.wait())
        try:
            await asyncio.sleep(0)  # let the listener register its reader
            if not ready.done():
                ready.set_result(None)
            await asyncio.wait({listening, stop}, return_when=asyncio.FIRST_COMPLETED)
            if listening.done() and not listening.cancelled():
                listening.result()  # a failure of the listener ends serving with it
        finally:
            for task in (listening, stop):
                task.cancel()
            for task in (listening, stop):
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass  # raised from listening.result() above, or already reported there
            for session in list(self._sessions.values()):
                self._abandon(session)
            self._serving = False
            if not ready.done():
                ready.cancel()
            self._serve_ended.set_result(None)

    async def serve_forever(self) -> None:
        """Bind, then serve in the caller's task until :meth:`shutdown`."""
        ready = self._claim()
        await self._serve(ready)

    async def start(self) -> "AsyncTFTPServer":
        """Bind, serve in a background task, and return once listening.

        Raises what :meth:`bind` raised, and ``RuntimeError`` when already
        serving or closed. The task's end is seen by :meth:`wait_closed`.
        """
        ready = self._claim()
        task = self._task = asyncio.ensure_future(self._serve(ready))
        task.add_done_callback(self._ended)
        await asyncio.wait({task, ready}, return_when=asyncio.FIRST_COMPLETED)
        if task.done():
            self._task = None
            task.result()  # what ended it before it was listening
        return self

    def _ended(self, task: "asyncio.Task[None]") -> None:
        if not task.cancelled() and task.exception() is not None:
            log.error("the server stopped on an unexpected error", exc_info=task.exception())

    def shutdown(self) -> None:
        """Ask serving to stop; returns at once. Safe from any thread, and a no-op when not serving."""
        loop, stopped = self._loop, self._stopped
        if not self._serving or loop is None or stopped is None:
            return
        try:
            loop.call_soon_threadsafe(stopped.set)
        except RuntimeError:  # the loop is closed: nothing is serving on it
            pass

    async def wait_closed(self, timeout: Optional[float] = None) -> bool:
        """Wait until serving has stopped; ``False`` when ``timeout`` seconds passed first.

        Raises what ended a task begun by :meth:`start`, if it ended on an error.
        """
        done = self._serve_ended
        if done is not None and not done.done():
            try:
                await asyncio.wait_for(asyncio.shield(done), timeout)
            except asyncio.TimeoutError:
                return False
        task, self._task = self._task, None
        if task is not None:
            await task
        return True

    async def aclose(self) -> None:
        """:meth:`shutdown`, :meth:`wait_closed`, then release the socket. Final and repeatable."""
        if self._closing is None:
            self._closing = asyncio.ensure_future(self._close())
        await asyncio.shield(self._closing)

    async def _close(self) -> None:
        self._closed = True
        self.shutdown()
        try:
            await self.wait_closed()
        finally:
            if self._listener is not None:
                await self._listener.aclose()

    async def __aenter__(self) -> "AsyncTFTPServer":
        self.bind()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # -- requests ---------------------------------------------------------------------

    async def _listen(self) -> None:
        endpoint = self._listener.endpoint
        # An ICMP error surfacing on the listener is skipped, as the
        # synchronous server does; closing the endpoint ends the loop quietly.
        async for datagram in endpoint.datagrams(_RECV_SIZE, on_error=_skippable):
            self._arrived(self._listener.arrival(datagram))
            # Whatever else is already queued, without another wait.
            for _ in range(63):
                arrival = self._listener.recv()
                if arrival is None:
                    break
                self._arrived(arrival)

    def _arrived(self, arrival: Arrival) -> None:
        try:
            session = self._admit(arrival, time.monotonic())
        except Exception:  # pragma: no cover - a bug, not a client error
            log.exception("request from %s failed", arrival.sender[:2])
            return
        if session is not None:
            asyncio.ensure_future(self._open_session(session))

    async def _open_session(self, session: Session) -> None:
        loop = self._loop
        assert loop is not None
        session.notify = lambda: loop.call_soon_threadsafe(self._resume, session)
        timer = _Timer()
        session.driver = timer
        try:
            stream = await session.call_handler(self.handler, self.options, self.timeout, self._mtu(session))
            if session.closed:  # the server stopped meanwhile
                session.stream = stream
                session.close_stream(ok=False)
                return
            if self._async_streams:
                if session.context.request.is_read:
                    stream = AsyncReaderBridge(stream)
                else:
                    stream = AsyncWriterBridge(stream)
            session.stream = stream
            transport, _ = await loop.create_datagram_endpoint(
                lambda: _SessionProtocol(self, session), sock=session.sock
            )
            timer.transport = transport
            now = time.monotonic()
            transfer = session.start(
                stream, self.options, self.timeout, self.retries, now, self._mtu(session), **self._engine(now)
            )
        except Exception as exc:
            self._refuse(session, exc)
            return
        session.transfer = transfer
        self.stats.add("started")
        try:
            if transfer.is_done:
                self._done(session, time.monotonic())
            else:
                self._schedule(session)
        except Exception as exc:
            self._survive(session, exc)

    # -- transfer events ------------------------------------------------------------------

    def _on_datagram(self, session: Session, data: bytes, addr: Tuple[Any, ...]) -> None:
        transfer = session.transfer
        if session.closed or transfer is None:
            return
        if session.trace is not None:
            session.emit(data, "in", addr)
        if addr[1] != session.port or addr[0] != session.host:
            stray = _encode_error(TFTPErrorCode.UNKNOWN_TID)
            try:
                session.sock.sendto(stray, addr)
            except OSError:
                return
            if session.trace is not None:
                session.emit(stray, "out", addr)
            return
        now = time.monotonic()
        was_done = transfer.is_done
        try:
            transfer.handle(memoryview(data), len(data), now)
            if transfer.is_done and not was_done:
                self._done(session, now)
            else:
                self._schedule(session)
        except Exception as exc:
            self._survive(session, exc)

    def _resume(self, session: Session) -> None:
        transfer = session.transfer
        if session.closed or transfer is None or transfer.is_done:
            return
        now = time.monotonic()
        try:
            transfer.resume(now)
            if transfer.is_done:
                self._done(session, now)
            else:
                self._schedule(session)
        except Exception as exc:
            self._survive(session, exc)

    def _schedule(self, session: Session) -> None:
        timer: _Timer = session.driver
        due = session.wakeup()
        if due is None or session.closed:
            return
        if timer.at is None or due < timer.at:
            if timer.handle is not None:
                timer.handle.cancel()
            timer.at = due
            delay = max(0.0, due - time.monotonic())
            timer.handle = self._loop.call_later(delay, self._fire, session)  # type: ignore[union-attr]

    def _fire(self, session: Session) -> None:
        timer: _Timer = session.driver
        timer.handle = None
        timer.at = None
        if session.closed:
            return
        now = time.monotonic()
        due = session.wakeup()
        if due is None:
            return
        if due > now:
            self._schedule(session)
            return
        if session.linger_until is not None:
            self._release(session)
            return
        transfer = session.transfer
        assert transfer is not None
        try:
            transfer.on_timeout(now)
            if transfer.is_done:
                self._done(session, now)
            else:
                self._schedule(session)
        except Exception as exc:
            self._survive(session, exc)

    def _done(self, session: Session, now: float) -> None:
        if self._finished(session, now):
            self._schedule(session)
        else:
            self._release(session)

    def _release(self, session: Session) -> None:
        if not self._forget(session):
            return
        timer: Optional[_Timer] = session.driver
        if timer is not None:
            if timer.handle is not None:
                timer.handle.cancel()
            if timer.transport is not None:
                # The transport only receives: every datagram goes out on the socket itself
                # (Session.send), so abort() cannot discard a last ERROR or ACK still queued.
                timer.transport.abort()
                return
        session.sock.close()
