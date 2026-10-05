"""Receiving side: consumes DATA, produces ACKs (a server's WRQ, a client's RRQ)."""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..exceptions import WouldBlock
from ..options import Negotiated
from .base import _ACK, _ACK_HDR, _DATA, _ERROR, _OACK, SendFn, Transfer

__all__ = ["Receiver"]


class Receiver(Transfer):
    """Writes DATA packets to a sink and acknowledges them.

    :param write: from :func:`as_write`. It may raise :class:`WouldBlock`:
        the block is then held unacknowledged until :meth:`resume` writes it,
        which is backpressure the sender sees as a slower ACK.
    :param reply: sent first and resent on timeout until DATA 1 arrives --
        ACK 0 for a server answering a WRQ (or a client acknowledging an
        OACK), the OACK for a server answering a WRQ with options. ``None``
        when the first DATA is about to be handed in (a client whose RRQ was
        answered with DATA 1 directly).
    :param complete: called after the last DATA is written and before it is
        acknowledged; if it raises, the peer gets an ERROR instead of the
        final ACK (a server committing an upload to disk). It may raise
        :class:`WouldBlock` too, holding the final ACK.

    After the final ACK, ``done`` is set and the receiver keeps answering a
    repeated final DATA with that ACK, so a driver can "dally" (RFC 1350
    section 6) by feeding it packets for a while longer.

    Keyword arguments are :class:`Transfer`'s (``backoff``, ``max_timeout``,
    ``expires``).
    """

    __slots__ = (
        "_write",
        "_complete",
        "_expected",
        "_expected_wire",
        "_in_window",
        "_nacks",
        "_reply",
        "_final",
        "_pending",
        "_dropped",
    )

    def __init__(
        self,
        send: SendFn,
        write: Callable[[memoryview], object],
        negotiated: Negotiated,
        retries: int,
        now: float,
        reply: Optional[bytes] = None,
        complete: Optional[Callable[[], object]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(send, negotiated, retries, **kwargs)
        self._write = write
        self._complete = complete
        self._expected = 1
        self._expected_wire = 1
        self._in_window = 0
        self._nacks = 0  # ACKs sent for out-of-sequence DATA since the last in-order block
        self._reply = reply
        self._final: Optional[bytes] = None
        #: (payload, wire, written) of a block held by a stalled sink.
        self._pending: Optional[tuple] = None
        #: DATA arrived (and was dropped) while the sink was stalled.
        self._dropped = False
        if reply is not None:
            send(reply)
            self._arm(now)

    def _ack_last(self) -> None:
        block = self._expected - 1
        if block == 0 and self._reply is not None:
            self._send(self._reply)
        else:
            self._send(_ACK_HDR.pack(_ACK, self._wire(block)))

    def _accept(self, payload, wire: int, size: int, now: float, written: bool = False) -> None:
        """Write one in-order block and acknowledge as the window requires."""
        if size and not written:
            try:
                self._write(payload)
            except WouldBlock:
                self._hold(payload, wire, size, written=False)
                return
            except Exception as exc:
                self.fail(exc)
                return
        if size < self.blksize and self._complete is not None:
            try:
                self._complete()
            except WouldBlock:
                self._hold(payload, wire, size, written=True)
                return
            except Exception as exc:
                self.fail(exc)
                return
        block = self._expected
        self.bytes += size
        self.blocks += 1
        self._expected = block + 1
        self._expected_wire = self._wire(block + 1)
        self._progress()
        self._nacks = 0
        if size < self.blksize:
            self._final = _ACK_HDR.pack(_ACK, wire)
            self._send(self._final)
            self.done = True
            self.deadline = None
            return
        self._in_window += 1
        if self._in_window >= self.windowsize:
            self._in_window = 0
            self._send(_ACK_HDR.pack(_ACK, wire))
        self._arm(now)

    def _hold(self, payload, wire: int, size: int, written: bool) -> None:
        # Copy: the payload is usually a view of a reused receive buffer.
        self._pending = (bytes(payload), wire, size, written)
        self.stalled = True
        # Our sink is the holdup, not the peer: only the time limit applies.
        self.deadline = self.expires

    def resume(self, now: float) -> None:
        if not self.stalled or self.done or self._pending is None:
            return
        payload, wire, size, written = self._pending
        self._pending = None
        self.stalled = False
        self._heard = now  # the wait was on our sink, not the peer
        self._accept(memoryview(payload), wire, size, now, written)
        if self._dropped and not self.done and not self.stalled:
            # The rest of the window was dropped while we stalled: say where
            # we are now rather than wait for a window that cannot complete
            # (RFC 7440: ACK the last block received in order). Nothing to
            # do if accepting the block just completed, and ACKed, a window.
            self._dropped = False
            if self._in_window:
                self._in_window = 0
                self._nacks = 1
                self._ack_last()

    def handle(self, packet: memoryview, n: int, now: float) -> None:
        self._heard = now
        if n < 4 or packet[0]:
            return
        op = packet[1]
        if op == _DATA:
            wire = (packet[2] << 8) | packet[3]
            if self.done:
                if self._final is not None and wire == self._wire(self._expected - 1):
                    self._send(self._final)
                return
            if self.stalled:
                self._dropped = True
                return  # unacknowledged on purpose; the sender will resend
            if wire == self._expected_wire:
                size = n - 4
                if size > self.blksize:
                    self._illegal("DATA of %d bytes exceeds blksize %d" % (size, self.blksize))
                    return
                self._accept(packet[4:n], wire, size, now)
                return
            if (
                self._expected_wire == 0
                and wire == 1
                and self.windowsize == 1
                and self._expected == 65536
                and "rollover" not in self.negotiated.options
            ):
                # Rollover was not negotiated and this sender wraps to 1, as
                # some do: follow it rather than stall.
                self.rollover = 1
                self._period = 65535
                self._expected_wire = 1
                self.handle(packet, n, now)
                return
            # Out of order: a duplicate (our ACK was lost) or a gap (DATA
            # was lost). Either way re-acknowledge the last in-order block.
            # With a window that is once for a run of duplicates, and twice
            # for a gap: the first report may advance the sender's window
            # (which does not resend), and the second repeats it, which asks
            # for the blocks from the hole. No more than that, so a burst
            # does not become an ACK storm.
            if self.windowsize == 1:
                limit = 1 << 30
            elif (wire - self._expected_wire) % self._period < self.windowsize:
                limit = 2
            else:
                limit = 1
            if self._nacks < limit:
                self._nacks += 1
                self._in_window = 0
                self.retransmits += 1
                self._ack_last()
        elif op == _OACK:
            # The server repeated its OACK: our ACK 0 was lost (RFC 2347).
            if not self.done and self._expected == 1 and self._reply is not None:
                self.retransmits += 1
                self._send(self._reply)
        elif op == _ERROR:
            if not self.done:
                self._remote_error(packet, n)
        elif not self.done:
            self._illegal("unexpected opcode %d while receiving" % op)

    def on_timeout(self, now: float) -> None:
        if self.done or self._out_of_tries(now):
            return
        if self.stalled:
            return  # only reached through the time limit
        self._in_window = 0
        self._nacks = 0
        self.retransmits += 1
        self._ack_last()
        self._arm(now)
