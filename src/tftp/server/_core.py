"""What every server front end shares: configuration, admission, reporting.

:class:`tftp.TFTPServer` (a ``selectors`` loop) and :class:`tftp.AsyncTFTPServer`
(asyncio) differ only in how they wait for sockets and timers; deciding
whether a request becomes a transfer, refusing it, and reporting the outcome
live here so the two cannot drift apart. Nothing here opens a socket until
``bind()``.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Tuple

if TYPE_CHECKING:
    from netimps import Host, IPAddressLike

from .._text import Escaped
from ..capture._hook import HookGuard, guard
from ..capture.events import PacketEvent
from ..exceptions import RemoteError, TFTPDecodeError, TFTPError, TransferAbortedError
from ..options import Negotiated, TFTPServerOptions
from ..packet import TFTPErrorCode, TFTPOpcode, RequestPacket, decode
from ..packet.codec import _encode_error
from ..result import TransferResult
from ..transfer import Receiver, Transfer
from .handler import TFTPRequestContext
from .listener import Arrival, Listener
from .policy import TFTPServerLimits
from .session import PortAllocator, Session, as_port_range
from .stats import SERVER_COUNTERS, TFTPStats

__all__ = ["ServerBase"]

log = logging.getLogger("tftp.server")

_WINDOWS = sys.platform == "win32"
#: Concurrent transfers by default.
DEFAULT_MAX_SESSIONS = 500
#: select() on Windows watches 512 descriptors; the listener and the wake socket take two.
SELECT_SESSIONS = 510


class ServerBase:
    """Configuration and per-request decisions shared by the server front ends."""

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
        port_range: Any = None,
        interface: Any = None,
    ) -> None:
        if interface is not None and host:
            from netimps import is_wildcard

            if not is_wildcard(host):
                raise ValueError(
                    "give host or interface, not both (host may be '0.0.0.0' or '::' to pick the family)"
                )
        if isinstance(root_or_handler, (str, os.PathLike)):
            from ..backends.filesystem import FilesystemBackend

            handler: Any = FilesystemBackend(
                root_or_handler, writable=writable, create=create, overwrite=overwrite
            )
        else:
            handler = root_or_handler
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.handler = handler
        self.timeout = timeout
        self.retries = retries
        self.options = options or TFTPServerOptions()
        if max_sessions is None and _WINDOWS:
            max_sessions = SELECT_SESSIONS  # a selector loop cannot watch more
        self.max_sessions = max_sessions
        self.dally = dally
        self.on_complete = on_complete
        self.limits = limits or TFTPServerLimits()
        self.ignore_broadcast = ignore_broadcast
        self.backoff = backoff
        self.max_timeout = max_timeout
        self.trace = trace
        self._trace_guard: Optional[HookGuard] = None
        #: Where transfer sockets take their ports (:class:`PortRange`), or ``None``.
        self.port_range = as_port_range(port_range)
        self._ports = PortAllocator(self.port_range) if self.port_range is not None else None
        self._sessions: Dict[Tuple[str, int], Session] = {}
        self._per_client: Dict[str, int] = {}
        #: Counters since start (:class:`TFTPStats`); ``stats_snapshot()`` adds ``active``.
        self.stats = TFTPStats(*SERVER_COUNTERS)
        self._listen_on = (host, port, reply_from_request_address, interface)
        self._listener: Any = None
        self._address: Optional[Tuple[Any, ...]] = None

    # -- the listening socket -------------------------------------------------------

    def _bind_listener(self) -> Listener:
        """A bound listener; the caller owns it and closes it if a later step fails."""
        host, port, pktinfo, interface = self._listen_on
        return Listener(host, port, pktinfo=pktinfo, interface=interface)

    # -- properties -----------------------------------------------------------------

    @property
    def server_address(self) -> Optional[Tuple[Any, ...]]:
        """The bound listening address, or ``None`` before :meth:`bind`.

        Read it to learn the port chosen for ``port=0``.
        """
        return self._address

    @property
    def has_pktinfo(self) -> Optional[bool]:
        """Replies come from the request's own destination address; ``None`` before :meth:`bind`."""
        return None if self._listener is None else self._listener.has_pktinfo

    @property
    def is_dual_stack(self) -> Optional[bool]:
        """The listening socket accepts IPv4 as well as IPv6; ``None`` before :meth:`bind`."""
        return None if self._listener is None else self._listener.is_dual_stack

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
        data, sender, local, ifindex = arrival[:4]
        if len(data) < 2 or data[0] != 0 or data[1] not in (TFTPOpcode.RRQ, TFTPOpcode.WRQ):
            return None  # not a request: never answer, never amplify
        if self.ignore_broadcast and self._listener.is_broadcast(arrival):
            log.debug("ignoring broadcast request from %s to %s", sender[:2], local)
            return None
        self.stats.add("requests")
        if len(data) > self.limits.max_request_size:
            self.stats.add("refused")
            self._listener.reply_error(sender, TFTPErrorCode.ILLEGAL_OPERATION, "request too large")
            return None
        from netimps import unmap

        key = (str(unmap(sender[0])), sender[1])
        if key in self._sessions:
            return None  # a retransmitted request for a transfer already running
        try:
            request = decode(data)
        except TFTPDecodeError as exc:
            self.stats.add("refused")
            self._listener.reply_error(sender, TFTPErrorCode.ILLEGAL_OPERATION, str(exc))
            return None
        assert isinstance(request, RequestPacket)
        try:
            self.limits.check(request)
        except TFTPError as exc:
            self.stats.add("refused")
            self._listener.reply_error(sender, exc.code, exc.message)
            return None
        per_client = self.limits.max_sessions_per_client
        if (self.max_sessions is not None and len(self._sessions) >= self.max_sessions) or (
            per_client is not None and self._per_client.get(key[0], 0) >= per_client
        ):
            self.stats.add("refused")
            self._listener.reply_error(sender, TFTPErrorCode.NOT_DEFINED, "server busy")
            return None

        try:
            sock, peer = self._listener.reply_socket(arrival, self._ports)
        except OSError as exc:
            log.warning("no transfer socket for %s: %s", sender[:2], exc)
            self.stats.add("refused")
            self._listener.reply_error(sender, TFTPErrorCode.NOT_DEFINED, "server busy")
            return None
        context = TFTPRequestContext(request, peer, local_address=local, interface_index=ifindex)
        context.interface = arrival.interface
        context.listing = (
            request.is_read
            and request.options.get("x-list", "").strip() == "1"
            and self.options.accepts("x-list")
        )
        session = Session(sock, peer, context, now)
        if self.trace is not None:
            self._trace_guard = guard(self.trace, log, self._trace_guard)
            session.trace = self._trace_guard
            # The request arrived at the listening port, not the transfer's.
            arrived = (local, self._address[1]) if local is not None else self._address[:2]
            session.emit(data, "in", sender, arrived)
        # Registered at once, so a retransmitted request is recognised while
        # the stream is still being opened.
        self._sessions[session.key] = session
        self._per_client[session.key[0]] = self._per_client.get(session.key[0], 0) + 1
        return session

    def _mtu(self, session: Session) -> Optional[int]:
        interface = session.context.interface
        return interface.mtu if self.options.fit_mtu and interface is not None else None

    def _engine(self, now: float) -> Dict[str, Any]:
        """Keyword arguments for a session's transfer."""
        max_duration = self.limits.max_duration
        return {
            "backoff": self.backoff,
            "max_timeout": self.max_timeout,
            "expires": None if max_duration is None else now + max_duration,
            "max_idle": self.limits.max_idle,
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
        if session.closed:  # the server stopped while the handler was opening it: already reported
            session.close_stream(ok=False)
            return
        error = TFTPError.from_exception(exc)
        if not isinstance(exc, (TFTPError, OSError)):
            log.error("handler failed for %r", session.context, exc_info=exc)
        # An OSError is the host's reason (a refused send, a file system error) and stays in the log.
        log.info("%s refused: %s", session.context, Escaped(exc if isinstance(exc, OSError) else error))
        self.stats.add("refused")
        try:
            try:
                session.send(_encode_error(error.code, error.message))
            except OSError:
                pass  # the peer cannot be reached either
            session.close_stream(ok=False)
        finally:
            self._release(session)
        self._report(session, error, None)

    def _abandon(self, session: Session) -> None:
        """Serving stops with ``session`` in flight: the peer gets ERROR 0, the result reports the abort.

        A transfer that already finished was reported when it did; a request
        whose handler had not returned yet has no transfer and counts as refused.
        """
        if session.closed:
            return
        try:
            transfer = session.transfer
            if transfer is None:
                self.stats.add("refused")
                session.close_stream(ok=False)
                self._report(session, TransferAbortedError("server shutting down"), None)
            elif not transfer.is_done:
                transfer.abort("server shutting down")
                session.close_stream(ok=False)
                self._report(session, transfer.error, transfer)
        except Exception:
            log.exception("could not report the transfer that was in flight at shutdown")
        finally:
            self._release(session)

    def _survive(self, session: Optional[Session], exc: BaseException) -> None:
        """An exception escaped one dispatch: end that transfer, keep serving.

        Logged once, with its traceback, so a bug is seen; the client gets
        ERROR 0 and every other transfer carries on.
        """
        if session is not None and isinstance(exc, OSError):
            # The host refused a send or reset the socket: this transfer ends, the loop is fine.
            log.warning("%r ended: %s", session.context, exc)
        else:
            log.error(
                "unexpected failure in the event loop%s",
                " (ending that transfer)" if session else "",
                exc_info=exc,
            )
        if session is None or session.closed:
            return
        try:
            transfer = session.transfer
            if transfer is None:
                self._refuse(session, exc)
                return
            if not transfer.is_done:
                transfer.fail(exc)
            session.close_stream(transfer.error is None)
            self._report(session, transfer.error, transfer)
        except Exception:
            log.exception("could not end the transfer cleanly")
        finally:
            self._release(session)

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
        # A stale timer entry may outlive the session; it must not keep the
        # transfer, and with it the window ring, alive.
        session.transfer = None
        session.stream = None
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

    def _report(self, session: Session, error: Optional[TFTPError], transfer: Optional[Transfer]) -> None:
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
        # The client refused our OACK before any data moved: not a failure
        # but a size probe (EDK2 PXE asks for tsize this way before every
        # download) or a client that dislikes an option.
        declined = (
            transfer is not None
            and isinstance(error, RemoteError)
            and error.code == TFTPErrorCode.OPTION_REFUSED
            and transfer.bytes == 0
        )
        if transfer is not None:
            stats.add("declined" if declined else "completed" if error is None else "failed")
            stats.add("bytes_sent" if request.is_read else "bytes_received", transfer.bytes)
            stats.add("retransmits", transfer.retransmits)
        if declined:
            log.info(
                "%s %r: %s declined the options (%s)",
                result.operation,
                request.filename,
                session.peer[0],
                Escaped(error.message),  # type: ignore[union-attr]
            )
        elif error is None:
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
                "%s %r with %s failed: %s",
                result.operation,
                request.filename,
                session.peer[0],
                Escaped(error),
            )
        if self.on_complete is not None:
            try:
                self.on_complete(result)
            except Exception:
                log.exception("on_complete callback failed")
