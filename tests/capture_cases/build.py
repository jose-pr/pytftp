"""The captures `tests/test_capture_output.py` reads, and the output `pytftp capture` gives for each.

    python tests/capture_cases/build.py                    # write the captures
    python tests/capture_cases/build.py --expected [NAME]  # record the command's output

A capture is data: fixed times, loopback and documentation addresses only. The
expected output is recorded by the command as it is when this runs, so a change
to it is a change to a file under `expected/`, visible in the diff.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

from tftp import TFTPOpcode
from tftp.packet import encode_ack, encode_data, encode_error, encode_oack, encode_request

HERE = Path(__file__).resolve().parent
EXPECTED = HERE / "expected"

EPOCH = 1_700_000_000
CLIENT = "192.0.2.5"
SERVER = "192.0.2.1"
STRANGER = "192.0.2.77"
V6_CLIENT = "2001:db8::5"
V6_SERVER = "2001:db8::1"

#: What each variant passes after ``capture FILE``.
VARIANTS: Dict[str, List[str]] = {
    "text": [],
    "json": ["--json", "--payload"],
    "transfers": ["--no-packets", "--transfers"],
    "json_transfers": ["--json", "--no-packets", "--transfers"],
    "filter_ops": ["--filter", "op=RRQ,WRQ,ERROR"],
    "filter_hosts": ["--filter", "host=192.0.2.0/24,2001:db8::/32 and dst!=:69"],
    "filter_endpoints": ["--filter", "src=[2001:db8::5]:1000,192.0.2.5:2000"],
    "filter_file": ["--filter", "file=*.bin,*.txt"],
}
EXTRACT = "extract"

Datagram = Tuple[float, Tuple[str, int], Tuple[str, int], bytes]


def _pattern(length: int, seed: int = 0) -> bytes:
    return bytes((seed + 7 * i) % 251 for i in range(length))


# -- the datagrams of plain.pcap -------------------------------------------------------


def plain_script() -> List[Datagram]:
    """IPv4 datagrams of four transfers and two strays, in the order they were seen.

    ``plain.pcap`` is committed as the library's own pcap writer wrote this script once;
    a test writes it again through ``pktcap`` and compares.
    """
    script: List[Datagram] = []
    clock = [EPOCH + 0.5]

    def add(src: Tuple[str, int], dst: Tuple[str, int], payload: bytes) -> None:
        clock[0] += 0.012345
        script.append((clock[0], src, dst, payload))

    request = (SERVER, 69)
    # A read with options and an OACK; DATA 1 is seen twice.
    client, tid = (CLIENT, 2000), (SERVER, 40001)
    add(
        client,
        request,
        encode_request(
            TFTPOpcode.RRQ, "boot/ipxe.efi", options={"blksize": 512, "windowsize": 2, "tsize": 0}
        ),
    )
    add(tid, client, encode_oack({"blksize": 512, "windowsize": 2, "tsize": 1124}))
    add(client, tid, encode_ack(0))
    add(tid, client, encode_data(1, _pattern(512, 1)))
    add(tid, client, encode_data(1, _pattern(512, 1)))
    add(tid, client, encode_data(2, _pattern(512, 2)))
    add(client, tid, encode_ack(2))
    add(tid, client, encode_data(3, _pattern(100, 3)))
    add(client, tid, encode_ack(3))
    # A write in netascii, with a name that needs cleaning for a file.
    client, tid = (CLIENT, 2001), (SERVER, 40002)
    add(client, request, encode_request(TFTPOpcode.WRQ, "logs/Net Ascii.txt", mode="netascii"))
    add(tid, client, encode_ack(0))
    add(client, tid, encode_data(1, b"one\r\ntwo\r\nthree\r\n"))
    add(tid, client, encode_ack(1))
    # A read the server refuses.
    client, tid = (CLIENT, 2002), (SERVER, 40003)
    add(client, request, encode_request(TFTPOpcode.RRQ, "missing.bin"))
    add(tid, client, encode_error(1, "file not found"))
    # A read with a hole: block 2 is never seen.
    client, tid = (CLIENT, 2003), (SERVER, 40004)
    add(client, request, encode_request(TFTPOpcode.RRQ, "holey.bin"))
    add(tid, client, encode_data(1, _pattern(512, 4)))
    add(tid, client, encode_data(3, _pattern(50, 5)))
    add(client, tid, encode_ack(3))
    # A datagram to the request port that is not TFTP, and one that is not for it.
    add((STRANGER, 5555), request, b"hello")
    add((CLIENT, 9999), (SERVER, 9998), b"not tftp, not port 69")
    return script


# -- frames ---------------------------------------------------------------------------------


def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def udp(sport: int, dport: int, payload: bytes) -> bytes:
    return struct.pack("!HHHH", sport, dport, 8 + len(payload), 0) + payload


def ipv4(src: str, dst: str, payload: bytes, ident: int = 1, offset: int = 0, more: bool = False) -> bytes:
    flags = (0x2000 if more else 0) | (offset // 8)
    header = struct.pack(
        "!BBHHHBBH4s4s",
        0x45,
        0,
        20 + len(payload),
        ident,
        flags,
        64,
        17,
        0,
        bytes(map(int, src.split("."))),
        bytes(map(int, dst.split("."))),
    )
    return header[:10] + struct.pack("!H", _checksum(header)) + header[12:] + payload


def ipv6(src: bytes, dst: bytes, next_header: int, payload: bytes) -> bytes:
    return struct.pack("!IHBB", 0x60000000, len(payload), next_header, 64) + src + dst + payload


def v6_address(text: str) -> bytes:
    import ipaddress

    return ipaddress.IPv6Address(text).packed


def ether(ip: bytes, vlan: bool = False, v6: bool = False) -> bytes:
    ethertype = struct.pack("!H", 0x86DD if v6 else 0x0800)
    tag = b"\x81\x00\x00\x05" if vlan else b""
    return b"\x02" * 6 + b"\x04" * 6 + tag + ethertype + ip


def linux_cooked(ip: bytes) -> bytes:
    return struct.pack("!HHH8sH", 0, 772, 0, b"", 0x86DD) + ip


def _block(block_type: int, body: bytes) -> bytes:
    body += b"\0" * (-len(body) % 4)
    return struct.pack("<II", block_type, 12 + len(body)) + body + struct.pack("<I", 12 + len(body))


def pcapng(frames: List[bytes], linktype: int, nanoseconds: bool = False) -> bytes:
    """A pcapng file of one section and one interface; frame ``i`` is stamped ``EPOCH + i / 100``."""
    out = _block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
    options = struct.pack("<HHB3x", 9, 1, 9) + struct.pack("<HH", 0, 0) if nanoseconds else b""
    out += _block(1, struct.pack("<HHI", linktype, 0, 65535) + options)
    for index, frame in enumerate(frames):
        micros = EPOCH * 1_000_000 + index * 10_000 + 123
        stamp = micros * 1000 if nanoseconds else micros
        body = struct.pack("<IIIII", 0, stamp >> 32, stamp & 0xFFFFFFFF, len(frame), len(frame)) + frame
        out += _block(6, body)
    return out


def pcap(records: List[Tuple[float, bytes, int]], linktype: int) -> bytes:
    """A pcap file; a record is ``(time, captured octets, length on the wire)``."""
    out = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, linktype)
    for time, data, wire in records:
        seconds = int(time)
        out += struct.pack("<IIII", seconds, int(round((time - seconds) * 1e6)), len(data), wire) + data
    return out


def vlan_fragments() -> bytes:
    """Ethernet with a VLAN tag; a DATA in IPv4 fragments, the last one first; an ARP frame."""
    data = udp(50001, 50000, encode_data(1, _pattern(2000, 6)))
    frames = [
        ether(ipv4(CLIENT, SERVER, udp(50000, 69, encode_request(1, "frag.bin", options={"blksize": 3000})))),
        ether(ipv4(SERVER, CLIENT, udp(50001, 50000, encode_oack({"blksize": 3000}))), vlan=True),
        ether(ipv4(CLIENT, SERVER, udp(50000, 50001, encode_ack(0)))),
        ether(ipv4(SERVER, CLIENT, data[1480:], ident=9, offset=1480)),
        ether(ipv4(SERVER, CLIENT, data[:1480], ident=9, offset=0, more=True), vlan=True),
        ether(ipv4(CLIENT, SERVER, udp(50000, 50001, encode_ack(1)))),
        b"\x02" * 12 + b"\x08\x06" + b"\0" * 28,
    ]
    return pcapng(frames, linktype=1)


def cooked_v6() -> bytes:
    """Linux cooked capture, nanosecond stamps; a WRQ in two IPv6 fragments, then the transfer."""
    client, server = v6_address(V6_CLIENT), v6_address(V6_SERVER)
    request = udp(1000, 69, encode_request(TFTPOpcode.WRQ, "v6.txt", mode="netascii"))

    def fragment(chunk: bytes, offset: int, more: bool) -> bytes:
        header = struct.pack("!BBHI", 17, 0, (offset // 8) << 3 | (1 if more else 0), 77)
        return linux_cooked(ipv6(client, server, 44, header + chunk))

    def whole(src: bytes, dst: bytes, sport: int, dport: int, payload: bytes) -> bytes:
        return linux_cooked(ipv6(src, dst, 17, udp(sport, dport, payload)))

    frames = [
        fragment(request[:16], 0, True),
        fragment(request[16:], 16, False),
        whole(server, client, 3000, 1000, encode_ack(0)),
        whole(client, server, 1000, 3000, encode_data(1, b"a\r\nb\r\n")),
        whole(server, client, 3000, 1000, encode_ack(1)),
    ]
    return pcapng(frames, linktype=113, nanoseconds=True)


def mapped() -> bytes:
    """Ethernet carrying IPv6 whose addresses are IPv4-mapped."""
    client, server = v6_address("::ffff:" + CLIENT), v6_address("::ffff:" + SERVER)

    def frame(src: bytes, dst: bytes, sport: int, dport: int, payload: bytes) -> bytes:
        return ether(ipv6(src, dst, 17, udp(sport, dport, payload)), v6=True)

    frames = [
        frame(client, server, 2000, 69, encode_request(TFTPOpcode.RRQ, "mapped.bin")),
        frame(server, client, 3000, 2000, encode_data(1, _pattern(40, 8))),
        frame(client, server, 2000, 3000, encode_ack(1)),
    ]
    return pcapng(frames, linktype=1)


def wifi() -> bytes:
    """Frames of link type 105, IEEE 802.11, which nothing here reads."""
    return pcapng([_pattern(60, seed) for seed in (1, 2, 3)], linktype=105)


def cut() -> bytes:
    """Raw IP; a DATA the capture's snap length cut: its headers state 512 octets, the record holds 100."""
    request = ipv4(CLIENT, SERVER, udp(2000, 69, encode_request(TFTPOpcode.RRQ, "cut.bin")))
    first = ipv4(SERVER, CLIENT, udp(40001, 2000, encode_data(1, _pattern(512, 9))))
    second = ipv4(SERVER, CLIENT, udp(40001, 2000, encode_data(2, _pattern(512, 10))), ident=2)
    ack = ipv4(CLIENT, SERVER, udp(2000, 40001, encode_ack(2)), ident=3)
    kept = 20 + 8 + 4 + 100
    records = [
        (EPOCH + 1.0, request, len(request)),
        (EPOCH + 1.1, first, len(first)),
        (EPOCH + 1.2, second[:kept], len(second)),
        (EPOCH + 1.3, ack, len(ack)),
    ]
    return pcap(records, linktype=101)


#: Captures this module writes; ``plain.pcap`` is only committed.
BUILT = {
    "vlan_fragments.pcapng": vlan_fragments,
    "cooked_v6.pcapng": cooked_v6,
    "mapped.pcapng": mapped,
    "wifi.pcapng": wifi,
    "cut.pcap": cut,
}
CAPTURES = ("plain.pcap", *BUILT)


def write_captures(directory: Path = HERE) -> None:
    for name, build in BUILT.items():
        (directory / name).write_bytes(build())


# -- the command's output -----------------------------------------------------------------


def run_capture(capture: Path, extra: List[str]) -> Tuple[bytes, int]:
    """``pytftp capture CAPTURE EXTRA`` as a process: stdout with line feeds, and the exit status."""
    env = dict(os.environ, TZ="UTC", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    proc = subprocess.run(
        [sys.executable, "-m", "tftp", "capture", str(capture), *extra],
        capture_output=True,
        env=env,
        timeout=120,
    )
    return proc.stdout.replace(os.linesep.encode(), b"\n"), proc.returncode


def run_extract(capture: Path, directory: Path) -> Tuple[bytes, int, Dict[str, bytes]]:
    """The same with ``--no-packets --extract``: stdout, exit status and the files written."""
    out, status = run_capture(capture, ["--no-packets", "--extract", str(directory)])
    files = {p.name: p.read_bytes() for p in sorted(directory.glob("*"))} if directory.exists() else {}
    return out, status, files


def record_expected(names: List[str]) -> None:
    for name in names or CAPTURES:
        target = EXPECTED / Path(name).stem
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True)
        for variant, extra in VARIANTS.items():
            out, status = run_capture(HERE / name, extra)
            (target / (variant + ".out")).write_bytes(out)
            (target / (variant + ".status")).write_bytes(b"%d\n" % status)
        with tempfile.TemporaryDirectory() as scratch:
            out, status, files = run_extract(HERE / name, Path(scratch) / "files")
        (target / (EXTRACT + ".out")).write_bytes(out)
        (target / (EXTRACT + ".status")).write_bytes(b"%d\n" % status)
        for file_name, content in files.items():
            (target / EXTRACT).mkdir(exist_ok=True)
            (target / EXTRACT / file_name).write_bytes(content)


if __name__ == "__main__":
    arguments = sys.argv[1:]
    if arguments[:1] == ["--expected"]:
        record_expected(arguments[1:])
    else:
        write_captures()
