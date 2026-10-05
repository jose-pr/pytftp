"""One relayed transfer: two sockets, the learned upstream TID, and when it ends.

A transparent relay forwards datagrams unchanged, so it cannot know every
extension's effect on the protocol. It ends a transfer on whichever comes
first: an ERROR in either direction, a recognised final DATA/ACK exchange
(followed by a short linger so a retransmitted final DATA still gets
through), no traffic for ``idle_timeout``, or ``max_lifetime``.
"""

from __future__ import annotations

import socket
import struct
from typing import Any, NamedTuple, Optional, Tuple

from ..options import DEFAULT_BLKSIZE
from ..options.base import read_decimal
from ..exceptions import TFTPDecodeError
from ..packet import TFTPOpcode, RequestPacket, decode

__all__ = ["RelaySession", "RelaySummary"]


class RelaySummary(NamedTuple):
    """What :class:`TFTPRelay` reports when a relayed transfer ends.

    ``reason`` is ``"complete"``, ``"error"`` (an ERROR passed through, see
    ``error``), ``"idle"``, ``"lifetime"`` or ``"shutdown"``.
    """

    session: str
    client: Tuple[Any, ...]
    upstream: Tuple[Any, ...]
    filename: str
    operation: str
    mode: str
    bytes_to_client: int
    bytes_from_client: int
    packets: int
    duration: float
    reason: str
    error: Optional[Tuple[int, str]] = None


class RelaySession:
    __slots__ = (
        "id",
        "client",
        "key",
        "down",
        "up",
        "upstream",
        "upstream_tid",
        "request",
        "context",
        "started",
        "last_activity",
        "closing_at",
        "blksize",
        "final_block",
        "bytes_to_client",
        "bytes_from_client",
        "packets",
        "reason",
        "error",
        "closed",
    )

    def __init__(
        self,
        session_id: str,
        client: Tuple[Any, ...],
        key: Tuple[str, int],
        down: socket.socket,
        up: socket.socket,
        upstream: Tuple[Any, ...],
        request: RequestPacket,
        context: Any,
        now: float,
    ) -> None:
        self.id = session_id
        self.client = client
        self.key = key
        self.down = down
        self.up = up
        self.upstream = upstream  # (address, request port)
        self.upstream_tid: Optional[Tuple[Any, ...]] = None
        self.request = request
        self.context = context
        self.started = now
        self.last_activity = now
        self.closing_at: Optional[float] = None
        self.blksize = DEFAULT_BLKSIZE
        self.final_block: Optional[int] = None
        self.bytes_to_client = 0
        self.bytes_from_client = 0
        self.packets = 0
        self.reason: Optional[str] = None
        self.error: Optional[Tuple[int, str]] = None
        self.closed = False

    def deadline(self, idle_timeout: float, max_lifetime: float) -> float:
        due = min(self.last_activity + idle_timeout, self.started + max_lifetime)
        return due if self.closing_at is None else min(due, self.closing_at)

    def expiry_reason(self, now: float, idle_timeout: float, max_lifetime: float) -> str:
        if self.closing_at is not None and now >= self.closing_at:
            return self.reason or "complete"
        if now >= self.started + max_lifetime:
            return "lifetime"
        return "idle"

    def observe(self, data: bytes, from_client: bool, now: float, linger: float) -> None:
        """Follow the protocol just enough to see the transfer end."""
        self.last_activity = now
        self.packets += 1
        if len(data) < 4 or data[0]:
            return
        op = data[1]
        if op == TFTPOpcode.DATA:
            size = len(data) - 4
            if from_client:
                self.bytes_from_client += size
            else:
                self.bytes_to_client += size
            if size < self.blksize:
                self.final_block = struct.unpack_from("!H", data, 2)[0]
        elif op == TFTPOpcode.ACK:
            if self.final_block is not None and struct.unpack_from("!H", data, 2)[0] == self.final_block:
                if self.closing_at is None:
                    self.reason = "complete"
                self.closing_at = now + linger
        elif op == TFTPOpcode.OACK and not from_client:
            try:
                options = decode(data).options  # type: ignore[union-attr]
            except TFTPDecodeError:
                return
            for name in ("blksize", "blksize2"):
                value = read_decimal(options.get(name, ""))
                if value is not None:
                    self.blksize = value
        elif op == TFTPOpcode.ERROR:
            try:
                packet = decode(data)
                self.error = (int(packet.code), packet.message)  # type: ignore[union-attr]
            except TFTPDecodeError:
                self.error = (0, "")
            self.reason = "error"
            # Brief: the ERROR ends the transfer, but let a duplicate through.
            self.closing_at = now + min(linger, 1.0)

    def summary(self, now: float) -> RelaySummary:
        request = self.request
        return RelaySummary(
            self.id,
            self.client,
            self.upstream_tid or self.upstream,
            request.filename,
            "read" if request.is_read else "write",
            request.mode,
            self.bytes_to_client,
            self.bytes_from_client,
            self.packets,
            now - self.started,
            self.reason or "idle",
            self.error,
        )
