"""Following TFTP transfers through a capture.

A transfer starts with an RRQ/WRQ to a request port (69 by default); the
server answers from a new port (its TID), and every later datagram between
the client's address/port and that TID belongs to the transfer. The
:class:`FlowTracker` does that bookkeeping and reconstructs each transfer:
options, retransmissions, errors, completion -- and the file itself.
"""

from __future__ import annotations

import ipaddress
import struct
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from ..netascii import decode as netascii_decode
from ..options import DEFAULT_BLKSIZE
from ..options.base import read_decimal
from ..exceptions import TFTPDecodeError
from ..packet import TFTPOpcode, decode
from .events import PacketEvent, new_session_id
from .frames import UDPDatagram

__all__ = ["CapturedTransfer", "FlowTracker"]

Endpoint = Tuple[str, int]
_ANSWER_WINDOW = 10.0  # seconds within which a reply from another address counts


def _plain(endpoint: Endpoint) -> Endpoint:
    """``("::ffff:a.b.c.d", p)`` -> ``("a.b.c.d", p)``: one host, one key."""
    host = endpoint[0]
    if host[:7].lower() == "::ffff:":
        try:
            mapped = ipaddress.IPv6Address(host).ipv4_mapped
        except ValueError:
            return endpoint
        if mapped is not None:
            return (str(mapped), endpoint[1])
    return endpoint


class CapturedTransfer:
    """One transfer reconstructed from a capture.

    :ivar session: id shared with the transfer's :class:`PacketEvent` objects.
    :ivar client, server: the request's source, and its destination.
    :ivar server_tid: the server's transfer address, once it answered.
    :ivar operation: ``"read"`` (RRQ) or ``"write"`` (WRQ).
    :ivar requested, acknowledged: the request's options, the OACK's.
    :ivar blksize, windowsize: what the transfer ran with.
    :ivar error: ``(code, message, sent_by)`` of an ERROR, or ``None``.
    :ivar is_complete: the final DATA was seen and acknowledged.
    :ivar retransmissions: DATA packets seen more than once.
    """

    def __init__(self, session: str, time: float, client: Endpoint, server: Endpoint, request: Any) -> None:
        self.session = session
        self.started = time
        self.ended = time
        self.client = client
        self.server = server
        self.server_tid: Optional[Endpoint] = None
        self.filename: str = request.filename
        self.mode: str = request.mode
        self.operation = "read" if request.is_read else "write"
        self.requested: Dict[str, str] = dict(request.options)
        self.acknowledged: Dict[str, str] = {}
        self.blksize = DEFAULT_BLKSIZE
        self.windowsize = 1
        self.tsize: Optional[int] = None
        self.error: Optional[Tuple[int, str, str]] = None
        self.is_complete = False
        self.final_block: Optional[int] = None  # logical
        self.packets = 0
        self.retransmissions = 0
        self.request_retransmissions = 0
        self._sizes: Dict[int, int] = {}  # payload octets of each block seen, payloads kept or not
        self._blocks: Dict[int, bytes] = {}  # the payloads, when they are kept
        self._logical_hi = 0
        self._rollover = 0

    # -- reconstruction ---------------------------------------------------------

    def _wire(self, block: int) -> int:
        if block < 65536:
            return block
        return ((block - self._rollover) % (65536 - self._rollover)) + self._rollover

    def _logical(self, wire: int) -> int:
        """The logical block number with this wire value nearest the highest seen."""
        period = 65536 - self._rollover
        hi = self._logical_hi
        distance = (wire - self._wire(hi)) % period
        if distance > period // 2:
            distance -= period  # behind: a retransmission
        return max(0, hi + distance)

    def add_data(self, wire: int, payload: bytes, keep: bool = True) -> None:
        """Account for one DATA: always its size, and its payload when ``keep``."""
        block = self._logical(wire)
        if block in self._sizes:
            self.retransmissions += 1
        else:
            self._sizes[block] = len(payload)
            if keep:
                self._blocks[block] = payload
        self._logical_hi = max(self._logical_hi, block)
        if len(payload) < self.blksize:
            self.final_block = block

    def add_ack(self, wire: int) -> None:
        if self.final_block is not None and self._logical(wire) == self.final_block:
            self.is_complete = True

    @property
    def size(self) -> int:
        return sum(self._sizes.values())

    @property
    def missing_blocks(self) -> List[int]:
        """Logical block numbers never seen, up to the highest one seen."""
        if not self._sizes:
            return []
        return [n for n in range(1, max(self._sizes) + 1) if n not in self._sizes]

    def data(self, decode_netascii: bool = True) -> bytes:
        """The transferred bytes, in order; a gap ends what can be returned.

        Netascii transfers are decoded to local form unless
        ``decode_netascii=False``. Empty when the tracker did not keep payloads.
        """
        out = bytearray()
        block = 1
        while block in self._blocks:
            out += self._blocks[block]
            block += 1
        raw = bytes(out)
        return netascii_decode(raw) if decode_netascii and self.mode == "netascii" else raw

    @property
    def duration(self) -> float:
        return self.ended - self.started

    def to_dict(self) -> Dict[str, Any]:
        def endpoint(e):
            return None if e is None else "%s:%s" % e if ":" not in e[0] else "[%s]:%s" % e

        return {
            "session": self.session,
            "operation": self.operation,
            "filename": self.filename,
            "mode": self.mode,
            "client": endpoint(self.client),
            "server": endpoint(self.server),
            "server_tid": endpoint(self.server_tid),
            "requested": self.requested,
            "acknowledged": self.acknowledged,
            "blksize": self.blksize,
            "windowsize": self.windowsize,
            "tsize": self.tsize,
            "bytes": self.size,
            "packets": self.packets,
            "retransmissions": self.retransmissions,
            "missing_blocks": len(self.missing_blocks),
            "complete": self.is_complete,
            "error": (
                None
                if self.error is None
                else {"code": self.error[0], "message": self.error[1], "from": self.error[2]}
            ),
            "started": self.started,
            "duration": round(self.duration, 6),
        }

    def __repr__(self) -> str:
        state = (
            "complete" if self.is_complete else ("error %d" % self.error[0] if self.error else "incomplete")
        )
        return "CapturedTransfer(%s %s %r %s->%s, %d bytes, %s)" % (
            self.session,
            self.operation,
            self.filename,
            self.client,
            self.server,
            self.size,
            state,
        )


class FlowTracker:
    """Assigns datagrams to transfers and reconstructs them.

    :param ports: request ports (where RRQ/WRQ are sent).
    :param keep_payloads: keep DATA payloads (for :meth:`CapturedTransfer.data`).
        Turn off for long captures where only metadata matters: sizes,
        retransmissions and missing blocks are counted either way.
    """

    def __init__(self, ports: Iterable[int] = (69,), keep_payloads: bool = True) -> None:
        self.ports = frozenset(ports)
        self.keep_payloads = keep_payloads
        self.transfers: List[CapturedTransfer] = []
        self._by_client: Dict[Endpoint, CapturedTransfer] = {}

    def feed(self, datagram: UDPDatagram) -> Optional[PacketEvent]:
        """Account for one datagram; its event if it is TFTP, else ``None``."""
        source, destination = _plain(datagram.source), _plain(datagram.destination)
        if source is not datagram.source or destination is not datagram.destination:
            datagram = datagram._replace(source=source, destination=destination)
        payload = datagram.payload
        transfer = None
        if destination[1] in self.ports and len(payload) >= 2 and payload[0] == 0 and payload[1] in (1, 2):
            try:
                request = decode(payload)
            except TFTPDecodeError:
                request = None
            if request is not None:
                current = self._by_client.get(source)
                if (
                    current is not None
                    and current.server_tid is None
                    and current.filename == request.filename
                ):
                    current.request_retransmissions += 1  # the same request again
                    transfer = current
                else:
                    transfer = CapturedTransfer(
                        new_session_id("c"), datagram.time, source, destination, request
                    )
                    self.transfers.append(transfer)
                    self._by_client[source] = transfer
        else:
            transfer = self._match(datagram)
        if transfer is None:
            if destination[1] in self.ports or source[1] in self.ports:
                return PacketEvent(datagram.time, "seen", destination, source, payload, "capture")
            return None
        transfer.packets += 1
        transfer.ended = datagram.time
        self._observe(transfer, datagram)
        return PacketEvent(datagram.time, "seen", destination, source, payload, "capture", transfer.session)

    def _match(self, datagram: UDPDatagram) -> Optional[CapturedTransfer]:
        source, destination = datagram.source, datagram.destination
        to_client = self._by_client.get(destination)
        if to_client is not None:
            tid = to_client.server_tid
            if tid is None:
                if source[0] == to_client.server[0] or datagram.time - to_client.started < _ANSWER_WINDOW:
                    to_client.server_tid = source  # the server's TID (RFC 1350 section 4)
                    return to_client
            elif tid == source:
                return to_client
        from_client = self._by_client.get(source)
        if from_client is not None and from_client.server_tid == destination:
            return from_client
        return None

    def _observe(self, transfer: CapturedTransfer, datagram: UDPDatagram) -> None:
        payload = datagram.payload
        if len(payload) < 4 or payload[0]:
            return
        op = payload[1]
        from_client = datagram.source == transfer.client
        if op == TFTPOpcode.DATA:
            wire = struct.unpack_from("!H", payload, 2)[0]
            if self.keep_payloads:
                transfer.add_data(wire, payload[4:])
            else:
                transfer.add_data(wire, payload[4:], keep=False)
        elif op == TFTPOpcode.ACK:
            transfer.add_ack(struct.unpack_from("!H", payload, 2)[0])
        elif op == TFTPOpcode.OACK and not from_client:
            try:
                options = decode(payload).options  # type: ignore[union-attr]
            except TFTPDecodeError:
                return
            transfer.acknowledged = dict(options)
            for name in ("blksize", "blksize2"):
                value = read_decimal(options.get(name, ""))
                if value is not None:
                    transfer.blksize = value
            window = read_decimal(options.get("windowsize", ""))
            if window is not None:
                transfer.windowsize = window
            tsize = read_decimal(options.get("tsize", ""))
            if tsize is not None:
                transfer.tsize = tsize
            if read_decimal(options.get("rollover", "")) in (0, 1):
                transfer._rollover = read_decimal(options["rollover"])
        elif op == TFTPOpcode.ERROR and transfer.error is None:
            try:
                packet = decode(payload)
                transfer.error = (int(packet.code), packet.message, "client" if from_client else "server")  # type: ignore[union-attr]
            except TFTPDecodeError:
                transfer.error = (0, "", "client" if from_client else "server")

    def feed_all(self, datagrams: Iterable[UDPDatagram]) -> Iterator[PacketEvent]:
        for datagram in datagrams:
            event = self.feed(datagram)
            if event is not None:
                yield event
