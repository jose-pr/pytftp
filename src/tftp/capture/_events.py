"""One observed datagram: the unit every trace hook, capture and filter works on."""

from __future__ import annotations

import datetime
import itertools
import struct
from typing import Any, Dict, NamedTuple, Optional, Tuple

from ..exceptions import TFTPDecodeError
from .._text import escape
from ..packet._enums import TFTPOpcode
from ..packet._codec import decode

__all__ = ["PacketEvent", "summarize", "new_session_id"]

_session_ids = itertools.count(1)


def new_session_id(prefix: str = "t") -> str:
    """A short id, unique in this process, for correlating a transfer's events."""
    return "%s%d" % (prefix, next(_session_ids))


def _endpoint(address: Any) -> str:
    if not address:
        return "?"
    host, port = address[0], address[1]
    return "[%s]:%s" % (host, port) if ":" in str(host) else "%s:%s" % (host, port)


def _options(options: Dict[str, str], lead: str) -> str:
    """``" name=value ..."`` with every name and value escaped; empty for no options."""
    return "".join("%s%s=%s" % (lead, escape(name), escape(value)) for name, value in options.items())


def summarize(data: bytes) -> str:
    """A one-line description of a TFTP datagram (never raises).

    Text the peer chose (the mode, the options) is escaped, and a file name or an
    ERROR message is shown as ``repr`` shows it, so the line holds no control
    character.
    """
    if len(data) < 2:
        return "short datagram (%d bytes)" % len(data)
    op = (data[0] << 8) | data[1]
    if op == TFTPOpcode.DATA and len(data) >= 4:
        return "DATA %d (%d bytes)" % (struct.unpack_from("!H", data, 2)[0], len(data) - 4)
    if op == TFTPOpcode.ACK and len(data) >= 4:
        return "ACK %d" % struct.unpack_from("!H", data, 2)[0]
    try:
        packet = decode(data)
    except TFTPDecodeError as exc:
        return "malformed (%s)" % escape(str(exc))
    if op in (TFTPOpcode.RRQ, TFTPOpcode.WRQ):
        return "%s %r %s%s" % (
            TFTPOpcode(op).name,
            packet.filename,  # type: ignore[union-attr]
            escape(packet.mode),  # type: ignore[union-attr]
            _options(packet.options, " "),  # type: ignore[union-attr]
        )
    if op == TFTPOpcode.ERROR:
        return "ERROR %d %r" % (packet.code, packet.message)  # type: ignore[union-attr]
    if op == TFTPOpcode.OACK:
        return "OACK" + _options(packet.options, " ")  # type: ignore[union-attr]
    return "opcode %d" % op


class PacketEvent(NamedTuple):
    """A datagram seen by a client, server, relay or capture.

    :ivar time: wall-clock seconds (``time.time()``).
    :ivar direction: ``"in"`` (received) or ``"out"`` (sent), from ``role``'s
        point of view; ``"seen"`` for a passive capture.
    :ivar local: the observer's address for this datagram (for a capture,
        the destination).
    :ivar remote: the other end (for a capture, the source).
    :ivar data: the datagram itself.
    :ivar role: ``"client"``, ``"server"``, ``"relay"`` or ``"capture"``.
    :ivar session: id correlating one transfer's events, or ``None`` for
        datagrams outside any transfer (requests that were refused...).
    :ivar leg: for a relay, ``"client"`` or ``"upstream"``: which side of it.
    """

    time: float
    direction: str
    local: Tuple[Any, ...]
    remote: Tuple[Any, ...]
    data: bytes
    role: str = "capture"
    session: Optional[str] = None
    leg: Optional[str] = None

    @property
    def opcode(self) -> Optional[int]:
        return (self.data[0] << 8) | self.data[1] if len(self.data) >= 2 else None

    @property
    def opcode_name(self) -> str:
        op = self.opcode
        try:
            return TFTPOpcode(op).name if op is not None else "?"
        except ValueError:
            return str(op)

    @property
    def block(self) -> Optional[int]:
        if self.opcode in (TFTPOpcode.DATA, TFTPOpcode.ACK) and len(self.data) >= 4:
            return struct.unpack_from("!H", self.data, 2)[0]
        return None

    @property
    def payload_size(self) -> Optional[int]:
        return len(self.data) - 4 if self.opcode == TFTPOpcode.DATA and len(self.data) >= 4 else None

    def decode(self) -> Any:
        """The parsed packet; raises :class:`TFTPDecodeError`."""
        return decode(self.data)

    @property
    def summary(self) -> str:
        return summarize(self.data)

    @property
    def source(self) -> Tuple[Any, ...]:
        """Who sent it."""
        return self.remote if self.direction != "out" else self.local

    @property
    def destination(self) -> Tuple[Any, ...]:
        """Who it was sent to."""
        return self.local if self.direction != "out" else self.remote

    def __str__(self) -> str:
        """A human line: ``12:00:00.123456 [t3] 10.0.0.5:2000 > 10.0.0.1:69 RRQ 'f' octet``."""
        try:
            stamp = datetime.datetime.fromtimestamp(self.time).strftime("%H:%M:%S.%f")
        except (OverflowError, OSError, ValueError):  # a time the platform cannot convert
            stamp = "%.6f" % self.time
        tags = " ".join(t for t in (self.session and "[%s]" % self.session, self.leg) if t)
        return "%s %s%s > %s %s" % (
            stamp,
            tags + " " if tags else "",
            _endpoint(self.source),
            _endpoint(self.destination),
            self.summary,
        )

    def to_dict(self, payload: bool = False) -> Dict[str, Any]:
        """JSON-ready metadata; DATA payloads only with ``payload=True`` (hex)."""
        record: Dict[str, Any] = {
            "time": self.time,
            "direction": self.direction,
            "role": self.role,
            "session": self.session,
            "leg": self.leg,
            "source": _endpoint(self.source),
            "destination": _endpoint(self.destination),
            "opcode": self.opcode_name,
            "length": len(self.data),
            "summary": self.summary,
        }
        if self.block is not None:
            record["block"] = self.block
        try:
            packet = self.decode()
        except TFTPDecodeError:
            packet = None
        if packet is not None:
            for field in ("filename", "mode", "options", "code", "message"):
                if hasattr(packet, field):
                    value = getattr(packet, field)
                    record[field] = (
                        int(value) if field == "code" else dict(value) if field == "options" else value
                    )
        if payload and self.opcode == TFTPOpcode.DATA:
            record["payload"] = self.data[4:].hex()
        return record
