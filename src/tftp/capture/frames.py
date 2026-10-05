"""From captured link-layer frames to UDP datagrams.

Handles the link types tcpdump, Wireshark and dumpcap commonly write, IPv4
and IPv6 (extension headers included), and IP fragment reassembly -- needed
because a DATA packet with a large ``blksize`` is fragmented on the wire.
"""

from __future__ import annotations

import ipaddress
import socket
import struct
from typing import Dict, List, NamedTuple, Optional, Tuple

__all__ = ["UDPDatagram", "FrameDecoder", "LINKTYPES"]

#: Link types understood, by pcap LINKTYPE_* number.
LINKTYPES = {
    0: "NULL",  # BSD loopback, host-order family
    1: "ETHERNET",
    12: "RAW",  # some BSDs
    14: "RAW",
    101: "RAW",
    108: "LOOP",  # OpenBSD loopback, network-order family
    113: "LINUX_SLL",
    228: "IPV4",
    229: "IPV6",
    276: "LINUX_SLL2",
}

_V6_FAMILIES = {10, 24, 28, 30}  # AF_INET6 on Linux, BSD, macOS/FreeBSD
_FRAGMENT_LIMIT = 256  # reassemblies kept in flight


class UDPDatagram(NamedTuple):
    """One UDP datagram from a capture."""

    time: float
    source: Tuple[str, int]
    destination: Tuple[str, int]
    payload: bytes


class _Reassembly:
    __slots__ = ("pieces", "total")

    def __init__(self) -> None:
        self.pieces: Dict[int, bytes] = {}
        self.total: Optional[int] = None

    def add(self, offset: int, data: bytes, last: bool) -> Optional[bytes]:
        self.pieces[offset] = data
        if last:
            self.total = offset + len(data)
        if self.total is None:
            return None
        assembled = bytearray()
        position = 0
        for start in sorted(self.pieces):
            if start > position:
                return None  # a hole remains
            chunk = self.pieces[start]
            if start + len(chunk) > position:
                assembled += chunk[position - start :]
                position = start + len(chunk)
        return bytes(assembled) if position >= self.total else None


class FrameDecoder:
    """Decodes frames of one link type into :class:`UDPDatagram` objects.

    Keeps IP fragment state between frames, so feed it every frame of a
    capture in order. Anything that is not UDP over IP yields nothing.
    """

    def __init__(self, linktype: int) -> None:
        if linktype not in LINKTYPES:
            raise ValueError("unsupported link type %d" % linktype)
        self.linktype = linktype
        self._fragments: Dict[tuple, _Reassembly] = {}

    def decode(self, time: float, frame: bytes) -> List[UDPDatagram]:
        kind = LINKTYPES[self.linktype]
        try:
            if kind == "ETHERNET":
                ip = self._ethernet(frame)
            elif kind == "RAW":
                ip = frame
            elif kind == "IPV4" or kind == "IPV6":
                ip = frame
            elif kind == "NULL":
                family = struct.unpack("<I", frame[:4])[0] if frame[:4] != b"\0\0\0\0" else 0
                if family > 0xFFFF:  # written big-endian
                    family = struct.unpack(">I", frame[:4])[0]
                ip = frame[4:] if family in (2,) or family in _V6_FAMILIES else None
            elif kind == "LOOP":
                ip = frame[4:]
            elif kind == "LINUX_SLL":
                ip = frame[16:] if struct.unpack_from("!H", frame, 14)[0] in (0x0800, 0x86DD) else None
            else:  # LINUX_SLL2
                ip = frame[20:] if struct.unpack_from("!H", frame, 0)[0] in (0x0800, 0x86DD) else None
        except (struct.error, IndexError):
            return []
        if not ip:
            return []
        version = ip[0] >> 4
        try:
            if version == 4:
                return self._ipv4(time, ip)
            if version == 6:
                return self._ipv6(time, ip)
        except (struct.error, IndexError, ValueError):
            pass
        return []

    @staticmethod
    def _ethernet(frame: bytes) -> Optional[bytes]:
        offset = 12
        ethertype = struct.unpack_from("!H", frame, offset)[0]
        while ethertype in (0x8100, 0x88A8, 0x9100):  # VLAN tags, QinQ
            offset += 4
            ethertype = struct.unpack_from("!H", frame, offset)[0]
        if ethertype not in (0x0800, 0x86DD):
            return None
        return frame[offset + 2 :]

    def _remember(self, key: tuple) -> _Reassembly:
        state = self._fragments.get(key)
        if state is None:
            if len(self._fragments) >= _FRAGMENT_LIMIT:
                self._fragments.pop(next(iter(self._fragments)))
            state = self._fragments[key] = _Reassembly()
        return state

    def _ipv4(self, time: float, ip: bytes) -> List[UDPDatagram]:
        header = (ip[0] & 0x0F) * 4
        total = struct.unpack_from("!H", ip, 2)[0] or len(ip)  # 0 with TSO captures
        ident, flags_offset = struct.unpack_from("!HH", ip, 4)
        protocol = ip[9]
        if protocol != 17:
            return []
        source = socket.inet_ntop(socket.AF_INET, ip[12:16])
        destination = socket.inet_ntop(socket.AF_INET, ip[16:20])
        body = ip[header:total]
        more = bool(flags_offset & 0x2000)
        offset = (flags_offset & 0x1FFF) * 8
        if more or offset:
            key = (4, source, destination, ident)
            whole = self._remember(key).add(offset, body, not more)
            if whole is None:
                return []
            del self._fragments[key]
            body = whole
        return self._udp(time, source, destination, body)

    def _ipv6(self, time: float, ip: bytes) -> List[UDPDatagram]:
        payload_length = struct.unpack_from("!H", ip, 4)[0]
        next_header = ip[6]
        source = str(ipaddress.IPv6Address(ip[8:24]))
        destination = str(ipaddress.IPv6Address(ip[24:40]))
        body = ip[40 : 40 + payload_length] if payload_length else ip[40:]
        fragment = None
        while next_header in (0, 43, 44, 60):
            if next_header == 44:
                frag_offset_flags, ident = struct.unpack_from("!HI", body, 2)
                fragment = ((frag_offset_flags >> 3) * 8, bool(frag_offset_flags & 1), ident)
                next_header, body = body[0], body[8:]
            else:
                length = (body[1] + 1) * 8
                next_header, body = body[0], body[length:]
        if next_header != 17:
            return []
        if fragment is not None:
            offset, more, ident = fragment
            key = (6, source, destination, ident)
            whole = self._remember(key).add(offset, body, not more)
            if whole is None:
                return []
            del self._fragments[key]
            body = whole
        return self._udp(time, source, destination, body)

    @staticmethod
    def _udp(time: float, source: str, destination: str, body: bytes) -> List[UDPDatagram]:
        if len(body) < 8:
            return []
        sport, dport, length = struct.unpack_from("!HHH", body, 0)
        payload = body[8:length] if 8 <= length <= len(body) else body[8:]
        return [UDPDatagram(time, (source, sport), (destination, dport), payload)]
