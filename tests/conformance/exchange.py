"""One client script over loopback, shared by the recorder and the replay.

A case says what a client asks and how it carries on; ``play`` does it against
the server at an address and returns every datagram of the exchange. The
recorder plays it against tftp-hpa and stores the result; the replay plays it
against this library's server and compares. The wire is read here with
``struct`` and nothing else, so what a golden holds does not depend on the
code under test.

A case (``cases/<name>/case.json``)::

    description  what the case is about
    allow        extra option names the server under test is to allow
    files        {name: size}: files of a known pattern in the served directory
    request      {"op": "RRQ" or "WRQ", "file", "mode", "options": [[name, value], ...]}
    follow       a read: how many DATA datagrams to take before ending the transfer
    keep         optional, {"data": [first, last]}: only the DATA datagrams numbered
                 first..last (counting from 1) are kept, the rest are acknowledged unseen
    reference    optional, ``null`` when no recording of the reference can be made;
                 ``reason`` then says why
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import struct
from typing import Any, Dict, List, Tuple

HERE = pathlib.Path(__file__).resolve().parent
CASES = HERE / "cases"
RRQ, WRQ, DATA, ACK, ERROR, OACK = 1, 2, 3, 4, 5, 6
NAMES = {RRQ: "RRQ", WRQ: "WRQ", DATA: "DATA", ACK: "ACK", ERROR: "ERROR", OACK: "OACK"}
#: A server that says nothing within this many seconds has said all it will.
SILENCE = 2.0

Datagram = Tuple[str, bytes]


def pattern(size: int) -> bytes:
    """``size`` octets that differ from block to block and are the same on every host."""
    return bytes((i * 7 + i // 251) % 256 for i in range(size))


def cases() -> List[pathlib.Path]:
    return sorted(p for p in CASES.iterdir() if (p / "case.json").is_file())


def load(case: pathlib.Path) -> Dict[str, Any]:
    return json.loads((case / "case.json").read_text(encoding="utf-8"))


def prepare(root: pathlib.Path, case: Dict[str, Any]) -> None:
    """Create the case's files under ``root``, readable and writable by anyone."""
    os.chmod(root, 0o777)
    for name, size in case.get("files", {}).items():
        path = root / name
        path.write_bytes(pattern(size))
        os.chmod(path, 0o666)


def request(spec: Dict[str, Any]) -> bytes:
    opcode = {"RRQ": RRQ, "WRQ": WRQ}[spec["op"]]
    fields = [spec["file"], spec.get("mode", "octet")]
    for name, value in spec.get("options", []):
        fields += [name, value]
    return struct.pack("!H", opcode) + b"".join(f.encode("ascii") + b"\0" for f in fields)


def opcode(datagram: bytes) -> int:
    return int(struct.unpack("!H", datagram[:2])[0])


def block(datagram: bytes) -> int:
    return int(struct.unpack("!H", datagram[2:4])[0])


def options(datagram: bytes) -> Dict[str, str]:
    """The name and value pairs of an OACK, the names in lower case."""
    fields = datagram[2:].split(b"\0")[:-1]
    return {k.decode().lower(): v.decode() for k, v in zip(fields[::2], fields[1::2])}


def summary(datagram: bytes) -> str:
    """What a datagram says, in words: its opcode, then its block, its options or its code."""
    op = opcode(datagram)
    if op == OACK:
        return "OACK " + " ".join("%s=%s" % pair for pair in sorted(options(datagram).items()))
    if op == DATA:
        return "DATA %d %d" % (block(datagram), len(datagram) - 4)
    if op in (ACK, ERROR):
        return "%s %d" % (NAMES[op], block(datagram))
    return NAMES.get(op, "OP%d" % op)


def same(wanted: bytes, got: bytes) -> bool:
    """Whether two server datagrams say the same: an OACK by its options, an ERROR by its code."""
    if opcode(wanted) != opcode(got):
        return False
    if opcode(wanted) == OACK:
        return options(wanted) == options(got)
    if opcode(wanted) == ERROR:
        return block(wanted) == block(got)
    return wanted == got


def play(case: Dict[str, Any], address: Tuple[str, int]) -> List[Datagram]:
    """The exchange of ``case`` with the server at ``address``: ``(sender, datagram)`` in order."""
    spec = case["request"]
    first, last = case.get("keep", {}).get("data", (1, 1 << 31))
    seen: List[Datagram] = []
    taken = 0
    size = 512
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.settimeout(SILENCE)
        sock.sendto(request(spec), address)
        seen.append(("client", request(spec)))
        peer = address
        while True:
            try:
                datagram, peer = sock.recvfrom(70000)
            except socket.timeout:
                return seen
            number = opcode(datagram)
            taken += number == DATA
            kept = number != DATA or first <= taken <= last
            if kept:
                seen.append(("server", datagram))
            if number == ERROR:
                return seen
            if number == OACK and spec["op"] == "WRQ" or number not in (OACK, DATA):
                break
            if number == OACK:
                granted = options(datagram)
                size = int(granted.get("blksize2", granted.get("blksize", size)))
                reply = struct.pack("!HH", ACK, 0)
            else:
                reply = struct.pack("!HH", ACK, block(datagram))
            sock.sendto(reply, peer)
            if kept:
                seen.append(("client", reply))
            if number == DATA and len(datagram) - 4 < size:
                return seen
            if number == DATA and taken >= case.get("follow", 1):
                break
        ending = struct.pack("!HH", ERROR, 0) + b"done\0"
        sock.sendto(ending, peer)
        seen.append(("client", ending))
    return seen
