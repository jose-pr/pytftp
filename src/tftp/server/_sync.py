"""The server: one thread, one ``selectors`` event loop, any number of transfers.

:class:`SelectorService` is the lifecycle it shares with the relay: ``bind``,
``start``, ``serve_forever``, ``shutdown``, ``wait_closed`` and ``close``.

The listening socket is read through :class:`netimps.UDPEndpoint`, which
reports each request's destination address (``IP_PKTINFO`` /
``IPV6_PKTINFO``) where the platform allows it; :mod:`.session` binds the
transfer's socket to it. Timers are a heap holding at most one live entry
per session: a deadline that moves later is re-queued only when its old
entry comes due, so the hot path never touches the heap.
"""

from __future__ import annotations

import collections
import concurrent.futures
import contextlib
import heapq
import selectors
import socket
import sys
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, List, Optional, Tuple, TypeVar

if TYPE_CHECKING:
    from netimps import Host, IPAddressLike

from ..capture._events import PacketEvent
from ..options._policy import TFTPServerOptions
from ..packet._enums import TFTPErrorCode
from ..packet._codec import _encode_error
from .._result import TransferResult
from ..transfer._engine import Transfer
from .._loggers import SERVER as log
from ._core import DEFAULT_MAX_SESSIONS, SELECT_SESSIONS, ServerBase
from ._handler import has_coroutine_hooks
from ._policy import TFTPServerLimits
from ._session import Session

__all__ = ["SelectorService", "TFTPServer"]

_RECV_SIZE = 65536  # one receive buffer, shared by every session
_DRAIN = 64  # packets read per readiness event before yielding to others
_WAKE_BYTE = bytes(1)
#: How long close() waits for a loop that has been asked to stop; a handler that
#: has not returned by then is abandoned (the loop thread is a daemon).
_CLOSE_WAIT = 5.0

_Service = TypeVar("_Service", bound="SelectorService")


class SelectorService:
    """The lifecycle of a service run by one selector loop on one thread.

    A subclass provides ``_acquire()`` (take every resource, or none),
    ``_loop()`` (run until ``self._stopping``), ``_wake()`` (end a wait in the
    loop from any thread) and ``_free()`` (let go of what ``_acquire`` took).

    ``bind`` opens the sockets and is idempotent. ``start`` and
    ``serve_forever`` bind, then serve; a second one while serving, or any
    after ``close``, raises ``RuntimeError``. ``shutdown`` never blocks and is
    safe from any thread, a handler included; it has no effect when nothing is
    serving. ``wait_closed`` blocks until serving has stopped. ``close`` is
    ``shutdown``, ``wait_closed`` (up to five seconds), then release, and is
    final.
    """

    _service = "server"

    def _init_service(self) -> None:
        self._guard = threading.Lock()
        self._close_guard = threading.Lock()
        self._bound = False
        self._serving = False
        self._closed = False
        self._stopping = False
        self._idle = threading.Event()
        self._idle.set()
        self._entered = threading.Event()
        self._serving_ident: Optional[int] = None
        self._thread: Optional[threading.Thread] = None

    # -- what a service provides -------------------------------------------

    def _acquire(self) -> None:  # pragma: no cover - subclasses
        raise NotImplementedError

    def _loop(self) -> None:  # pragma: no cover - subclasses
        raise NotImplementedError

    def _wake(self) -> None:  # pragma: no cover - subclasses
        raise NotImplementedError

    def _free(self) -> None:  # pragma: no cover - subclasses
        raise NotImplementedError

    # -- lifecycle ---------------------------------------------------------

    def bind(self) -> None:
        """Open and bind the sockets. Idempotent; raises what the bind raised.

        Nothing stays open when it raises. Raises ``RuntimeError`` after
        :meth:`close`.
        """
        with self._guard:
            if self._closed:
                raise RuntimeError("%s is closed" % self._service)
            if not self._bound:
                self._acquire()
                self._bound = True

    def _claim(self) -> None:
        """Bind, then mark this caller as the one that serves."""
        self.bind()
        with self._guard:
            if self._closed:
                raise RuntimeError("%s is closed" % self._service)
            if self._serving:
                raise RuntimeError("%s is already serving" % self._service)
            self._serving = True
            self._stopping = False
            self._idle.clear()
            self._entered.clear()

    def _serve(self) -> None:
        self._serving_ident = threading.get_ident()
        self._entered.set()
        try:
            self._loop()
        finally:
            with self._guard:
                self._serving_ident = None
                self._serving = False
            self._idle.set()

    def serve_forever(self) -> None:
        """Bind, then serve on the calling thread until :meth:`shutdown`."""
        self._claim()
        self._serve()

    def start(self: _Service) -> _Service:
        """Bind, serve on a daemon thread, and return ``self`` once serving.

        Raises what :meth:`bind` raised, and ``RuntimeError`` when already
        serving or closed.
        """
        self._claim()
        thread = threading.Thread(target=self._serve, name="tftp-" + self._service, daemon=True)
        self._thread = thread
        thread.start()
        self._entered.wait()
        return self

    def shutdown(self) -> None:
        """Ask serving to stop; returns at once. Safe from any thread and from a handler."""
        with self._guard:
            if not self._serving:
                return
            self._stopping = True
        self._wake()

    def wait_closed(self, timeout: Optional[float] = None) -> bool:
        """Block until serving has stopped; ``False`` when ``timeout`` seconds passed first.

        Raises ``RuntimeError`` from the thread that is serving, which would wait for itself.
        """
        if self._serving_ident == threading.get_ident():
            raise RuntimeError("wait_closed() from the serving thread would wait for itself")
        began = time.monotonic()
        if not self._idle.wait(timeout):
            return False
        thread = self._thread
        if thread is not None:
            thread.join(None if timeout is None else max(0.0, timeout - (time.monotonic() - began)))
            if thread.is_alive():
                return False
            self._thread = None
        return True

    def close(self) -> None:
        """:meth:`shutdown`, :meth:`wait_closed`, then release every socket. Final and repeatable.

        Raises ``RuntimeError`` from the thread that is serving: a handler
        that wants the server down calls :meth:`shutdown`.
        """
        if self._serving_ident == threading.get_ident():
            raise RuntimeError("close() from the serving thread: call shutdown()")
        with self._close_guard:
            if self._closed:
                return
            self.shutdown()
            self.wait_closed(_CLOSE_WAIT)
            with self._guard:
                self._closed = True
            self._free()

    def __enter__(self: _Service) -> _Service:
        self.bind()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class TFTPServer(SelectorService, ServerBase):
    """A TFTP server.

    :param root_or_handler: a directory to serve (wrapped in
        :class:`FilesystemBackend` with ``writable``, ``create`` and
        ``overwrite``), or a handler object.
    :param host: address to listen on: a string, an ``ipaddress`` address or a
        ``netimps.Host``. ``None`` (the default) or ``"::"`` listens on IPv6
        and IPv4 at once where the platform allows dual-stack sockets, else
        falls back to ``"0.0.0.0"``. ``"0.0.0.0"`` is IPv4 only.
    :param port: UDP port; ``0`` picks a free one (see :attr:`server_address`,
        valid after :meth:`bind`).
    :param timeout: retransmission timeout, unless a client negotiates its own.
    :param retries: retransmissions of one packet before abandoning a transfer.
    :param options: what the server negotiates (:class:`TFTPServerOptions`).
    :param max_sessions: concurrent transfers; requests beyond it get
        ERROR 0 "server busy". The default is 500 on every platform; ``None``
        is unlimited, except on Windows, where ``select()`` caps it at 510
        (a larger number there is a ``ValueError``).
    :param reply_from_request_address: answer each request from the address
        it was sent to (pktinfo). When the platform cannot report it, replies
        come from the listening address, or the routing table's choice when
        listening on a wildcard.
    :param dally: after acknowledging the last block of an upload, keep the
        transfer open for one timeout to re-acknowledge a retransmitted last
        block (RFC 1350 section 6). Costs nothing but a socket for that long.
    :param on_complete: called with a :class:`TransferResult` after every
        transfer, failed ones included (``result.error`` is set).
    :param limits: request, per-client and duration bounds
        (:class:`TFTPServerLimits`).
    :param ignore_broadcast: silently drop requests sent to a broadcast or
        multicast address (RFC 1123 4.2.3.4). Needs pktinfo to see the
        destination; without it every request looks unicast.
    :param backoff: each consecutive retransmission waits this many times
        longer (RFC 1123 4.2.3.2); progress resets it.
    :param max_timeout: ceiling for the backed-off wait; ``None`` is eight
        times the timeout.
    :param trace: ``trace(PacketEvent)`` for every datagram of every
        transfer, received and sent (``role="server"``, one ``session``
        id per transfer). Requests refused before a transfer exists are
        not traced. :class:`tftp.capture.PcapWriter` is a ready hook.
    :param open_in_thread: call the handler's ``open_read``/``open_write`` in
        a worker thread, so a handler that blocks (an HTTP request, an
        upstream server) never stalls other transfers. ``None`` decides from
        the handler: those with ``opens_fast = True`` (the built-in
        file and memory handlers) open inline, everything else in a worker.
    :param workers: size of that worker pool.
    :param port_range: a :class:`PortRange`, a ``(low, high)`` pair
        (inclusive), a ``range`` or ``"LOW:HIGH"`` text: transfer sockets take
        their ports from it, round-robin from a position this server owns, so a
        firewall can allow them. A request arriving while every port is in
        use gets ERROR 0 "server busy". ``None`` lets the OS choose.
    :param interface: listen on one network adapter: a name (``"eth0"``), a
        ``netimps.Interface``, its MAC or one of its addresses. The listener
        binds that adapter's IPv4 address when it has one, else its IPv6
        address; ``host="0.0.0.0"`` or ``"::"`` picks the family instead. Any
        other ``host`` with an interface is a ``ValueError``. One family per
        server: run two servers to serve both on one adapter.
    """

    def __init__(
        self,
        root_or_handler: Any,
        *,
        host: "IPAddressLike | Host | None" = None,
        port: int = 69,
        writable: bool = False,
        create: bool = True,
        overwrite: bool = False,
        timeout: float = 1.0,
        retries: int = 5,
        options: Optional[TFTPServerOptions] = None,
        max_sessions: Optional[int] = DEFAULT_MAX_SESSIONS,
        reply_from_request_address: bool = True,
        dally: bool = True,
        on_complete: Optional[Callable[[TransferResult], Any]] = None,
        limits: Optional[TFTPServerLimits] = None,
        ignore_broadcast: bool = True,
        backoff: float = 2.0,
        max_timeout: Optional[float] = None,
        trace: Optional[Callable[[PacketEvent], Any]] = None,
        open_in_thread: Optional[bool] = None,
        workers: int = 8,
        port_range: Any = None,
        interface: Any = None,
    ) -> None:
        if has_coroutine_hooks(root_or_handler):
            raise TypeError(
                "%r has coroutine hooks; TFTPServer takes plain functions (AsyncTFTPServer takes coroutines)"
                % (root_or_handler,)
            )
        if sys.platform == "win32" and max_sessions is not None and max_sessions > SELECT_SESSIONS:
            raise ValueError(
                "max_sessions=%d is more than the %d transfers select() can watch on Windows"
                % (max_sessions, SELECT_SESSIONS)
            )
        super().__init__(
            root_or_handler,
            host=host,
            port=port,
            writable=writable,
            create=create,
            overwrite=overwrite,
            timeout=timeout,
            retries=retries,
            options=options,
            max_sessions=max_sessions,
            reply_from_request_address=reply_from_request_address,
            dally=dally,
            on_complete=on_complete,
            limits=limits,
            ignore_broadcast=ignore_broadcast,
            backoff=backoff,
            max_timeout=max_timeout,
            trace=trace,
            port_range=port_range,
            interface=interface,
        )
        self._init_service()
        self._ready: "collections.deque[Session]" = collections.deque()
        self._pending_opens: "collections.deque[Tuple[Session, Any]]" = collections.deque()
        if open_in_thread is None:
            open_in_thread = not getattr(self.handler, "opens_fast", False)
        self._worker_count = max(1, workers) if open_in_thread else 0
        self._workers: Optional[concurrent.futures.ThreadPoolExecutor] = None
        self._selector: Any = None
        self._wake_r: Any = None
        self._wake_w: Any = None
        self._timers: List[Tuple[float, int, Session]] = []
        self._seq = 0
        self._buf = bytearray(_RECV_SIZE)
        self._view = memoryview(self._buf)

    # -- lifecycle ----------------------------------------------------------

    def _acquire(self) -> None:
        with contextlib.ExitStack() as stack:
            listener = self._bind_listener()
            stack.callback(listener.close)
            workers = None
            if self._worker_count:
                workers = concurrent.futures.ThreadPoolExecutor(
                    self._worker_count, thread_name_prefix="tftp-open"
                )
                stack.callback(workers.shutdown)
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
        self._workers = workers
        self._selector = selector
        self._wake_r, self._wake_w = wake_r, wake_w

    def _loop(self) -> None:
        clock = time.monotonic
        select = self._selector.select
        try:
            while not self._stopping:
                events = select(self._next_timeout(clock()))
                for key, _ in events:
                    data = key.data
                    try:
                        if data is None:
                            self._on_request(clock())
                        elif data is self:
                            self._drain_wake()
                        else:
                            self._on_packet(data, clock())
                    except Exception as exc:
                        self._survive(data if isinstance(data, Session) else None, exc)
                if self._timers and self._timers[0][0] <= clock():
                    self._run_timers(clock())
        finally:
            for session in list(self._sessions.values()):
                self._abandon(session)
            while self._pending_opens:
                session, outcome = self._pending_opens.popleft()
                self._opened(session, outcome, time.monotonic())

    def _free(self) -> None:
        if self._workers is not None:
            self._workers.shutdown(wait=True)
            while self._pending_opens:
                session, outcome = self._pending_opens.popleft()
                self._opened(session, outcome, time.monotonic())
        if self._selector is not None:
            self._selector.close()
        if self._listener is not None:
            self._listener.close()
        for sock in (self._wake_r, self._wake_w):
            if sock is not None:
                sock.close()

    def _wake(self) -> None:
        try:
            self._wake_w.send(_WAKE_BYTE)
        except OSError:
            pass

    def __repr__(self) -> str:
        address = None if self._address is None else self._address[:2]
        return "TFTPServer(%r, %d active)" % (address, len(self._sessions))

    # -- event loop ---------------------------------------------------------

    def _drain_wake(self) -> None:
        try:
            while self._wake_r.recv(64):
                pass
        except OSError:
            pass
        now = time.monotonic()
        while self._pending_opens:
            session, outcome = self._pending_opens.popleft()
            try:
                self._opened(session, outcome, now)
            except Exception as exc:
                self._survive(session, exc)
        while self._ready:
            session = self._ready.popleft()
            transfer = session.transfer
            if session.closed or transfer is None or transfer.is_done:
                continue
            try:
                transfer.resume(now)
                if transfer.is_done:
                    self._done(session, now)
                else:
                    self._schedule(session)
            except Exception as exc:
                self._survive(session, exc)

    def _notifier(self, session: Session) -> Callable[[], None]:
        """A thread-safe callback resuming ``session`` on the loop thread."""

        def notify() -> None:
            self._ready.append(session)
            self._wake()

        return notify

    def _next_timeout(self, now: float) -> Optional[float]:
        if not self._timers:
            return None
        return max(0.0, self._timers[0][0] - now)

    def _schedule(self, session: Session) -> None:
        due = session.wakeup()
        if due is None or session.closed:
            return
        if session.timer_at is None or due < session.timer_at:
            self._seq += 1
            session.timer_at = due
            heapq.heappush(self._timers, (due, self._seq, session))

    def _run_timers(self, now: float) -> None:
        timers = self._timers
        while timers and timers[0][0] <= now:
            due, _, session = heapq.heappop(timers)
            if session.closed or session.timer_at != due:
                continue
            session.timer_at = None
            wake = session.wakeup()
            if wake is None:
                continue
            if wake > now:
                self._schedule(session)
                continue
            if session.linger_until is not None:
                self._release(session)
                continue
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

    def _on_packet(self, session: Session, now: float) -> None:
        if session.closed:
            return
        transfer = session.transfer
        assert transfer is not None
        recv = session.sock.recvfrom_into
        buf = self._buf
        view = self._view
        host = session.host
        port = session.port
        trace = session.trace
        # Lock-step (windowsize 1) brings one packet per wakeup: a second read
        # would only find the socket empty and raise, every block.
        for _ in range(_DRAIN if transfer.windowsize > 1 else 1):
            try:
                n, addr = recv(buf)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                continue
            if trace is not None:
                session.emit(view[:n], "in", addr)
            if addr[1] != port or addr[0] != host:
                try:
                    stray = _encode_error(TFTPErrorCode.UNKNOWN_TID)
                    session.sock.sendto(stray, addr)
                    if trace is not None:
                        session.emit(stray, "out", addr)
                except OSError:
                    pass
                continue
            was_done = transfer.is_done
            transfer.handle(view, n, now)
            if transfer.is_done and not was_done:
                self._done(session, now)
                if session.closed:
                    return
        self._schedule(session)

    # -- requests -----------------------------------------------------------

    def _on_request(self, now: float) -> None:
        for _ in range(_DRAIN):
            arrival = self._listener.recv()
            if arrival is None:
                return
            try:
                session = self._admit(arrival, now)
            except Exception:  # pragma: no cover - a bug, not a client error
                log.exception("request from %s failed", arrival.sender[:2])
                continue
            if session is None:
                continue
            session.notify = self._notifier(session)
            try:
                if self._workers is None:
                    self._opened(session, self._open(session), time.monotonic())
                else:
                    self._workers.submit(self._open_in_worker, session)
            except Exception as exc:
                self._survive(session, exc)

    def _open_in_worker(self, session: Session) -> None:
        outcome = self._open(session)
        self._pending_opens.append((session, outcome))
        self._wake()

    def _opened(self, session: Session, outcome: Any, now: float) -> None:
        """Loop thread: a session finished opening (``outcome`` is a transfer or an exception)."""
        if session.closed:  # the server stopped while a worker was opening it
            if isinstance(outcome, Transfer):
                outcome.abort("server shutting down")
            session.close_stream(ok=False)
            return
        if isinstance(outcome, BaseException):
            self._refuse(session, outcome)
            return
        session.transfer = outcome
        self.stats.add("started")
        self._selector.register(session.sock, selectors.EVENT_READ, session)
        if outcome.is_done:  # e.g. the first read failed
            self._done(session, now)
        else:
            self._schedule(session)

    # -- completion ---------------------------------------------------------

    def _done(self, session: Session, now: float) -> None:
        """The transfer finished: report it, then close or linger."""
        if self._finished(session, now):
            self._schedule(session)
        else:
            self._release(session)

    def _release(self, session: Session) -> None:
        if not self._forget(session):
            return
        try:
            self._selector.unregister(session.sock)
        except (KeyError, ValueError):
            pass
        session.sock.close()
