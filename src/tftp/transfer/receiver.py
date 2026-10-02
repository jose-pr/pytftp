"""Receiving side: consumes DATA, produces ACKs (a server's WRQ, a client's RRQ)."""

from __future__ import annotations

from typing import Callable, Optional

from ..options import Negotiated
from .base import _ACK, _ACK_HDR, _DATA, _ERROR, SendFn, Transfer

__all__ = ["Receiver"]


class Receiver(Transfer):
    """Writes DATA packets to a sink and acknowledges them.

    :param write: from :func:`as_write`.
    :param reply: sent first and resent on timeout until DATA 1 arrives --
        ACK 0 for a server answering a WRQ (or a client acknowledging an
        OACK), the OACK for a server answering a WRQ with options. ``None``
        when the first DATA is about to be handed in (a client whose RRQ was
        answered with DATA 1 directly).
    :param complete: called after the last DATA is written and before it is
        acknowledged; if it raises, the peer gets an ERROR instead of the
        final ACK (a server committing an upload to disk).

    After the final ACK, ``done`` is set and the receiver keeps answering a
    repeated final DATA with that ACK, so a driver can "dally" (RFC 1350
    section 6) by feeding it packets for a while longer.
    """

    __slots__ = (
        "_write",
        "_complete",
        "_expected",
        "_expected_wire",
        "_in_window",
        "_nacked",
        "_reply",
        "_final",
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
    ) -> None:
        super().__init__(send, negotiated, retries)
        self._write = write
        self._complete = complete
        self._expected = 1
        self._expected_wire = 1
        self._in_window = 0
        self._nacked = False
        self._reply = reply
        self._final: Optional[bytes] = None
        if reply is not None:
            send(reply)
            self.deadline = now + self.timeout

    def _ack_last(self) -> None:
        block = self._expected - 1
        if block == 0 and self._reply is not None:
            self._send(self._reply)
        else:
            self._send(_ACK_HDR.pack(_ACK, self._wire(block)))

    def handle(self, packet: memoryview, n: int, now: float) -> None:
        if n < 4 or packet[0]:
            return
        op = packet[1]
        if op == _DATA:
            wire = (packet[2] << 8) | packet[3]
            if self.done:
                if self._final is not None and wire == self._wire(self._expected - 1):
                    self._send(self._final)
                return
            if wire == self._expected_wire:
                size = n - 4
                if size > self.blksize:
                    self._illegal("DATA of %d bytes exceeds blksize %d" % (size, self.blksize))
                    return
                if size:
                    try:
                        self._write(packet[4:n])
                    except Exception as exc:
                        self.fail(exc)
                        return
                block = self._expected
                self.bytes += size
                self.blocks += 1
                self._expected = block + 1
                self._expected_wire = self._wire(block + 1)
                self._tries = self.retries
                self._nacked = False
                if size < self.blksize:
                    if self._complete is not None:
                        try:
                            self._complete()
                        except Exception as exc:
                            self.fail(exc)
                            return
                    self._final = _ACK_HDR.pack(_ACK, wire)
                    self._send(self._final)
                    self.done = True
                    self.deadline = None
                    return
                self._in_window += 1
                if self._in_window >= self.windowsize:
                    self._in_window = 0
                    self._send(_ACK_HDR.pack(_ACK, wire))
                self.deadline = now + self.timeout
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
            # was lost). Either way re-acknowledge the last in-order block,
            # once per gap when windowing so a burst does not become an ACK
            # storm.
            if self.windowsize == 1 or not self._nacked:
                self._nacked = True
                self._in_window = 0
                self.retransmits += 1
                self._ack_last()
        elif op == _ERROR:
            if not self.done:
                self._remote_error(packet, n)
        elif not self.done:
            self._illegal("unexpected opcode %d while receiving" % op)

    def on_timeout(self, now: float) -> None:
        if self.done or self._out_of_tries():
            return
        self._in_window = 0
        self._nacked = False
        self.retransmits += 1
        self._ack_last()
        self.deadline = now + self.timeout
