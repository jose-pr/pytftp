"""One server-side transfer: its socket, its stream, and how it starts.

Every transfer gets its own UDP socket -- its transfer ID (RFC 1350
section 4) -- bound to the address the request was sent to when that is
known, so a multi-homed or virtual-IP host answers from the address the
client used (``Listener.reply_socket``, on netimps' ``UDPEndpoint``).
"""

from __future__ import annotations

import errno
import logging
import os
import socket
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator, List, Optional, Tuple, Union

from .._sockets import fit_window
from ..exceptions import TFTPError, TFTPValueError
from ..netascii import NetasciiReader, NetasciiWriter
from ..options import Negotiated, TFTPServerOptions, negotiate
from ..packet import TFTPErrorCode, RequestPacket, encode_ack, encode_oack
from ..transfer import Receiver, Sender, Transfer, as_readinto, as_write
from ..capture.events import PacketEvent, new_session_id
from .handler import TFTPRequestContext

__all__ = [
    "Session",
    "PortRange",
    "PortRangeLike",
    "PortAllocator",
    "as_port_range",
    "bind_transfer",
    "stream_size",
]

log = logging.getLogger("tftp.server")

#: Send failures that are loss: the buffer or the stack is busy, not the transfer broken.
#: 10055 is WSAENOBUFS, which Python reports as the errno on Windows.
_LOSS = frozenset({errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR, errno.ENOBUFS, 10055})


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


@dataclass(frozen=True, repr=False)
class PortRange:
    """The UDP ports transfer sockets may use, ``low`` to ``high`` inclusive.

    Each transfer needs a port of its own; pinning them to a range lets a
    firewall allow them (tftp-hpa ``-R``, dnsmasq ``--tftp-port-range``).
    An immutable value: ``len()`` counts the ports, iteration and ``in``
    cover them in order, ``str()`` is ``LOW:HIGH`` and ``PortRange.parse``
    reads it back. Which port a server takes next is the server's own
    :class:`PortAllocator`.

    :raises TypeError: a bound that is not an ``int``.
    :raises TFTPValueError: not ``1 <= low <= high <= 65535``.
    """

    low: int
    high: int

    def __post_init__(self) -> None:
        for bound in (self.low, self.high):
            if isinstance(bound, bool) or not isinstance(bound, int):
                raise TypeError("a port is an int, not %s" % type(bound).__name__)
        if not 1 <= self.low <= self.high <= 65535:
            raise TFTPValueError(
                "a port range satisfies 1 <= low <= high <= 65535, not %d:%d" % (self.low, self.high)
            )

    @classmethod
    def parse(cls, text: str) -> "PortRange":
        """``LOW:HIGH`` (``LOW-HIGH`` too), ASCII digits, as ``str(range)`` writes it.

        :raises TypeError: ``text`` is not a ``str``.
        :raises TFTPValueError: anything else.
        """
        if not isinstance(text, str):
            raise TypeError("a port range is parsed from text, not %s" % type(text).__name__)
        low, sep, high = text.replace("-", ":", 1).partition(":")
        if not sep or not all(part.isascii() and part.isdigit() for part in (low, high)):
            raise TFTPValueError("a port range is LOW:HIGH within 1..65535, not %r" % text)
        return cls(int(low), int(high))

    @classmethod
    def try_parse(cls, text: str, default: "Optional[PortRange]" = None) -> "Optional[PortRange]":
        """:meth:`parse`, or ``default`` for text that is not a port range.

        Still raises :class:`TypeError` when ``text`` is not a ``str``.
        """
        try:
            return cls.parse(text)
        except TFTPValueError:
            return default

    def __len__(self) -> int:
        return self.high - self.low + 1

    def __iter__(self) -> Iterator[int]:
        return iter(range(self.low, self.high + 1))

    def __contains__(self, port: object) -> bool:
        return isinstance(port, int) and not isinstance(port, bool) and self.low <= port <= self.high

    def __str__(self) -> str:
        return "%d:%d" % (self.low, self.high)

    def __repr__(self) -> str:
        return "PortRange(%d, %d)" % (self.low, self.high)


#: What a ``port_range`` argument takes in place of a :class:`PortRange`: a
#: ``(low, high)`` pair, a ``range`` of step 1 or the text ``"LOW:HIGH"``.
PortRangeLike = Union[PortRange, Tuple[int, int], "range", str]


def as_port_range(value: "Optional[PortRangeLike]") -> Optional[PortRange]:
    """``None``, or the :class:`PortRange` that ``value`` stands for.

    :raises TypeError: ``value`` is none of the forms of :data:`PortRangeLike`.
    :raises TFTPValueError: text or numbers that are not a port range.
    """
    if value is None or isinstance(value, PortRange):
        return value
    if isinstance(value, str):
        return PortRange.parse(value)
    if isinstance(value, range):
        if value.step != 1 or not len(value):
            raise TFTPValueError("a port range is a non-empty range with step 1")
        return PortRange(value.start, value.stop - 1)
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return PortRange(value[0], value[1])
    raise TypeError(
        "a port range is a PortRange, a (low, high) pair, a range or LOW:HIGH text, not %r" % (value,)
    )


class PortAllocator:
    """Which port of a :class:`PortRange` a transfer socket tries next.

    Ports are tried round-robin from where the last search stopped, so a port
    just released is the last to be reused. Every server and relay owns one:
    two given the same range share the ports, not the position.
    """

    __slots__ = ("ports", "_next")

    def __init__(self, ports: PortRange) -> None:
        self.ports = ports
        self._next = ports.low

    def ordered(self) -> List[int]:
        """Every port once, starting after the last one handed out."""
        start, high, low = self._next, self.ports.high, self.ports.low
        return list(range(start, high + 1)) + list(range(low, start))

    def taken(self, port: int) -> None:
        """``port`` was just handed out: the next search starts after it."""
        self._next = port + 1 if port < self.ports.high else self.ports.low


def bind_transfer(host: Any, family: int, ports: Optional[PortAllocator] = None) -> socket.socket:
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
            raise AddressInUseError("no free port in %s" % ports.ports)
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
        """Send one datagram to the peer.

        A full send buffer or a busy network stack drops it, which the
        transfer's timeout recovers from. Any other ``OSError`` (``EMSGSIZE``
        for a datagram the host cannot send, an unreachable peer) is raised:
        the caller ends this transfer with it.
        """
        try:
            self.sock.sendto(packet, self.peer)
        except OSError as exc:
            if exc.errno in _LOSS or isinstance(exc, (BlockingIOError, InterruptedError)):
                log.debug("%r: a datagram was dropped by the host: %s", self.context, exc)
                return
            raise
        if self.trace is not None:
            self.emit(packet, "out", self.peer)

    def emit(
        self, data, direction: str, remote: Tuple[Any, ...], local: Optional[Tuple[Any, ...]] = None
    ) -> None:
        """Report one datagram to the trace hook (exceptions are logged, not raised)."""
        self.trace(  # type: ignore[misc]  # a HookGuard: it logs a failure once and returns
            PacketEvent(time.time(), direction, local or self.local, remote, bytes(data), "server", self.id)
        )

    def call_handler(
        self, handler: Any, policy: TFTPServerOptions, timeout: float, mtu: Optional[int] = None
    ) -> Any:
        """Step 1: ask ``handler`` for the stream (a coroutine, for a handler with coroutine hooks).

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
        policy: TFTPServerOptions,
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
                # The encoded size needs the whole file read, on the loop that serves every
                # other transfer: tsize is left out, as tftp-hpa does.
                size = None
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
        policy: TFTPServerOptions,
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
