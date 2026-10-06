"""Writing pcap from trace events."""

from __future__ import annotations

import ipaddress
import os
import struct
from typing import IO, Any, Tuple, Union

from ._events import PacketEvent

__all__ = ["PcapWriter"]


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

    Usable as a ``trace`` hook directly: ``TFTPServer(..., trace=PcapWriter(path))``.
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
