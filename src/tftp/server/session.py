"""One server-side transfer: its socket, its stream, and how it starts.

Every transfer gets its own UDP socket -- its transfer ID (RFC 1350
section 4) -- bound to the address the request was sent to when that is
known, so a multi-homed or virtual-IP host answers from the address the
client used (``Listener.reply_socket``, on netimps' ``UDPEndpoint``).
"""

from __future__ import annotations

import logging
import os
import socket
import time
from typing import Any, Callable, Optional, Tuple

from .._sockets import fit_window
from ..exceptions import TFTPError
from ..netascii import NetasciiReader, NetasciiWriter, encoded_size
from ..options import Negotiated, ServerOptions, negotiate
from ..packet import TFTPErrorCode, RequestPacket, encode_ack, encode_oack
from ..transfer import Receiver, Sender, Transfer, as_readinto, as_write
from ..capture.events import PacketEvent, new_session_id
from .handler import TFTPRequestContext

__all__ = ["Session", "PortRange", "bind_transfer", "stream_size"]

log = logging.getLogger("tftp.server")


def stream_size(stream: Any) -> Optional[int]:
    """Bytes left in ``stream``: its ``size`` attribute, ``fstat``, or seeking."""
    size = getattr(stream, "size", None)
    if isinstance(size, int) and not isinstance(size, bool):
        return size
    try:
        return os.fstat(stream.fileno()).st_size
    except (AttributeError, OSError, ValueError):
        pass
    try:
        here = stream.tell()
        end = stream.seek(0, os.SEEK_END)
        stream.seek(here)
        return end - here
    except (AttributeError, OSError, ValueError):
        return None


class PortRange:
    """The UDP ports transfer sockets may use, ``low`` to ``high`` inclusive.

    Each transfer needs a port of its own; pinning them to a range lets a
    firewall allow them (tftp-hpa ``-R``, dnsmasq ``--tftp-port-range``).
    Ports are tried round-robin from where the last search stopped, so a
    port just released is the last to be reused.
    """

    __slots__ = ("low", "high", "_next")

    def __init__(self, low: int, high: int) -> None:
        if not 1 <= low <= high <= 65535:
            raise ValueError("port range must satisfy 1 <= low <= high <= 65535")
        self.low = low
        self.high = high
        self._next = low

    @classmethod
    def of(cls, value: Any) -> "Optional[PortRange]":
        """``None``, a :class:`PortRange`, a ``(low, high)`` pair or a ``range``."""
        if value is None or isinstance(value, PortRange):
            return value
        if isinstance(value, range):
            if value.step != 1 or not len(value):
                raise ValueError("port range must be a non-empty range with step 1")
            return cls(value.start, value.stop - 1)
        low, high = value
        return cls(int(low), int(high))

    def __len__(self) -> int:
        return self.high - self.low + 1

    def ordered(self) -> "list[int]":
        """Every port once, starting after the last one handed out."""
        start = self._next
        return list(range(start, self.high + 1)) + list(range(self.low, start))

    def taken(self, port: int) -> None:
        """``port`` was just handed out: the next search starts after it."""
        self._next = port + 1 if port < self.high else self.low

    def __iter__(self):
        return iter(self.ordered())

    def __repr__(self) -> str:
        return "PortRange(%d, %d)" % (self.low, self.high)


def bind_transfer(host: Any, family: int, ports: Optional[PortRange] = None) -> socket.socket:
    """A non-blocking UDP socket on ``host``: any port, or a free one in ``ports``.

    Raises :class:`netimps.AddressInUseError` when every port in the range
    is taken, and any other ``OSError`` (an address that cannot be bound) as is.
    """
    from netimps import AddressInUseError, bind

    if ports is None:
        sock = bind(host, 0, family=family)
    else:
        for port in ports.ordered():
            try:
                sock = bind(host, port, family=family)
                break
            except AddressInUseError:
                continue
        else:
            raise AddressInUseError("no free port in %d..%d" % (ports.low, ports.high))
        ports.taken(port)
    sock.setblocking(False)
    return sock


class Session:
    """A transfer in progress on the server, and the state the loop keeps for it."""

    __slots__ = (
        "sock",
        "peer",
        "host",
        "port",
        "transfer",
        "context",
        "stream",
        "started",
        "timer_at",
        "linger_until",
        "closed",
        "key",
        "local",
        "notify",
        "id",
        "trace",
        "_negotiated",
        "driver",
    )

    def __init__(
        self, sock: socket.socket, peer: Tuple[Any, ...], context: TFTPRequestContext, started: float
    ) -> None:
        self.sock = sock
        self.peer = peer
        self.host = peer[0]
        self.port = peer[1]
        self.context = context
        self.started = started
        self.transfer: Optional[Transfer] = None
        self.stream: Any = None
        self.timer_at: Optional[float] = None
        self.linger_until: Optional[float] = None
        self.closed = False
        from netimps import unmap

        self.key: Tuple[str, int] = (str(unmap(peer[0])), peer[1])
        self.local: Tuple[Any, ...] = sock.getsockname()
        #: Thread-safe "this transfer can make progress again" callback,
        #: handed to streams that support ``set_wakeup`` (see WouldBlock).
        self.notify: Optional[Callable[[], None]] = None
        self.id = new_session_id("s")
        #: ``trace(PacketEvent)`` for this transfer's datagrams, or ``None``.
        self.trace: Optional[Callable[[PacketEvent], Any]] = None
        self._negotiated: Optional[Negotiated] = None  # a WRQ's, from call_handler
        #: Front-end state (the async server keeps its timer and transport here).
        self.driver: Any = None

    def wakeup(self) -> Optional[float]:
        """When the loop must next look at this session, or ``None``."""
        if self.linger_until is not None:
            return self.linger_until
        return self.transfer.deadline if self.transfer is not None else None

    def send(self, packet) -> None:
        try:
            self.sock.sendto(packet, self.peer)
        except OSError:
            # A full send buffer or a transient route error is loss, which
            # the transfer's timeout already recovers from.
            return
        if self.trace is not None:
            self.emit(packet, "out", self.peer)

    def emit(
        self, data, direction: str, remote: Tuple[Any, ...], local: Optional[Tuple[Any, ...]] = None
    ) -> None:
        """Report one datagram to the trace hook (exceptions are logged, not raised)."""
        try:
            self.trace(  # type: ignore[misc]
                PacketEvent(
                    time.time(), direction, local or self.local, remote, bytes(data), "server", self.id
                )
            )
        except Exception:
            log.exception("trace hook failed")

    def call_handler(
        self, handler: Any, policy: ServerOptions, timeout: float, mtu: Optional[int] = None
    ) -> Any:
        """Step 1: ask ``handler`` for the stream (may return an awaitable).

        Raises whatever the handler raises, or :class:`TFTPError` for an
        unsupported mode; the caller turns either into an ERROR packet. A WRQ
        is negotiated here, since ``open_write`` receives the agreed ``tsize``.
        """
        request: RequestPacket = self.context.request
        mode = request.mode
        if mode == "mail":
            raise TFTPError(TFTPErrorCode.ILLEGAL_OPERATION, "mail mode is not supported")
        if mode not in ("octet", "netascii"):
            raise TFTPError(TFTPErrorCode.ILLEGAL_OPERATION, "unknown mode %r" % mode)
        if request.is_read:
            return handler.open_read(self.context)
        self._negotiated = negotiate(
            request.options, policy, is_read=False, timeout=timeout, mtu=mtu, ipv6=self._ipv6
        )
        return handler.open_write(self.context, self._negotiated.tsize)

    def start(
        self,
        stream: Any,
        policy: ServerOptions,
        timeout: float,
        retries: int,
        now: float,
        mtu: Optional[int] = None,
        **engine: Any,
    ) -> Transfer:
        """Step 2: negotiate (RRQ) and build the transfer around ``stream``.

        ``engine`` goes to the transfer (``backoff``, ``max_timeout``,
        ``expires``). The first packet (OACK, DATA 1 or ACK 0) is sent here.
        """
        request: RequestPacket = self.context.request
        self._hook_wakeup(stream)
        if request.is_read:
            self.stream = stream
            wants_size = "tsize" in request.options and policy.accepts("tsize")
            if request.mode == "netascii":
                size = encoded_size(stream) if wants_size else None
                reader: Any = NetasciiReader(stream)
            else:
                size = stream_size(stream) if wants_size else None
                reader = stream
            negotiated = negotiate(
                request.options,
                policy,
                is_read=True,
                timeout=timeout,
                size=size,
                mtu=mtu,
                ipv6=self._ipv6,
                stream=stream,
            )
            fit_window(self.sock, negotiated.blksize, negotiated.windowsize)
            oack = encode_oack(negotiated.options) if negotiated.options else None
            log.debug("%r: %r", self.context, negotiated)
            return Sender(self.send, as_readinto(reader), negotiated, retries, now, oack=oack, **engine)

        negotiated = self._negotiated
        fit_window(self.sock, negotiated.blksize, negotiated.windowsize)
        writer: Any = NetasciiWriter(stream) if request.mode == "netascii" else stream
        self.stream = writer
        reply = encode_oack(negotiated.options) if negotiated.options else encode_ack(0)
        log.debug("%r: %r", self.context, negotiated)
        return Receiver(
            self.send, as_write(writer), negotiated, retries, now, reply=reply, complete=self.commit, **engine
        )

    def open(
        self,
        handler: Any,
        policy: ServerOptions,
        timeout: float,
        retries: int,
        now: float,
        mtu: Optional[int] = None,
        **engine: Any,
    ) -> Transfer:
        """Both steps, for a synchronous handler."""
        stream = self.call_handler(handler, policy, timeout, mtu)
        self.stream = stream  # so a failure while starting still closes it
        return self.start(stream, policy, timeout, retries, now, mtu, **engine)

    @property
    def _ipv6(self) -> bool:
        return self.sock.family == socket.AF_INET6

    def _hook_wakeup(self, stream: Any) -> None:
        set_wakeup = getattr(stream, "set_wakeup", None)
        if set_wakeup is not None and self.notify is not None:
            set_wakeup(self.notify)

    def commit(self) -> None:
        """Close the upload stream before the final ACK, so a failure is reported.

        ``close`` may raise :class:`WouldBlock` (an upload still on its way
        upstream); the stream is kept so the retry after ``resume`` closes it.
        """
        self.stream.close()
        self.stream = None

    def close_stream(self, ok: bool) -> None:
        """Close the stream; a failed upload is aborted when the stream allows."""
        stream, self.stream = self.stream, None
        if stream is None:
            return
        try:
            if not ok and hasattr(stream, "abort"):
                stream.abort()
            else:
                stream.close()
        except Exception:
            log.exception("closing the stream for %r failed", self.context)
