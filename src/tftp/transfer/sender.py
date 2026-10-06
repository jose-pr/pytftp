"""Sending side: produces DATA, consumes ACKs (a server's RRQ, a client's WRQ)."""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..exceptions import WouldBlock
from ..options import Negotiated
from .base import _ACK, _DATA, _ERROR, _OACK, SendFn, Transfer, _pack_header

__all__ = ["Sender"]


class Sender(Transfer):
    """Sends a stream as DATA packets: a server's RRQ, a client's WRQ.

    :param read: a fill-the-view callable from :func:`as_readinto`. It may
        raise :class:`WouldBlock`; sending then pauses until :meth:`resume`.
    :param oack: when given, sent first, and DATA starts after ACK 0 (a
        server answering an RRQ that carried options). Otherwise DATA starts
        at once.

    Keyword arguments are :class:`Transfer`'s (``backoff``, ``max_timeout``,
    ``expires``).
    """

    __slots__ = (
        "_read",
        "_ring",
        "_views",
        "_lens",
        "_base",
        "_next",
        "_hi",
        "_last",
        "_control",
        "_resent_at",
    )

    def __init__(
        self,
        send: SendFn,
        read: Callable[[memoryview], int],
        negotiated: Negotiated,
        retries: int,
        now: float,
        oack: Optional[bytes] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(send, negotiated, retries, **kwargs)
        self._read = read
        # Slots are allocated when their block is first read, so a transfer
        # costs what it has sent, not the window it negotiated.
        self._ring: list = [None] * self.windowsize
        self._views: list = [None] * self.windowsize
        self._lens = [0] * self.windowsize
        self._base = 1  # first block not yet acknowledged
        self._next = 1  # next block to transmit in the current window pass
        self._hi = 0  # highest block read into the ring
        self._last: Optional[int] = None  # the final block, once read
        self._resent_at = -(1 << 30)  # base when blocks in flight were last resent
        self._control = oack
        if oack is not None:
            send(oack)
            self._arm(now)
        else:
            self._pump(now)

    def _load(self, block: int) -> int:
        slot = block % self.windowsize
        view = self._views[slot]
        if view is None:
            buffer = self._ring[slot] = bytearray(4 + self.blksize)
            view = self._views[slot] = memoryview(buffer)
        _pack_header(view, 0, _DATA, self._wire(block))
        try:
            n = self._read(view[4:])
        except WouldBlock:
            self.is_stalled = True
            return -1
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

    def _pump(self, now: float) -> None:
        """Send from ``_next`` to the end of the window starting at ``_base``."""
        send = self._send
        block = self._next
        end = self._base + self.windowsize
        if self._last is not None and self._last < end:
            end = self._last + 1
        self.is_stalled = False
        while block < end:
            if block > self._hi:
                slot = self._load(block)
                if slot < 0:
                    break  # stalled (resume continues here) or failed
                if self._last is not None and self._last < end:
                    end = self._last + 1
            else:
                slot = block % self.windowsize
                self.retransmits += 1
            send(self._views[slot][: self._lens[slot]])
            block += 1
        self._next = block
        if self.is_done:
            return
        if self.is_stalled and self._hi < self._base:
            # Waiting on our own source with nothing outstanding: no peer
            # timeout applies, only the transfer's own time limit.
            self.deadline = self.expires
        else:
            self._arm(now)

    def _restart(self, now: float) -> None:
        """(Re)send the window from the first unacknowledged block (RFC 7440)."""
        self._next = self._base
        self._resent_at = self._base
        self._pump(now)

    def resume(self, now: float) -> None:
        if self.is_stalled and not self.is_done:
            self._heard = now  # the wait was on our source, not the peer
            self._pump(now)

    def handle(self, packet: memoryview, n: int, now: float) -> None:
        self._heard = now
        if self.is_done or n < 4 or packet[0]:
            return  # every packet this side acts on holds an opcode and a number
        op = packet[1]
        if op == _ACK:
            wire = (packet[2] << 8) | packet[3]
            if self._control is not None:
                if wire == 0:
                    self._control = None
                    self._progress()
                    self._pump(now)
                return
            ref = self._base - 1
            acked = ref + (wire - self._wire(ref)) % self._period
            if acked > self._hi:
                return  # stale or bogus: outside anything sent
            if acked == ref:
                # A duplicate ACK is never a reason to resend with windowsize
                # 1 (RFC 1123 4.2.3.1). With a window it says the receiver
                # saw a block out of sequence, so the blocks after ``acked``
                # are resent -- but a duplicate the network or the receiver
                # made, answered by a resend, makes the receiver acknowledge
                # that copy too, which is a duplicate again, and the window
                # is then sent twice for the rest of the transfer. Blocks
                # are therefore resent for an ACK at most once per two
                # windows of progress; the timeout covers the rest.
                if self.windowsize == 1 or self._base < self._resent_at + 2 * self.windowsize:
                    return
                self._restart(now)
            else:
                # The window moved: send the blocks that now fit. Blocks
                # already in flight are not sent again (an ACK is 4 octets;
                # one that costs a window of DATA is an amplifier).
                self._base = acked + 1
                self._progress()
                if self._last is not None and acked >= self._last:
                    self.is_done = True
                    self.deadline = None
                    return
                if self._next < self._base:
                    self._next = self._base
                self._pump(now)
        elif op == _OACK:
            # A WRQ client sees the server's OACK again when DATA 1 was lost.
            # Resending on it would be Sorcerer's Apprentice; our own timeout
            # resends DATA 1.
            return
        elif op == _ERROR:
            self._remote_error(packet, n)
        else:
            self._illegal("unexpected opcode %d while sending" % op)

    def on_timeout(self, now: float) -> None:
        if self.is_done or self._out_of_tries(now):
            return
        if self._control is not None:
            self.retransmits += 1
            self._send(self._control)
            self._arm(now)
        else:
            self._restart(now)
