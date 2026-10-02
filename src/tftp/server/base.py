"""What every server front end shares: configuration, admission, reporting.

:class:`tftp.Server` (a ``selectors`` loop) and :class:`tftp.aio.AsyncServer`
(asyncio) differ only in how they wait for sockets and timers; deciding
whether a request becomes a transfer, refusing it, and reporting the outcome
live here so the two cannot drift apart.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any, Callable, Dict, Optional, Tuple

from ..capture.events import PacketEvent
from ..errors import TftpError, error_for_exception
from ..options import Negotiated, ServerOptions
from ..packet import ErrorCode, MalformedPacket, Opcode, Request, decode, encode_error
from ..result import TransferResult
from ..transfer import Receiver, Transfer
from .handler import FileSystemHandler, RequestContext
from .listener import Arrival, Listener
from .netinfo import InterfaceInfo
from .policy import ServerLimits
from .session import Session, reply_socket
from .stats import SERVER_COUNTERS, Stats

__all__ = ["ServerBase"]

log = logging.getLogger("tftp.server")

_WINDOWS = sys.platform == "win32"
#: select() on Windows handles at most 512 sockets.
WINDOWS_SESSION_CAP = 500


class ServerBase:
    """Configuration and per-request decisions shared by the server front ends."""

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
        trace: Optional[Callable[[PacketEvent], Any]] = None,
        session_cap: Optional[int] = None,
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
        if max_sessions is None:
            max_sessions = session_cap
        self.max_sessions = max_sessions
        self.dally = dally
        self.on_complete = on_complete
        self.limits = limits or ServerLimits()
        self.ignore_broadcast = ignore_broadcast
        self.backoff = backoff
        self.max_timeout = max_timeout
        self.trace = trace
        self._interfaces = InterfaceInfo()
        self._sessions: Dict[Tuple[str, int], Session] = {}
        self._per_client: Dict[str, int] = {}
        #: Counters since start (:class:`Stats`); ``stats_snapshot()`` adds ``active``.
        self.stats = Stats(*SERVER_COUNTERS)
        self._listener = Listener(host, port, pktinfo=reply_from_request_address)
        self._address: Tuple[Any, ...] = self._listener.sock.getsockname()

    # -- properties -----------------------------------------------------------------

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

    def stats_snapshot(self) -> Dict[str, int]:
        """Every counter plus ``active`` (transfers in progress), for metrics."""
        snapshot = self.stats.snapshot()
        snapshot["active"] = len(self._sessions)
        return snapshot

    # -- admission -------------------------------------------------------------------

    def _admit(self, arrival: Arrival, now: float) -> Optional[Session]:
        """Turn a datagram on the listening port into a session, or answer/drop it.

        Returns the new session (registered, with its transfer socket) or
        ``None`` when the datagram was dropped or refused.
        """
        data, sender, local, ifindex = arrival
        if len(data) < 2 or data[0] != 0 or data[1] not in (Opcode.RRQ, Opcode.WRQ):
            return None  # not a request: never answer, never amplify
        if self.ignore_broadcast and local is not None and self._interfaces.is_broadcast(local, ifindex):
            log.debug("ignoring broadcast request from %s to %s", sender[:2], local)
            return None
        self.stats.add("requests")
        if len(data) > self.limits.max_request_size:
            self.stats.add("refused")
            self._listener.reply_error(sender, ErrorCode.ILLEGAL_OPERATION, "request too large")
            return None
        from netimps import unmap

        key = (str(unmap(sender[0])), sender[1])
        if key in self._sessions:
            return None  # a retransmitted request for a transfer already running
        try:
            request = decode(data)
        except MalformedPacket as exc:
            self.stats.add("refused")
            self._listener.reply_error(sender, ErrorCode.ILLEGAL_OPERATION, str(exc))
            return None
        assert isinstance(request, Request)
        try:
            self.limits.check(request)
        except TftpError as exc:
            self.stats.add("refused")
            self._listener.reply_error(sender, exc.code, exc.message)
            return None
        per_client = self.limits.max_sessions_per_client
        if (self.max_sessions is not None and len(self._sessions) >= self.max_sessions) or (
            per_client is not None and self._per_client.get(key[0], 0) >= per_client
        ):
            self.stats.add("refused")
            self._listener.reply_error(sender, ErrorCode.NOT_DEFINED, "server busy")
            return None

        sock, peer = reply_socket(self._listener.family, self._listener.host, sender, local, ifindex)
        session = Session(sock, peer, RequestContext(request, peer, local, ifindex), now)
        if self.trace is not None:
            session.trace = self.trace
            # The request arrived at the listening port, not the transfer's.
            arrived = (local, self._address[1]) if local is not None else self._address[:2]
            session.emit(data, "in", sender, arrived)
        # Registered at once, so a retransmitted request is recognised while
        # the stream is still being opened.
        self._sessions[session.key] = session
        self._per_client[session.key[0]] = self._per_client.get(session.key[0], 0) + 1
        return session

    def _mtu(self, session: Session) -> Optional[int]:
        return self._interfaces.mtu(session.context.interface_index) if self.options.fit_mtu else None

    def _engine(self, now: float) -> Dict[str, Any]:
        """Keyword arguments for a session's transfer."""
        max_duration = self.limits.max_duration
        return {
            "backoff": self.backoff,
            "max_timeout": self.max_timeout,
            "expires": None if max_duration is None else now + max_duration,
        }

    def _open(self, session: Session) -> Any:
        """Open synchronously: the transfer, or the exception that refused it."""
        now = time.monotonic()
        try:
            return session.open(
                self.handler,
                self.options,
                self.timeout,
                self.retries,
                now,
                mtu=self._mtu(session),
                **self._engine(now),
            )
        except Exception as exc:
            return exc

    # -- outcomes -----------------------------------------------------------------------

    def _refuse(self, session: Session, exc: BaseException) -> None:
        """The handler (or the mode) refused: ERROR to the client, report, release."""
        error = error_for_exception(exc)
        if not isinstance(exc, (TftpError, OSError)):
            log.error("handler failed for %r", session.context, exc_info=exc)
        log.info("%s refused: %s", session.context, error)
        self.stats.add("refused")
        session.send(encode_error(error.code, error.message))
        session.close_stream(ok=False)
        self._release(session)
        self._report(session, error, None)

    def _finished(self, session: Session, now: float) -> bool:
        """Report a finished transfer; ``True`` when its socket should linger (dally)."""
        transfer = session.transfer
        assert transfer is not None
        ok = transfer.error is None
        session.close_stream(ok)
        self._report(session, transfer.error, transfer)
        if ok and self.dally and isinstance(transfer, Receiver):
            session.linger_until = now + transfer.timeout
            return True
        return False

    def _forget(self, session: Session) -> bool:
        """Mark closed and drop the bookkeeping; ``False`` if it already was."""
        if session.closed:
            return False
        session.closed = True
        if self._sessions.pop(session.key, None) is not None:
            host = session.key[0]
            left = self._per_client.get(host, 1) - 1
            if left > 0:
                self._per_client[host] = left
            else:
                self._per_client.pop(host, None)
        return True

    def _release(self, session: Session) -> None:  # pragma: no cover - front ends override
        if self._forget(session):
            session.sock.close()

    def _report(self, session: Session, error: Optional[TftpError], transfer: Optional[Transfer]) -> None:
        request = session.context.request
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
        stats = self.stats
        if transfer is not None:
            stats.add("completed" if error is None else "failed")
            stats.add("bytes_sent" if request.is_read else "bytes_received", transfer.bytes)
            stats.add("retransmits", transfer.retransmits)
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
