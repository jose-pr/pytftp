"""The server: one thread, one ``selectors`` event loop, any number of transfers.

The listening socket is read through :class:`netimps.UdpEndpoint`, which
reports each request's destination address (``IP_PKTINFO`` /
``IPV6_PKTINFO``) where the platform allows it; :mod:`.session` binds the
transfer's socket to it. Timers are a heap holding at most one live entry
per session: a deadline that moves later is re-queued only when its old
entry comes due, so the hot path never touches the heap.
"""

from __future__ import annotations

import collections
import heapq
import logging
import os
import selectors
import socket
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..errors import TftpError, error_for_exception
from ..options import Negotiated, ServerOptions
from ..packet import ErrorCode, MalformedPacket, Opcode, Request, decode, encode_error
from ..result import TransferResult
from ..transfer import Receiver, Transfer
from .handler import FileSystemHandler, RequestContext
from .listener import Listener
from .netinfo import InterfaceInfo
from .policy import ServerLimits
from .session import Session, reply_socket

__all__ = ["Server"]

log = logging.getLogger("tftp.server")

_WINDOWS = sys.platform == "win32"
#: select() on Windows handles at most 512 sockets.
_WINDOWS_SESSION_CAP = 500
_RECV_SIZE = 65536  # one receive buffer, shared by every session
_DRAIN = 64  # packets read per readiness event before yielding to others
_WAKE_BYTE = bytes(1)


class Server:
    """A TFTP server.

    :param root_or_handler: a directory to serve (wrapped in
        :class:`FileSystemHandler` with ``writable``, ``create`` and
        ``overwrite``), or a handler object.
    :param host: address to listen on. ``"::"`` (the default) listens on
        IPv6 and IPv4 at once where the platform allows dual-stack sockets,
        else falls back to ``"0.0.0.0"``. ``"0.0.0.0"`` is IPv4 only.
    :param port: UDP port; ``0`` picks a free one (see :attr:`server_address`).
    :param timeout: retransmission timeout, unless a client negotiates its own.
    :param retries: retransmissions of one packet before abandoning a transfer.
    :param options: what the server negotiates (:class:`ServerOptions`).
    :param max_sessions: concurrent transfers; requests beyond it get
        ERROR 0 "server busy". ``None`` is unlimited, except on Windows,
        where ``select()`` caps it at 500.
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
        (:class:`ServerLimits`).
    :param ignore_broadcast: silently drop requests sent to a broadcast or
        multicast address (RFC 1123 4.2.3.4). Needs pktinfo to see the
        destination; without it every request looks unicast.
    :param backoff: each consecutive retransmission waits this many times
        longer (RFC 1123 4.2.3.2); progress resets it.
    :param max_timeout: ceiling for the backed-off wait; ``None`` is eight
        times the timeout.
    """

    def __init__(
        self,
        root_or_handler: Any,
        host: str = "::",
        port: int = 69,
        *,
        writable: bool = False,
        create: bool = True,
        overwrite: bool = False,
        timeout: float = 1.0,
        retries: int = 5,
        options: Optional[ServerOptions] = None,
        max_sessions: Optional[int] = None,
        reply_from_request_address: bool = True,
        dally: bool = True,
        on_complete: Optional[Callable[[TransferResult], Any]] = None,
        limits: Optional[ServerLimits] = None,
        ignore_broadcast: bool = True,
        backoff: float = 2.0,
        max_timeout: Optional[float] = None,
    ) -> None:
        if isinstance(root_or_handler, (str, os.PathLike)):
            handler: Any = FileSystemHandler(
                root_or_handler, writable=writable, create=create, overwrite=overwrite
            )
        else:
            handler = root_or_handler
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.handler = handler
        self.timeout = timeout
        self.retries = retries
        self.options = options or ServerOptions()
        if max_sessions is None and _WINDOWS:
            max_sessions = _WINDOWS_SESSION_CAP
        self.max_sessions = max_sessions
        self.dally = dally
        self.on_complete = on_complete
        self.limits = limits or ServerLimits()
        self.ignore_broadcast = ignore_broadcast
        self.backoff = backoff
        self.max_timeout = max_timeout
        self._interfaces = InterfaceInfo()
        self._per_client: Dict[str, int] = {}
        self._ready: "collections.deque[Session]" = collections.deque()

        self._listener = Listener(host, port, pktinfo=reply_from_request_address)
        self._address: Tuple[Any, ...] = self._listener.sock.getsockname()

        self._selector = selectors.DefaultSelector()
        self._wake_r, self._wake_w = socket.socketpair()
        self._wake_r.setblocking(False)
        self._wake_w.setblocking(False)
        self._selector.register(self._listener.sock, selectors.EVENT_READ, None)
        self._selector.register(self._wake_r, selectors.EVENT_READ, self)
        self._sessions: Dict[Tuple[str, int], Session] = {}
        self._timers: List[Tuple[float, int, Session]] = []
        self._seq = 0
        self._buf = bytearray(_RECV_SIZE)
        self._view = memoryview(self._buf)
        self._stopping = False
        self._running = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._closed = False

    @property
    def server_address(self) -> Tuple[Any, ...]:
        """The bound listening address, e.g. to learn the port chosen for ``port=0``."""
        return self._address

    @property
    def supports_pktinfo(self) -> bool:
        """Replies come from the request's own destination address."""
        return self._listener.supports_pktinfo

    @property
    def dual_stack(self) -> bool:
        """The listening socket accepts IPv4 as well as IPv6."""
        return self._listener.dual_stack

    @property
    def active_sessions(self) -> int:
        return len(self._sessions)

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
                    if data is None:
                        self._on_request(clock())
                    elif data is self:
                        self._drain_wake()
                    else:
                        self._on_packet(data, clock())
                if self._timers and self._timers[0][0] <= clock():
                    self._run_timers(clock())
        finally:
            self._running.clear()
            for session in list(self._sessions.values()):
                if session.transfer is not None and not session.transfer.done:
                    session.transfer.abort("server shutting down")
                self._finish(session)

    def shutdown(self) -> None:
        """Stop :meth:`serve_forever`; safe from any thread or a handler."""
        self._stopping = True
        self._wake()

    def start(self) -> "Server":
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
        self._selector.close()
        self._listener.close()
        for sock in (self._wake_r, self._wake_w):
            sock.close()

    def __enter__(self) -> "Server":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return "Server(%r, %d active)" % (self.server_address[:2], len(self._sessions))

    # -- event loop ---------------------------------------------------------

    def _drain_wake(self) -> None:
        try:
            while self._wake_r.recv(64):
                pass
        except OSError:
            pass
        now = time.monotonic()
        while self._ready:
            session = self._ready.popleft()
            transfer = session.transfer
            if session.closed or transfer is None or transfer.done:
                continue
            transfer.resume(now)
            if transfer.done:
                self._done(session, now)
            else:
                self._schedule(session)

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
                self._close(session)
                continue
            transfer = session.transfer
            assert transfer is not None
            transfer.on_timeout(now)
            if transfer.done:
                self._done(session, now)
            else:
                self._schedule(session)

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
        for _ in range(_DRAIN):
            try:
                n, addr = recv(buf)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                continue
            if addr[1] != port or addr[0] != host:
                try:
                    session.sock.sendto(encode_error(ErrorCode.UNKNOWN_TID), addr)
                except OSError:
                    pass
                continue
            was_done = transfer.done
            transfer.handle(view, n, now)
            if transfer.done and not was_done:
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
            data, sender, local, ifindex = arrival
            if len(data) < 2 or data[0] != 0 or data[1] not in (Opcode.RRQ, Opcode.WRQ):
                continue  # not a request: never answer, never amplify
            if self.ignore_broadcast and local is not None and self._interfaces.is_broadcast(local, ifindex):
                log.debug("ignoring broadcast request from %s to %s", sender[:2], local)
                continue
            if len(data) > self.limits.max_request_size:
                self._listener.reply_error(sender, ErrorCode.ILLEGAL_OPERATION, "request too large")
                continue
            try:
                self._start(data, sender, local, ifindex, now)
            except Exception:  # pragma: no cover - a bug, not a client error
                log.exception("request from %s failed", sender[:2])

    def _start(
        self, data: bytes, sender: Tuple[Any, ...], local: Optional[str], ifindex: int, now: float
    ) -> None:
        from netimps import unmap

        key = (str(unmap(sender[0])), sender[1])
        if key in self._sessions:
            return  # a retransmitted request for a transfer already running
        try:
            request = decode(data)
        except MalformedPacket as exc:
            self._listener.reply_error(sender, ErrorCode.ILLEGAL_OPERATION, str(exc))
            return
        assert isinstance(request, Request)
        try:
            self.limits.check(request)
        except TftpError as exc:
            self._listener.reply_error(sender, exc.code, exc.message)
            return
        if self.max_sessions is not None and len(self._sessions) >= self.max_sessions:
            self._listener.reply_error(sender, ErrorCode.NOT_DEFINED, "server busy")
            return
        per_client = self.limits.max_sessions_per_client
        if per_client is not None and self._per_client.get(key[0], 0) >= per_client:
            self._listener.reply_error(sender, ErrorCode.NOT_DEFINED, "server busy")
            return

        sock, peer = reply_socket(self._listener.family, self._listener.host, sender, local, ifindex)
        context = RequestContext(request, peer, local, ifindex)
        session = Session(sock, peer, context, now)
        session.notify = self._notifier(session)
        max_duration = self.limits.max_duration
        try:
            transfer = session.open(
                self.handler,
                self.options,
                self.timeout,
                self.retries,
                now,
                mtu=self._interfaces.mtu(ifindex) if self.options.fit_mtu else None,
                backoff=self.backoff,
                max_timeout=self.max_timeout,
                expires=None if max_duration is None else now + max_duration,
            )
        except Exception as exc:
            error = error_for_exception(exc)
            if not isinstance(exc, (TftpError, OSError)):
                log.exception("handler failed for %r", context)
            log.info("%s refused: %s", context, error)
            session.send(encode_error(error.code, error.message))
            session.close_stream(ok=False)
            sock.close()
            self._report(session, error, None)
            return
        session.transfer = transfer
        self._sessions[session.key] = session
        self._per_client[session.key[0]] = self._per_client.get(session.key[0], 0) + 1
        self._selector.register(sock, selectors.EVENT_READ, session)
        if transfer.done:  # e.g. the first read failed
            self._done(session, now)
        else:
            self._schedule(session)

    # -- completion ---------------------------------------------------------

    def _done(self, session: Session, now: float) -> None:
        """The transfer finished: report it, then close or linger."""
        transfer = session.transfer
        assert transfer is not None
        ok = transfer.error is None
        session.close_stream(ok)
        self._report(session, transfer.error, transfer)
        if ok and self.dally and isinstance(transfer, Receiver):
            session.linger_until = now + transfer.timeout
            self._schedule(session)
        else:
            self._close(session)

    def _finish(self, session: Session) -> None:
        if session.closed:
            return
        if session.transfer is not None and session.stream is not None:
            session.close_stream(session.transfer.error is None and session.transfer.done)
        self._close(session)

    def _close(self, session: Session) -> None:
        if session.closed:
            return
        session.closed = True
        if self._sessions.pop(session.key, None) is not None:
            host = session.key[0]
            left = self._per_client.get(host, 1) - 1
            if left > 0:
                self._per_client[host] = left
            else:
                self._per_client.pop(host, None)
        try:
            self._selector.unregister(session.sock)
        except (KeyError, ValueError):
            pass
        session.sock.close()

    def _report(self, session: Session, error: Optional[TftpError], transfer: Optional[Transfer]) -> None:
        context = session.context
        request = context.request
        duration = time.monotonic() - session.started
        result = TransferResult(
            request.filename,
            "read" if request.is_read else "write",
            request.mode,
            session.peer,
            session.local,
            transfer.bytes if transfer else 0,
            transfer.blocks if transfer else 0,
            transfer.retransmits if transfer else 0,
            duration,
            transfer.negotiated if transfer else Negotiated(timeout=self.timeout),
            error,
        )
        if error is None:
            log.info(
                "%s %r %s %s: %d bytes in %.3fs",
                "sent" if request.is_read else "received",
                request.filename,
                "to" if request.is_read else "from",
                session.peer[0],
                result.bytes,
                duration,
            )
        elif transfer is not None:
            log.warning(
                "%s %r with %s failed: %s", result.operation, request.filename, session.peer[0], error
            )
        if self.on_complete is not None:
            try:
                self.on_complete(result)
            except Exception:
                log.exception("on_complete callback failed")
