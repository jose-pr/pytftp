"""Reading pcap/pcapng captures, and writing pcap from trace events.

Pure Python, both formats, either byte order, microsecond or nanosecond
timestamps, several interfaces per pcapng file. Reading works from a file or
a stream, so a live capture can be piped in::

    tcpdump -i eth0 -U -w - udp | pytftp capture -
"""

from __future__ import annotations

import ipaddress
import os
import struct
from typing import IO, Any, BinaryIO, Iterator, List, Optional, Tuple, Union

from .events import PacketEvent
from .frames import FrameDecoder, UdpDatagram

__all__ = ["read_frames", "read_datagrams", "PcapWriter"]

_PCAP_MAGICS = {
    b"\xd4\xc3\xb2\xa1": ("<", 1e-6),
    b"\xa1\xb2\xc3\xd4": (">", 1e-6),
    b"\x4d\x3c\xb2\xa1": ("<", 1e-9),
    b"\xa1\xb2\x3c\x4d": (">", 1e-9),
}
_PCAPNG_SHB = 0x0A0D0D0A


class CaptureFormatError(ValueError):
    """Not a pcap/pcapng capture, or a truncated one."""


def _read_exact(stream: BinaryIO, n: int) -> Optional[bytes]:
    data = b""
    while len(data) < n:
        chunk = stream.read(n - len(data))
        if not chunk:
            if data:
                raise CaptureFormatError("capture truncated")
            return None
        data += chunk
    return data


def _pcap(stream: BinaryIO, head: bytes) -> Iterator[Tuple[float, int, bytes]]:
    endian, scale = _PCAP_MAGICS[head[:4]]
    rest = _read_exact(stream, 20)
    if rest is None:
        return
    linktype = struct.unpack(endian + "I", rest[16:20])[0] & 0x0FFFFFFF
    record = struct.Struct(endian + "IIII")
    while True:
        header = _read_exact(stream, 16)
        if header is None:
            return
        seconds, fraction, caplen, _ = record.unpack(header)
        frame = _read_exact(stream, caplen)
        if frame is None:
            return
        yield seconds + fraction * scale, linktype, frame


def _pcapng(stream: BinaryIO, head: bytes) -> Iterator[Tuple[float, int, bytes]]:
    endian = "<"
    interfaces: List[Tuple[int, float]] = []
    block_type_bytes = head
    while True:
        if block_type_bytes is None:
            return
        length_bytes = _read_exact(stream, 4)
        if length_bytes is None:
            return
        raw_type = block_type_bytes
        if struct.unpack("<I", raw_type)[0] == _PCAPNG_SHB:
            magic = _read_exact(stream, 4)
            if magic is None:
                return
            endian = "<" if magic == b"\x4d\x3c\x2b\x1a" else ">"
            interfaces = []
            length = struct.unpack(endian + "I", length_bytes)[0]
            body = magic + (_read_exact(stream, length - 12) or b"")
        else:
            length = struct.unpack(endian + "I", length_bytes)[0]
            if length < 12:
                raise CaptureFormatError("bad pcapng block length")
            body = _read_exact(stream, length - 8) or b""
        block_type = struct.unpack(endian + "I", raw_type)[0]
        if block_type == 1:  # Interface Description Block
            linktype = struct.unpack_from(endian + "H", body, 0)[0]
            scale = 1e-6
            position = 8
            while position + 4 <= len(body) - 4:
                code, size = struct.unpack_from(endian + "HH", body, position)
                if code == 0:
                    break
                if code == 9 and size >= 1:  # if_tsresol
                    value = body[position + 4]
                    scale = 2.0 ** -(value & 0x7F) if value & 0x80 else 10.0**-value
                position += 4 + ((size + 3) & ~3)
            interfaces.append((linktype, scale))
        elif block_type == 6:  # Enhanced Packet Block
            iface, high, low, caplen = struct.unpack_from(endian + "IIII", body, 0)
            linktype, scale = interfaces[iface] if iface < len(interfaces) else (1, 1e-6)
            yield ((high << 32) | low) * scale, linktype, body[20 : 20 + caplen]
        elif block_type == 3:  # Simple Packet Block: no timestamp
            original = struct.unpack_from(endian + "I", body, 0)[0]
            linktype = interfaces[0][0] if interfaces else 1
            yield 0.0, linktype, body[4 : 4 + min(original, len(body) - 8)]
        block_type_bytes = _read_exact(stream, 4)


def read_frames(source: Union[str, "os.PathLike[str]", BinaryIO]) -> Iterator[Tuple[float, int, bytes]]:
    """``(time, linktype, frame)`` for every packet in a pcap or pcapng capture.

    ``source`` is a path or a binary stream (``sys.stdin.buffer`` for a live
    pipe). Raises :class:`CaptureFormatError` for anything else.
    """
    if isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as stream:
            yield from read_frames(stream)
        return
    head = _read_exact(source, 4)
    if head is None:
        return
    if head in _PCAP_MAGICS:
        yield from _pcap(source, head)
    elif struct.unpack("<I", head)[0] == _PCAPNG_SHB:
        yield from _pcapng(source, head)
    else:
        raise CaptureFormatError("not a pcap or pcapng capture")


def read_datagrams(source: Union[str, "os.PathLike[str]", BinaryIO]) -> Iterator[UdpDatagram]:
    """Every UDP datagram in a capture, IP fragments reassembled."""
    decoders = {}
    for time, linktype, frame in read_frames(source):
        decoder = decoders.get(linktype)
        if decoder is None:
            try:
                decoder = decoders[linktype] = FrameDecoder(linktype)
            except ValueError:
                continue
        yield from decoder.decode(time, frame)


def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


class PcapWriter:
    """Writes :class:`PacketEvent` objects (or raw datagrams) as a pcap file.

    Link type RAW (101): each datagram gets synthesized IPv4/IPv6 and UDP
    headers (with valid checksums), so Wireshark decodes it as TFTP. A
    v4-mapped IPv6 address is written as IPv4.

    Usable as a ``trace`` hook directly: ``Server(..., trace=PcapWriter(path))``.
    """

    def __init__(self, target: Union[str, "os.PathLike[str]", IO[bytes]]) -> None:
        if isinstance(target, (str, os.PathLike)):
            self._file: IO[bytes] = open(target, "wb")
            self._owned = True
        else:
            self._file = target
            self._owned = False
        self._file.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 101))
        self._ident = 0

    def __call__(self, event: PacketEvent) -> None:
        self.write(event.time, event.source, event.destination, event.data)

    def write(
        self, time: float, source: Tuple[Any, ...], destination: Tuple[Any, ...], payload: bytes
    ) -> None:
        src = ipaddress.ip_address(str(source[0]).split("%", 1)[0])
        dst = ipaddress.ip_address(str(destination[0]).split("%", 1)[0])
        if src.version == 6 and src.ipv4_mapped is not None:
            src = src.ipv4_mapped
        if dst.version == 6 and dst.ipv4_mapped is not None:
            dst = dst.ipv4_mapped
        length = 8 + len(payload)
        udp = struct.pack("!HHHH", int(source[1]), int(destination[1]), length, 0) + payload
        if src.version == 4 and dst.version == 4:
            pseudo = src.packed + dst.packed + struct.pack("!BBH", 0, 17, length)
            checksum = _checksum(pseudo + udp) or 0xFFFF
            udp = udp[:6] + struct.pack("!H", checksum) + udp[8:]
            self._ident = (self._ident + 1) & 0xFFFF
            header = struct.pack(
                "!BBHHHBBH4s4s", 0x45, 0, 20 + length, self._ident, 0, 64, 17, 0, src.packed, dst.packed
            )
            header = header[:10] + struct.pack("!H", _checksum(header)) + header[12:]
            packet = header + udp
        else:
            if src.version == 4:
                src = ipaddress.IPv6Address("::ffff:" + str(src))
            if dst.version == 4:
                dst = ipaddress.IPv6Address("::ffff:" + str(dst))
            pseudo = src.packed + dst.packed + struct.pack("!I3xB", length, 17)
            checksum = _checksum(pseudo + udp) or 0xFFFF
            udp = udp[:6] + struct.pack("!H", checksum) + udp[8:]
            packet = struct.pack("!IHBB", 0x60000000, length, 17, 64) + src.packed + dst.packed + udp
        seconds = int(time)
        micros = int(round((time - seconds) * 1e6))
        if micros >= 1_000_000:
            seconds, micros = seconds + 1, micros - 1_000_000
        self._file.write(struct.pack("<IIII", seconds, micros, len(packet), len(packet)) + packet)
        self._file.flush()

    def close(self) -> None:
        if self._owned:
            self._file.close()

    def __enter__(self) -> "PcapWriter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
