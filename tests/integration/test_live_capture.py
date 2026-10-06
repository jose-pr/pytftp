"""`pytftp capture -i`: live capture on Linux, which needs CAP_NET_RAW (root, or a capability on the interpreter)."""

from __future__ import annotations

import json
import signal
import socket
import subprocess
import sys
import threading
import time

import pytest

pytest.importorskip("duho")

import tftp  # noqa: E402


def _can_capture() -> str:
    """Why this process cannot open a packet socket, or an empty string when it can."""
    if not hasattr(socket, "AF_PACKET"):
        return "no AF_PACKET on this platform"
    try:
        socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3)).close()
    except PermissionError:
        return "needs CAP_NET_RAW"
    except OSError as exc:
        return "no packet socket: %s" % exc
    return ""


_WHY_NOT = _can_capture()
pytestmark = pytest.mark.skipif(bool(_WHY_NOT), reason=_WHY_NOT)


def test_a_transfer_on_loopback_is_followed_live(tmp_path):
    (tmp_path / "live.bin").write_bytes(b"live" * 600)
    server = tftp.TFTPServer(str(tmp_path), host="127.0.0.1", port=0, timeout=1.0).start()
    port = server.server_address[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "tftp", "capture", "-i", "lo", "-p", str(port), "--json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    lines = []
    reader = threading.Thread(target=lambda: lines.extend(iter(proc.stdout.readline, "")), daemon=True)
    reader.start()
    try:
        client = tftp.TFTPClient("127.0.0.1", port, timeout=2.0)
        deadline = time.monotonic() + 20
        while not lines and time.monotonic() < deadline:  # until the capture is listening
            client.get("live.bin")
            time.sleep(0.2)
        assert lines, "the capture saw nothing: %s" % proc.poll()
        assert client.get("live.bin") == b"live" * 600
        time.sleep(1.5)  # the socket is read with a one second timeout
    finally:
        proc.send_signal(signal.SIGINT)
        out, err = proc.communicate(timeout=30)
        server.close()
    records = [json.loads(line) for line in lines + out.splitlines()]
    requests = [r for r in records if r["opcode"] == "RRQ"]
    assert requests and {r["filename"] for r in requests} == {"live.bin"}
    assert all(
        r["source"].startswith("127.0.0.1:") and r["destination"] == "127.0.0.1:%d" % port for r in requests
    )
    assert {"DATA", "ACK"} <= {r["opcode"] for r in records}
    assert "Traceback" not in err, err
    assert proc.returncode == 0
