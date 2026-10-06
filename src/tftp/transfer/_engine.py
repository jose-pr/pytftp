"""Shared transfer state, and adapters from file objects to the engine."""

from __future__ import annotations

import errno
import io
import struct
from typing import Callable, Optional

from ..exceptions import (
    TFTPProtocolError,
    RemoteError,
    TFTPError,
    TransferAbortedError,
    TransferTimeoutError,
    WouldBlock,
)
from ..options._handler import Negotiated
from ..packet._codec import _encode_error, _error_text

__all__ = ["Transfer", "as_readinto", "as_write"]

_DATA = 3
_ACK = 4
_ERROR = 5
_OACK = 6
_ACK_HDR = struct.Struct("!HH")
_pack_header = struct.Struct("!HH").pack_into
_MAX_WINDOW = 32767

SendFn = Callable[[object], object]


def as_readinto(source) -> Callable[[memoryview], int]:
    """A ``readinto``-style callable for ``source``, filling the view fully.

    Short reads are retried until the view is full or the source is
    exhausted, so a short return means end of data. ``source`` needs
    ``readinto`` or ``read``; either returning ``None`` is "nothing ready"
    and raises :class:`WouldBlock`.
    """
    readinto = getattr(source, "readinto", None)
    if readinto is None:
        read = source.read

        def readinto(view):  # type: ignore[misc]
            chunk = read(len(view))
            if chunk is None:
                return None
            n = len(chunk)
            view[:n] = chunk
            return n

    # Bytes already read for a block when the source raised WouldBlock
    # part-way through it; the next call starts from them.
    carry = bytearray()

    def fill(view: memoryview) -> int:
        want = len(view)
        total = len(carry)
        if total:
            view[:total] = carry
            del carry[:]
        try:
            while total < want:
                n = readinto(view[total:])
                if n is None:  # io.RawIOBase: a non-blocking stream with nothing to read
                    raise WouldBlock
                if not n:
                    break
                total += n
        except WouldBlock:
            carry[:] = view[:total]
            raise
        return total

    return fill


def _write_rest(write: Callable[[memoryview], Optional[int]], data, done: int) -> None:
    """Hand ``data`` to ``write`` again from octet ``done`` until all of it is taken."""
    rest = memoryview(data)
    while True:
        rest = rest[done:]
        if not rest:
            return
        done = write(rest)  # type: ignore[assignment]
        if not done:
            raise OSError(errno.EIO, "the sink took none of the %d octets left of a block" % len(rest))


def as_write(sink) -> Callable[[memoryview], object]:
    """A ``write`` callable that is safe to hand a reused receive buffer.

    Standard file objects copy what they are given, so they get the buffer
    directly. Anything else gets ``bytes``, because an object that kept a
    reference to the buffer would see it overwritten by the next packet.

    A buffered file takes the whole block. For any other sink a count below the
    block's length is a short write (``io.RawIOBase.write``): the rest is
    written until it is taken, and a sink that takes nothing raises
    ``OSError``. A raw sink that returns ``None`` has nothing ready:
    :class:`WouldBlock`. Any other sink's ``None`` is "all of it".
    """
    write = sink.write
    copies = isinstance(sink, io.IOBase) or getattr(sink, "copies_writes", False)
    if isinstance(sink, io.BufferedIOBase):
        return write
    raw = isinstance(sink, io.RawIOBase)

    def write_all(view: memoryview) -> None:
        data = view if copies else bytes(view)
        done = write(data)
        if done is None:
            if raw:
                raise WouldBlock
            return
        if done < len(data):
            _write_rest(write, data, done)

    return write_all


class Transfer:
    """State shared by both directions.

    :param retries: retransmissions of one packet before giving up.
    :param backoff: each consecutive retransmission waits this much longer
        (RFC 1123 4.2.3.2 asks for at least exponential backoff); progress
        resets the wait to the negotiated timeout.
    :param max_timeout: ceiling for the backed-off wait; ``None`` is eight
        times the timeout.
    :param expires: absolute time (driver's clock) by which the transfer must
        finish, or ``None``.
    :param max_idle: seconds with no datagram from the peer after which the
        transfer fails, whatever timeout was negotiated; ``None`` is no bound.
        Time spent waiting on the local source or sink does not count.

    :ivar is_done: the transfer is finished, successfully or not.
    :ivar error: the failure, or ``None``.
    :ivar deadline: when :meth:`on_timeout` is due, in the driver's clock;
        ``None`` while nothing is outstanding.
    :ivar is_stalled: waiting for the source or sink (see :class:`WouldBlock`).
    :ivar bytes: payload bytes moved so far.
    :ivar retransmits: packets sent again after loss.
    """

    __slots__ = (
        "_send",
        "negotiated",
        "blksize",
        "windowsize",
        "timeout",
        "rollover",
        "_period",
        "retries",
        "_timer",
        "backoff",
        "max_timeout",
        "expires",
        "max_idle",
        "_heard",
        "is_done",
        "error",
        "deadline",
        "is_stalled",
        "bytes",
        "blocks",
        "retransmits",
    )

    def __init__(
        self,
        send: SendFn,
        negotiated: Negotiated,
        retries: int,
        *,
        backoff: float = 2.0,
        max_timeout: Optional[float] = None,
        expires: Optional[float] = None,
        max_idle: Optional[float] = None,
    ) -> None:
        self._send = send
        self.negotiated = negotiated
        self.blksize = negotiated.blksize
        # A 16-bit block number tells an old ACK from a new one only when the
        # window is at most half the number space.
        self.windowsize = min(negotiated.windowsize, _MAX_WINDOW)
        self.timeout = negotiated.timeout
        self.rollover = negotiated.rollover
        self._period = 65536 - self.rollover
        self.retries = retries
        self.backoff = max(1.0, backoff)
        self.max_timeout = max(self.timeout, max_timeout if max_timeout is not None else self.timeout * 8)
        from netimps import Backoff  # pure arithmetic: the engine still does no I/O

        #: The retransmission wait: doubles on silence, back to ``timeout`` on progress.
        self._timer = Backoff(self.timeout, multiplier=self.backoff, max_delay=self.max_timeout)
        self.expires = expires
        self.max_idle = max_idle
        self._heard: Optional[float] = None  # when the peer last sent us a datagram
        self.is_done = False
        self.error: Optional[TFTPError] = None
        self.deadline: Optional[float] = None
        self.is_stalled = False
        self.bytes = 0
        self.blocks = 0
        self.retransmits = 0

    def _wire(self, block: int) -> int:
        if block < 65536:
            return block
        return ((block - self.rollover) % self._period) + self.rollover

    def _arm(self, now: float) -> None:
        deadline = now + self._timer.delay
        if self.max_idle is not None:
            if self._heard is None:
                self._heard = now
            idle_at = self._heard + self.max_idle
            if idle_at < deadline:
                deadline = idle_at
        if self.expires is not None and deadline > self.expires:
            deadline = self.expires
        self.deadline = deadline

    def _progress(self) -> None:
        """The peer moved the transfer forward: fresh retries, base timeout."""
        self._timer.reset()

    def fail(self, exc: BaseException, notify: bool = True) -> None:
        """End the transfer with ``exc``, sending the peer an ERROR if asked."""
        if self.is_done:
            return
        error = TFTPError.from_exception(exc)
        if not isinstance(exc, TFTPError):
            error.__cause__ = exc
        self.error = error
        self.is_done = True
        self.deadline = None
        self.is_stalled = False
        if notify:
            try:
                self._send(_encode_error(error.code, error.message))
            except OSError:
                pass

    def abort(self, message: str = "transfer aborted") -> None:
        """Cancel locally: the peer gets ERROR 0 and ``error`` is :class:`TransferAbortedError`."""
        self.fail(TransferAbortedError(message))

    def _remote_error(self, packet: memoryview, n: int) -> None:
        code = (packet[2] << 8) | packet[3] if n >= 4 else 0
        self.fail(RemoteError.from_code(code, _error_text(packet[4:n])), notify=False)

    def _illegal(self, what: str) -> None:
        self.fail(TFTPProtocolError(what), notify=True)

    def handle(self, packet: memoryview, n: int, now: float) -> None:  # pragma: no cover
        raise NotImplementedError

    def on_timeout(self, now: float) -> None:  # pragma: no cover
        raise NotImplementedError

    def resume(self, now: float) -> None:  # pragma: no cover
        """Retry what :class:`WouldBlock` interrupted."""
        raise NotImplementedError

    def _out_of_tries(self, now: float) -> bool:
        """Account for one timeout; ``True`` (and failed) when it was the last."""
        if self.expires is not None and now >= self.expires:
            self.fail(TransferTimeoutError("transfer exceeded its time limit"), notify=True)
            return True
        if self.max_idle is not None and self._heard is not None and now >= self._heard + self.max_idle:
            self.fail(
                TransferTimeoutError("no datagram from the peer for %g seconds" % self.max_idle), notify=False
            )
            return True
        timer = self._timer
        if timer.attempt >= self.retries:
            self.fail(TransferTimeoutError("no response after %d retries" % self.retries), notify=False)
            return True
        timer.advance()
        return False
