"""Interoperability with independent TFTP implementations.

Runs wherever the peers are found: tftp-hpa (server ``in.tftpd`` and client
``tftp``), BusyBox (``tftp`` and ``tftpd``), dnsmasq's TFTP server. The peer
servers need root (they chroot, drop privileges or bind port 69), so those
tests also need passwordless ``sudo -n``; each test skips, naming the binary or
the privilege that is missing, rather than fail.
"""

from __future__ import annotations

import io
import os
import shutil
import socket
import subprocess
import time

import pytest

import tftp
from tftp import TFTPOpcode
from tftp.packet import encode_error, encode_request

pytestmark = pytest.mark.interop

SHAPES = [
    pytest.param({"blksize": None}, id="rfc1350"),
    pytest.param({}, id="blksize1428"),
    pytest.param({"blksize": 8192, "windowsize": 8}, id="blksize8192-w8"),
]


def _which(*names):
    for name in names:
        path = shutil.which(name) or (os.path.exists("/usr/sbin/" + name) and "/usr/sbin/" + name)
        if path:
            return path
    return None


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _can_sudo() -> bool:
    if _is_root():
        return True
    if not hasattr(os, "geteuid"):
        return False
    try:
        return subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _root_cmd(argv):
    return argv if _is_root() else ["sudo", "-n", *argv]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def served(tmp_path_factory):
    """A world-readable/writable directory with a test file."""
    root = tmp_path_factory.mktemp("peer")
    os.chmod(root, 0o777)
    data = os.urandom(300_001)
    (root / "f.bin").write_bytes(data)
    os.chmod(root / "f.bin", 0o666)
    return root, data


class PeerServer:
    def __init__(self, argv, port, pattern):
        self.port = port
        self.pattern = pattern
        self.proc = subprocess.Popen(_root_cmd(argv), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if not self._serves():
            self.stop()
            pytest.skip("peer server does not serve its directory: %s" % self._stderr)

    def _serves(self) -> bool:
        """Ask for the served file until this peer sends it.

        Any answer is not enough: dnsmasq takes port 69, where a system TFTP
        service may answer in its place, and a peer that dropped privileges
        may be unable to read the directory. Both answer "file not found".
        """
        request = encode_request(TFTPOpcode.RRQ, "f.bin")
        deadline = time.monotonic() + 10.0
        while self.proc.poll() is None and time.monotonic() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.settimeout(0.25)
                probe.sendto(request, ("127.0.0.1", self.port))
                try:
                    answer, peer = probe.recvfrom(2048)
                except OSError:
                    continue
                if answer[:2] == b"\x00\x03":
                    probe.sendto(encode_error(0, "probe"), peer)  # end the transfer the probe began
                    return True
                time.sleep(0.25)
        return False

    def stop(self):
        subprocess.run(_root_cmd(["pkill", "-f", self.pattern]), capture_output=True)
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:  # pragma: no cover
            self.proc.kill()
            self.proc.wait(5)
        self._stderr = self.proc.stderr.read().decode(errors="replace")[:200]
        self.proc.stderr.close()


def _peer(kind, root):
    binary = {"tftp-hpa": "in.tftpd", "busybox": "busybox", "dnsmasq": "dnsmasq"}[kind]
    if _which(binary) is None:
        pytest.skip("%s (the %s server) is not installed" % (binary, kind))
    if not _can_sudo():
        pytest.skip("the %s server needs root: passwordless sudo is not available" % kind)
    if kind == "tftp-hpa":
        binary = _which("in.tftpd")
        port = _free_port()
        address = "127.0.0.1:%d" % port
        return PeerServer(
            [binary, "-L", "-c", "-a", address, "-s", str(root)], port, "in.tftpd -L -c -a " + address
        )
    if kind == "busybox":
        port = _free_port()
        return PeerServer(
            ["busybox", "udpsvd", "-E", "127.0.0.1", str(port), "busybox", "tftpd", "-c", str(root)],
            port,
            "udpsvd -E 127.0.0.1 %d" % port,
        )
    if kind == "dnsmasq":
        binary = _which("dnsmasq")
        # dnsmasq has no TFTP listen-port option: it takes port 69.
        argv = [
            binary,
            "--no-daemon",
            "--port=0",
            "--enable-tftp",
            "--tftp-root=" + str(root),
            "--listen-address=127.0.0.1",
            "--bind-interfaces",
            "--conf-file=/dev/null",
            "--pid-file=",
            "--user=root",  # or it changes user and cannot enter pytest's private directory
        ]
        return PeerServer(argv, 69, "dnsmasq --no-daemon --port=0 --enable-tftp")
    raise AssertionError(kind)


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("kind", ["tftp-hpa", "busybox", "dnsmasq"])
def test_our_client_downloads_from_peer(served, kind, shape):
    root, data = served
    server = _peer(kind, root)
    try:
        sink = io.BytesIO()
        result = tftp.TFTPClient("127.0.0.1", server.port, timeout=1.0, retries=3, **shape).download(
            "f.bin", sink
        )
        assert sink.getvalue() == data
        if shape.get("blksize") is not None:
            assert "blksize" in result.negotiated.options
    finally:
        server.stop()


@pytest.mark.parametrize("kind", ["tftp-hpa", "busybox"])
def test_our_client_uploads_to_peer(served, kind):
    root, data = served
    server = _peer(kind, root)
    try:
        if kind == "tftp-hpa":
            # tftp-hpa runs as nobody: the file it writes must be writable by it.
            (root / "up.bin").write_bytes(b"")
            os.chmod(root / "up.bin", 0o666)
        tftp.TFTPClient("127.0.0.1", server.port, timeout=1.0, retries=3, windowsize=None).upload(
            "up.bin", data
        )
        assert (root / "up.bin").read_bytes() == data
    finally:
        server.stop()


def test_our_client_sees_peer_errors(served):
    root, _ = served
    server = _peer("tftp-hpa", root)
    try:
        with pytest.raises(tftp.FileNotFound):
            tftp.TFTPClient("127.0.0.1", server.port, timeout=1.0).get("missing.bin")
    finally:
        server.stop()


# -- their clients, our server: no root needed ------------------------------------------------------


@pytest.fixture
def our_server(served):
    root, data = served
    with tftp.TFTPServer(
        str(root), host="127.0.0.1", port=0, writable=True, overwrite=True, timeout=1.0
    ) as server:
        server.start()
        yield server, root, data


def test_tftp_hpa_client(our_server):
    binary = _which("tftp")
    if binary is None or "hpa" not in subprocess.run([binary, "-V"], capture_output=True, text=True).stdout:
        pytest.skip("tftp-hpa client not installed")
    server, root, data = our_server
    port = str(server.server_address[1])
    out = root / "hpa-out.bin"
    r = subprocess.run(
        [binary, "-m", "binary", "127.0.0.1", port, "-c", "get", "f.bin", str(out)], capture_output=True
    )
    assert r.returncode == 0 and out.read_bytes() == data
    r = subprocess.run(
        [binary, "-m", "binary", "127.0.0.1", port, "-c", "put", str(out), "hpa-up.bin"], capture_output=True
    )
    assert r.returncode == 0 and (root / "hpa-up.bin").read_bytes() == data


@pytest.mark.parametrize("blksize", ["512", "1428", "65464"])
def test_busybox_client(our_server, blksize):
    if _which("busybox") is None:
        pytest.skip("busybox not installed")
    server, root, data = our_server
    port = str(server.server_address[1])
    out = root / ("bb-%s.bin" % blksize)
    r = subprocess.run(
        ["busybox", "tftp", "-g", "-r", "f.bin", "-l", str(out), "-b", blksize, "127.0.0.1", port],
        capture_output=True,
    )
    assert r.returncode == 0 and out.read_bytes() == data
    r = subprocess.run(
        [
            "busybox",
            "tftp",
            "-p",
            "-l",
            str(out),
            "-r",
            "bb-up-%s.bin" % blksize,
            "-b",
            blksize,
            "127.0.0.1",
            port,
        ],
        capture_output=True,
    )
    assert r.returncode == 0 and (root / ("bb-up-%s.bin" % blksize)).read_bytes() == data
