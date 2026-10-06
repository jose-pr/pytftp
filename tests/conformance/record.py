"""Record what tftp-hpa answers to each conformance case. Development only.

    python tests/conformance/record.py              # write every golden.json
    python tests/conformance/record.py --check      # re-record in memory, report drift
    python tests/conformance/record.py NAME ...     # only these cases

The reference is tftp-hpa's server, ``in.tftpd``, run on loopback over a
directory holding the case's files. It binds a port and chroots, so this needs
root or passwordless ``sudo -n``, on Linux. A golden is the exchange as the
wire carried it, with the reference's version and the system it ran on, and is
never edited by hand: ``test_conformance.py`` replays the goldens and needs
neither this script nor tftp-hpa.

Exit status: 0 when every golden was written, or in ``--check`` matches what the
reference answers now; 1 on drift; 2 when the reference cannot be run.
"""

from __future__ import annotations

import json
import os
import pathlib
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

import exchange

HERE = pathlib.Path(__file__).resolve().parent
ROUTES = ("/usr/sbin/in.tftpd", "/usr/libexec/in.tftpd", "/usr/local/sbin/in.tftpd")


def _binary() -> Optional[str]:
    found = shutil.which("in.tftpd")
    return found or next((p for p in ROUTES if os.path.exists(p)), None)


def _as_root(argv: List[str]) -> List[str]:
    return argv if os.geteuid() == 0 else ["sudo", "-n", *argv]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _reference(binary: str) -> Dict[str, str]:
    version = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10)
    return {
        "tftpd": (version.stdout or version.stderr).strip().splitlines()[0],
        "environment": "%s %s" % (platform.system(), platform.machine()),
    }


def _answering(proc: "subprocess.Popen[bytes]", port: int) -> bool:
    """Ask for a file that is not there until the server answers, whatever it answers."""
    probe_request = exchange.request({"op": "RRQ", "file": "ready-probe"})
    deadline = time.monotonic() + 10.0
    while proc.poll() is None and time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.settimeout(0.25)
            probe.sendto(probe_request, ("127.0.0.1", port))
            try:
                probe.recvfrom(2048)
                return True
            except OSError:
                continue
    return False


def record(binary: str, case: pathlib.Path) -> List[Dict[str, str]]:
    """The exchange of one case against a fresh tftp-hpa over a fresh directory."""
    spec = exchange.load(case)
    with tempfile.TemporaryDirectory() as workdir:
        root = pathlib.Path(workdir)
        exchange.prepare(root, spec)
        port = _free_port()
        address = "127.0.0.1:%d" % port
        argv = [binary, "-L", "-c", "-a", address, "-s", str(root)]
        proc = subprocess.Popen(_as_root(argv), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            if not _answering(proc, port):
                raise RuntimeError(
                    "in.tftpd did not answer: %s" % proc.stderr.read().decode(errors="replace")
                )
            played = exchange.play(spec, ("127.0.0.1", port))
        finally:
            subprocess.run(_as_root(["pkill", "-f", "in.tftpd -L -c -a " + address]), capture_output=True)
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(5)
            proc.stderr.close()
    return [{"sender": sender, "payload": datagram.hex()} for sender, datagram in played]


def _golden(binary: str, case: pathlib.Path) -> Dict[str, Any]:
    return {"reference": _reference(binary), "exchange": record(binary, case)}


def _drift(old: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    notes = []
    if old["reference"]["tftpd"] != new["reference"]["tftpd"]:
        notes.append("version %s -> %s" % (old["reference"]["tftpd"], new["reference"]["tftpd"]))
    before, after = old["exchange"], new["exchange"]
    if len(before) != len(after):
        notes.append("%d datagrams -> %d" % (len(before), len(after)))
    for i, (a, b) in enumerate(zip(before, after)):
        if a != b:
            notes.append(
                "datagram %d: %s %s -> %s %s"
                % (
                    i,
                    a["sender"],
                    exchange.summary(bytes.fromhex(a["payload"])),
                    b["sender"],
                    exchange.summary(bytes.fromhex(b["payload"])),
                )
            )
    return notes


def main(argv: List[str]) -> int:
    check = "--check" in argv
    names = [a for a in argv if not a.startswith("--")]
    binary = _binary()
    if binary is None or not hasattr(os, "geteuid"):
        print("needs in.tftpd (tftp-hpa) on a Linux host")
        return 2
    drifted = 0
    for case in exchange.cases():
        if names and case.name not in names:
            continue
        if exchange.load(case).get("reference", True) is None:
            print("%-44s no reference recording (see its case.json)" % case.name)
            continue
        golden = _golden(binary, case)
        path = case / "golden.json"
        if check:
            notes = (
                _drift(json.loads(path.read_text(encoding="utf-8")), golden)
                if path.exists()
                else ["no golden"]
            )
            print("%-44s %s" % (case.name, "; ".join(notes) or "no drift"))
            drifted += bool(notes)
        else:
            path.write_text(json.dumps(golden, indent=1) + "\n", encoding="utf-8", newline="\n")
            print("%-44s recorded %d datagrams" % (case.name, len(golden["exchange"])))
    return 1 if drifted else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
