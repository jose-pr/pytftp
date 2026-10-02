"""The asyncio server: :class:`Server`'s behaviour on an asyncio event loop.

Handlers may be ``async def``: ``open_read``/``open_write`` are awaited when
they return an awaitable, and the streams they return may be asynchronous
(``async read``/``async write``, async iterables, ``StreamWriter``) -- the
engine is driven around them with backpressure. Blocking handlers still work:
those not marked ``_tftp_fast_open_`` are opened in the loop's executor.

The listening socket keeps pktinfo (replies from the request's address): it
is read with ``loop.add_reader`` where the loop has it, and by a small reader
thread where it does not (Windows' default Proactor loop).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import select
import socket
import sys
import threading
import time
from typing import TYPE_CHECKING, Any, Optional, Tuple

if TYPE_CHECKING:
    from netimps import Host, IPAddressLike

from ..packet import ErrorCode, encode_error
from ..server.base import WINDOWS_SESSION_CAP, ServerBase
from ..server.listener import Arrival
from ..server.session import Session
from .bridge import AsyncReaderBridge, AsyncWriterBridge, is_async_reader, is_async_writer

__all__ = ["AsyncServer"]

log = logging.getLogger("tftp.server")


class _SessionProtocol(asyncio.DatagramProtocol):
    def __init__(self, server: "AsyncServer", session: Session) -> None:
        self.server = server
        self.session = session

    def datagram_received(self, data: bytes, addr: Tuple[Any, ...]) -> None:
        self.server._on_datagram(self.session, data, addr)

    def error_received(self, exc: Exception) -> None:
        pass


class _Timer:
    """A session's pending timer: the handle and when it is due."""

    __slots__ = ("handle", "at", "transport")

    def __init__(self) -> None:
        self.handle: Optional[asyncio.TimerHandle] = None
        self.at: Optional[float] = None
        self.transport: Optional[asyncio.DatagramTransport] = None


class AsyncServer(ServerBase):
    """:class:`Server` for asyncio; same arguments except ``open_in_thread``/``workers``.

    ``executor`` (default: the loop's) runs blocking handler opens.
    Use as ``async with AsyncServer(...) as server: await server.serve_forever()``,
    or ``await server.start()`` ... ``await server.stop()``.
    """

    def __init__(
        self,
        root_or_handler: Any,
        host: "IPAddressLike | Host | None" = "::",
        port: int = 69,
        *,
        executor: Any = None,
        **kwargs: Any,
    ) -> None:
        if sys.platform == "win32":
            kwargs.setdefault("session_cap", WINDOWS_SESSION_CAP)  # a selector loop's select()
        super().__init__(root_or_handler, host, port, **kwargs)
        self.executor = executor
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stopped: Optional[asyncio.Event] = None
        self._task: Optional["asyncio.Task[None]"] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._reader_stop = threading.Event()
        self._using_add_reader = False
        self._reader_wake: Optional[Tuple[socket.socket, socket.socket]] = None
        self._closed = False

    # -- lifecycle --------------------------------------------------------------------

    async def serve_forever(self) -> None:
        """Serve until :meth:`shutdown`."""
        if self._closed:
            raise RuntimeError("server is closed")
        loop = self._loop = asyncio.get_running_loop()
        self._stopped = asyncio.Event()
        try:
            loop.add_reader(self._listener.sock, self._on_listener_ready)
            self._using_add_reader = True
        except NotImplementedError:  # Proactor: read the listener from a thread
            self._reader_stop.clear()
            self._reader_wake = socket.socketpair()
            self._reader_thread = threading.Thread(target=self._reader, name="tftp-listen", daemon=True)
            self._reader_thread.start()
        try:
            await self._stopped.wait()
        finally:
            if self._using_add_reader:
                loop.remove_reader(self._listener.sock)
                self._using_add_reader = False
            self._reader_stop.set()
            if self._reader_thread is not None:
                try:
                    self._reader_wake[1].send(bytes(1))  # type: ignore[index]
                except OSError:
                    pass
                await loop.run_in_executor(None, self._reader_thread.join, 2.0)
                self._reader_thread = None
                for end in self._reader_wake:  # type: ignore[union-attr]
                    end.close()
            for session in list(self._sessions.values()):
                if session.transfer is not None and not session.transfer.done:
                    session.transfer.abort("server shutting down")
                if session.stream is not None:
                    session.close_stream(ok=False)
                self._release(session)

    def shutdown(self) -> None:
        """Stop :meth:`serve_forever`; safe from any thread."""
        loop, stopped = self._loop, self._stopped
        if loop is not None and stopped is not None:
            loop.call_soon_threadsafe(stopped.set)

    async def start(self) -> "AsyncServer":
        """Serve in a background task; returns once listening."""
        self._task = asyncio.ensure_future(self.serve_forever())
        while self._stopped is None:
            await asyncio.sleep(0)
        return self

    async def stop(self) -> None:
        self.shutdown()
        if self._task is not None:
            await self._task
            self._task = None

    async def close(self) -> None:
        if self._closed:
            return
        await self.stop()
        self._closed = True
        self._listener.close()

    async def __aenter__(self) -> "AsyncServer":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # -- requests ---------------------------------------------------------------------

    def _on_listener_ready(self) -> None:
        for _ in range(64):
            arrival = self._listener.recv()
            if arrival is None:
                return
            self._arrived(arrival)

    def _reader(self) -> None:
        sock = self._listener.sock
        loop = self._loop
        assert loop is not None
        wake = self._reader_wake[0]  # type: ignore[index]
        while not self._reader_stop.is_set():
            try:
                readable, _, _ = select.select([sock, wake], [], [], 5.0)
            except (OSError, ValueError):
                return
            if wake in readable or not readable:
                continue
            while True:
                arrival = self._listener.recv()
                if arrival is None:
                    break
                loop.call_soon_threadsafe(self._arrived, arrival)

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
            if getattr(self.handler, "_tftp_fast_open_", False) or self._is_async_handler(session):
                stream = session.call_handler(self.handler, self.options, self.timeout, self._mtu(session))
                if inspect.isawaitable(stream):
                    stream = await stream
            else:
                stream = await loop.run_in_executor(
                    self.executor,
                    session.call_handler,
                    self.handler,
                    self.options,
                    self.timeout,
                    self._mtu(session),
                )
            if session.closed:  # the server stopped meanwhile
                session.stream = stream
                session.close_stream(ok=False)
                return
            if session.context.request.is_read and is_async_reader(stream):
                stream = AsyncReaderBridge(stream)
            elif not session.context.request.is_read and is_async_writer(stream):
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
        if transfer.done:
            self._done(session, time.monotonic())
        else:
            self._schedule(session)

    def _is_async_handler(self, session: Session) -> bool:
        method = (
            self.handler.open_read
            if session.context.request.is_read
            else getattr(self.handler, "open_write", None)
        )
        return inspect.iscoroutinefunction(method)

    # -- transfer events ------------------------------------------------------------------

    def _on_datagram(self, session: Session, data: bytes, addr: Tuple[Any, ...]) -> None:
        transfer = session.transfer
        if session.closed or transfer is None:
            return
        if session.trace is not None:
            session.emit(data, "in", addr)
        if addr[1] != session.port or addr[0] != session.host:
            stray = encode_error(ErrorCode.UNKNOWN_TID)
            try:
                session.sock.sendto(stray, addr)
            except OSError:
                return
            if session.trace is not None:
                session.emit(stray, "out", addr)
            return
        now = time.monotonic()
        was_done = transfer.done
        transfer.handle(memoryview(data), len(data), now)
        if transfer.done and not was_done:
            self._done(session, now)
        else:
            self._schedule(session)

    def _resume(self, session: Session) -> None:
        transfer = session.transfer
        if session.closed or transfer is None or transfer.done:
            return
        now = time.monotonic()
        transfer.resume(now)
        if transfer.done:
            self._done(session, now)
        else:
            self._schedule(session)

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
        transfer.on_timeout(now)
        if transfer.done:
            self._done(session, now)
        else:
            self._schedule(session)

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
                timer.transport.abort()
                return
        session.sock.close()
