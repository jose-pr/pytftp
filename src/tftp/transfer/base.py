"""Shared transfer state, and adapters from file objects to the engine."""

from __future__ import annotations

import io
import struct
from typing import Callable, Optional

from ..errors import ProtocolError, RemoteError, TftpError, TransferTimeout, error_for_exception
from ..netascii import NetasciiWriter
from ..options import Negotiated
from ..packet import encode_error

__all__ = ["Transfer", "as_readinto", "as_write"]

_DATA = 3
_ACK = 4
_ERROR = 5
_ACK_HDR = struct.Struct("!HH")
_pack_header = struct.Struct("!HH").pack_into

SendFn = Callable[[object], object]


def as_readinto(source) -> Callable[[memoryview], int]:
    """A ``readinto``-style callable for ``source``, filling the view fully.

    Short reads are retried until the view is full or the source is
    exhausted, so a short return means end of data. ``source`` needs
    ``readinto`` or ``read``.
    """
    readinto = getattr(source, "readinto", None)
    if readinto is None:
        read = source.read

        def readinto(view):  # type: ignore[misc]
            chunk = read(len(view))
            n = len(chunk)
            view[:n] = chunk
            return n

    def fill(view: memoryview) -> int:
        want = len(view)
        n = readinto(view) or 0
        total = n
        while n and total < want:
            n = readinto(view[total:]) or 0
            total += n
        return total

    return fill


_COPYING_WRITERS = (io.IOBase, NetasciiWriter)


def as_write(sink) -> Callable[[memoryview], object]:
    """A ``write`` callable that is safe to hand a reused receive buffer.

    Standard file objects copy what they are given, so they get the buffer
    directly. Anything else gets ``bytes``, because an object that kept a
    reference to the buffer would see it overwritten by the next packet.
    """
    write = sink.write
    if isinstance(sink, _COPYING_WRITERS) or getattr(sink, "_tftp_copies_", False):
        return write
    return lambda view: write(bytes(view))


class Transfer:
    """State shared by both directions.

    :ivar done: the transfer is finished, successfully or not.
    :ivar error: the failure, or ``None``.
    :ivar deadline: when :meth:`on_timeout` is due, in the driver's clock;
        ``None`` while nothing is outstanding.
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
        "_tries",
        "done",
        "error",
        "deadline",
        "bytes",
        "blocks",
        "retransmits",
    )

    def __init__(self, send: SendFn, negotiated: Negotiated, retries: int) -> None:
        self._send = send
        self.negotiated = negotiated
        self.blksize = negotiated.blksize
        self.windowsize = negotiated.windowsize
        self.timeout = negotiated.timeout
        self.rollover = negotiated.rollover
        self._period = 65536 - self.rollover
        self.retries = retries
        self._tries = retries
        self.done = False
        self.error: Optional[TftpError] = None
        self.deadline: Optional[float] = None
        self.bytes = 0
        self.blocks = 0
        self.retransmits = 0

    def _wire(self, block: int) -> int:
        if block < 65536:
            return block
        return ((block - self.rollover) % self._period) + self.rollover

    def fail(self, exc: BaseException, notify: bool = True) -> None:
        """End the transfer with ``exc``, sending the peer an ERROR if asked."""
        if self.done:
            return
        error = error_for_exception(exc)
        if not isinstance(exc, TftpError):
            error.__cause__ = exc
        if notify:
            try:
                self._send(encode_error(error.code, error.message))
            except OSError:
                pass
        self.error = error
        self.done = True
        self.deadline = None

    def _remote_error(self, packet: memoryview, n: int) -> None:
        code = (packet[2] << 8) | packet[3] if n >= 4 else 0
        raw = bytes(packet[4:n]).split(b"\0", 1)[0]
        self.fail(RemoteError(code, raw.decode("utf-8", "replace")), notify=False)

    def _illegal(self, what: str) -> None:
        self.fail(ProtocolError(what), notify=True)

    def handle(self, packet: memoryview, n: int, now: float) -> None:  # pragma: no cover
        raise NotImplementedError

    def on_timeout(self, now: float) -> None:  # pragma: no cover
        raise NotImplementedError

    def _out_of_tries(self) -> bool:
        self._tries -= 1
        if self._tries < 0:
            self.fail(TransferTimeout("no response after %d retries" % self.retries), notify=False)
            return True
        return False
