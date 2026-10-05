"""The server: one thread, one ``selectors`` event loop, any number of transfers.

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
import heapq
import logging
import selectors
import socket
import sys
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, List, Optional, Tuple

if TYPE_CHECKING:
    from netimps import Host, IPAddressLike

from ..capture.events import PacketEvent
from ..options import TFTPServerOptions
from ..packet import TFTPErrorCode
from ..packet.codec import _encode_error
from ..result import TransferResult
from ..transfer import Transfer
from .base import DEFAULT_MAX_SESSIONS, SELECT_SESSIONS, ServerBase
from .policy import TFTPServerLimits
from .session import Session

__all__ = ["TFTPServer"]

log = logging.getLogger("tftp.server")

_RECV_SIZE = 65536  # one receive buffer, shared by every session
_DRAIN = 64  # packets read per readiness event before yielding to others
_WAKE_BYTE = bytes(1)


class TFTPServer(ServerBase):
    """A TFTP server.

    :param root_or_handler: a directory to serve (wrapped in
        :class:`FilesystemBackend` with ``writable``, ``create`` and
        ``overwrite``), or a handler object.
    :param host: address to listen on: a string, an ``ipaddress`` address or a
        ``netimps.Host``. ``None`` (the default) or ``"::"`` listens on IPv6
        and IPv4 at once where the platform allows dual-stack sockets, else
        falls back to ``"0.0.0.0"``. ``"0.0.0.0"`` is IPv4 only.
    :param port: UDP port; ``0`` picks a free one (see :attr:`server_address`).
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
        the handler: those marked ``_tftp_fast_open_ = True`` (the built-in
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
        host: "IPAddressLike | Host | None" = None,
        port: int = 69,
        *,
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
        if sys.platform == "win32" and max_sessions is not None and max_sessions > SELECT_SESSIONS:
            raise ValueError(
                "max_sessions=%d is more than the %d transfers select() can watch on Windows"
                % (max_sessions, SELECT_SESSIONS)
            )
        super().__init__(
            root_or_handler,
            host,
            port,
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
        self._ready: "collections.deque[Session]" = collections.deque()
        self._pending_opens: "collections.deque[Tuple[Session, Any]]" = collections.deque()
        if open_in_thread is None:
            open_in_thread = not getattr(self.handler, "_tftp_fast_open_", False)
        self._workers = (
            concurrent.futures.ThreadPoolExecutor(max(1, workers), thread_name_prefix="tftp-open")
            if open_in_thread
            else None
        )
        self._selector = selectors.DefaultSelector()
        self._wake_r, self._wake_w = socket.socketpair()
        self._wake_r.setblocking(False)
        self._wake_w.setblocking(False)
        self._selector.register(self._listener.sock, selectors.EVENT_READ, None)
        self._selector.register(self._wake_r, selectors.EVENT_READ, self)
        self._timers: List[Tuple[float, int, Session]] = []
        self._seq = 0
        self._buf = bytearray(_RECV_SIZE)
        self._view = memoryview(self._buf)
        self._stopping = False
        self._running = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._closed = False

    # -- lifecycle ----------------------------------------------------------

    def serve_forever(self) -> None:
        """Run the event loop until :meth:`shutdown`."""
        if self._closed:
            raise RuntimeError("server is closed")
        self._stopping = False
        self._running.set()
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
            self._running.clear()
            for session in list(self._sessions.values()):
                if session.transfer is not None and not session.transfer.is_done:
                    session.transfer.abort("server shutting down")
                if session.transfer is not None and session.stream is not None:
                    session.close_stream(session.transfer.error is None and session.transfer.is_done)
                self._release(session)
            while self._pending_opens:
                session, outcome = self._pending_opens.popleft()
                self._opened(session, outcome, time.monotonic())

    def shutdown(self) -> None:
        """Stop :meth:`serve_forever`; safe from any thread or a handler."""
        self._stopping = True
        self._wake()

    def start(self) -> "TFTPServer":
        """Run :meth:`serve_forever` in a daemon thread; returns ``self``."""
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("server already running")
        self._thread = threading.Thread(target=self.serve_forever, name="tftp-server", daemon=True)
        self._thread.start()
        self._running.wait(5)
        return self

    def stop(self, timeout: Optional[float] = 5.0) -> None:
        """:meth:`shutdown`, then wait for the :meth:`start` thread to exit."""
        self.shutdown()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def close(self) -> None:
        """Stop serving and release every socket."""
        if self._closed:
            return
        self.stop()
        self._closed = True
        if self._workers is not None:
            self._workers.shutdown(wait=True)
            while self._pending_opens:
                session, outcome = self._pending_opens.popleft()
                self._opened(session, outcome, time.monotonic())
        self._selector.close()
        self._listener.close()
        for sock in (self._wake_r, self._wake_w):
            sock.close()

    def __enter__(self) -> "TFTPServer":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return "TFTPServer(%r, %d active)" % (self.server_address[:2], len(self._sessions))

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

    def _wake(self) -> None:
        try:
            self._wake_w.send(_WAKE_BYTE)
        except OSError:
            pass

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
