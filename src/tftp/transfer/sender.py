"""Sending side: produces DATA, consumes ACKs (a server's RRQ, a client's WRQ)."""

from __future__ import annotations

from typing import Callable, Optional

from ..options import Negotiated
from .base import _ACK, _DATA, _ERROR, SendFn, Transfer, _pack_header

__all__ = ["Sender"]


class Sender(Transfer):
    """Sends a stream as DATA packets: a server's RRQ, a client's WRQ.

    :param read: a fill-the-view callable from :func:`as_readinto`.
    :param oack: when given, sent first, and DATA starts after ACK 0 (a
        server answering an RRQ that carried options). Otherwise DATA starts
        at once.
    """

    __slots__ = ("_read", "_ring", "_views", "_lens", "_base", "_hi", "_last", "_control", "_dup_base")

    def __init__(
        self,
        send: SendFn,
        read: Callable[[memoryview], int],
        negotiated: Negotiated,
        retries: int,
        now: float,
        oack: Optional[bytes] = None,
    ) -> None:
        super().__init__(send, negotiated, retries)
        self._read = read
        size = 4 + self.blksize
        self._ring = [bytearray(size) for _ in range(self.windowsize)]
        self._views = [memoryview(b) for b in self._ring]
        self._lens = [0] * self.windowsize
        self._base = 1  # first block not yet acknowledged
        self._hi = 0  # highest block read into the ring
        self._last: Optional[int] = None  # the final block, once read
        self._dup_base = -1
        self._control = oack
        if oack is not None:
            send(oack)
            self.deadline = now + self.timeout
        else:
            self._send_window(now)

    def _load(self, block: int) -> int:
        slot = block % self.windowsize
        view = self._views[slot]
        _pack_header(view, 0, _DATA, self._wire(block))
        try:
            n = self._read(view[4:])
        except Exception as exc:  # the source failed: tell the peer why
            self.fail(exc)
            return -1
        if n < self.blksize:
            self._last = block
        self._lens[slot] = 4 + n
        self._hi = block
        self.bytes += n
        self.blocks += 1
        return slot

    def _send_window(self, now: float) -> None:
        send = self._send
        block = self._base
        end = block + self.windowsize
        if self._last is not None and self._last < end:
            end = self._last + 1
        hi = self._hi
        while block < end:
            if block > hi:
                slot = self._load(block)
                if slot < 0:
                    return
                if self._last is not None and self._last < end:
                    end = self._last + 1
            else:
                slot = block % self.windowsize
                self.retransmits += 1
            send(self._views[slot][: self._lens[slot]])
            block += 1
        self.deadline = now + self.timeout

    def handle(self, packet: memoryview, n: int, now: float) -> None:
        if self.done or n < 4 or packet[0]:
            return
        op = packet[1]
        if op == _ACK:
            wire = (packet[2] << 8) | packet[3]
            if self._control is not None:
                if wire == 0:
                    self._control = None
                    self._tries = self.retries
                    self._send_window(now)
                return
            ref = self._base - 1
            acked = ref + (wire - self._wire(ref)) % self._period
            if acked > self._hi:
                return  # stale or bogus: outside anything sent
            if acked == ref:
                # Duplicate ACK. Never resend on it with windowsize 1
                # (Sorcerer's Apprentice); with a window, the first one says
                # the receiver lost the block after it.
                if self.windowsize == 1 or self._dup_base == self._base:
                    return
                self._dup_base = self._base
            else:
                self._base = acked + 1
                self._tries = self.retries
                if self._last is not None and acked >= self._last:
                    self.done = True
                    self.deadline = None
                    return
            self._send_window(now)
        elif op == _ERROR:
            self._remote_error(packet, n)
        else:
            self._illegal("unexpected opcode %d while sending" % op)

    def on_timeout(self, now: float) -> None:
        if self.done or self._out_of_tries():
            return
        if self._control is not None:
            self.retransmits += 1
            self._send(self._control)
            self.deadline = now + self.timeout
        else:
            self._send_window(now)
