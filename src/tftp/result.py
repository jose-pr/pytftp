"""What a finished transfer reports."""

from __future__ import annotations

from typing import Any, Optional, Tuple

from .errors import TftpError
from .options import Negotiated

__all__ = ["TransferResult"]


class TransferResult:
    """The outcome of one transfer, on either side.

    :ivar filename: as requested.
    :ivar operation: ``"read"`` for an RRQ, ``"write"`` for a WRQ, whichever
        side reports it.
    :ivar mode: ``"octet"`` or ``"netascii"``.
    :ivar peer: the other side's ``(host, port, ...)`` transfer address.
    :ivar local: this side's transfer address.
    :ivar bytes: payload bytes moved (netascii-encoded size on the wire).
    :ivar blocks: DATA blocks moved, excluding retransmissions.
    :ivar retransmits: packets sent again after a timeout or a lost block.
    :ivar duration: seconds from request to completion.
    :ivar negotiated: the parameters the transfer ran with.
    :ivar error: the failure, or ``None`` on success.
    """

    __slots__ = (
        "filename",
        "operation",
        "mode",
        "peer",
        "local",
        "bytes",
        "blocks",
        "retransmits",
        "duration",
        "negotiated",
        "error",
    )

    def __init__(
        self,
        filename: str,
        operation: str,
        mode: str,
        peer: Tuple[Any, ...],
        local: Tuple[Any, ...],
        bytes: int,
        blocks: int,
        retransmits: int,
        duration: float,
        negotiated: Negotiated,
        error: Optional[TftpError] = None,
    ) -> None:
        self.filename = filename
        self.operation = operation
        self.mode = mode
        self.peer = peer
        self.local = local
        self.bytes = bytes
        self.blocks = blocks
        self.retransmits = retransmits
        self.duration = duration
        self.negotiated = negotiated
        self.error = error

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def throughput(self) -> float:
        """Payload bytes per second."""
        return self.bytes / self.duration if self.duration > 0 else 0.0

    def __repr__(self) -> str:
        return "TransferResult(%s %r, %d bytes in %.3fs, %d retransmits, %r%s)" % (
            self.operation,
            self.filename,
            self.bytes,
            self.duration,
            self.retransmits,
            self.negotiated,
            "" if self.error is None else ", error=%r" % (self.error,),
        )
