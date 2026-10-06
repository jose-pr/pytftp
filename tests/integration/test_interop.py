"""Interoperability with curl's TFTP client (skipped when curl lacks TFTP)."""

from __future__ import annotations

import shutil
import subprocess

import pytest


def _curl_tftp() -> bool:
    curl = shutil.which("curl")
    if curl is None:
        return False
    try:
        out = subprocess.run([curl, "--version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return " tftp" in out


pytestmark = [
    pytest.mark.interop,
    pytest.mark.skipif(not _curl_tftp(), reason="curl with TFTP support not found"),
]


def _udp_receive_buffer():
    """The default UDP receive buffer in octets (``net.inet.udp.recvspace``), or ``None`` where sysctl lacks it."""
    sysctl = shutil.which("sysctl")
    if sysctl is None:
        return None
    try:
        out = subprocess.run(
            [sysctl, "-n", "net.inet.udp.recvspace"], capture_output=True, text=True, timeout=10
        )
        return int(out.stdout.strip()) if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


MAX_BLOCKS = 1000


def curl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["curl", "-sS", "--max-time", "20", *args], capture_output=True, timeout=30)


@pytest.mark.parametrize("blksize", [None, "8", "1428", "65464"])
@pytest.mark.parametrize("name", ["empty.bin", "512.bin", "513.bin", "big.bin"])
def test_curl_downloads_from_server(root, make_server, tmp_path, name, blksize):
    if blksize:
        # curl does not enlarge its receive buffer: where the default is smaller than the datagram
        # (FreeBSD, 42080), the kernel drops it unreported and no server can make it arrive.
        space = _udp_receive_buffer()
        if space is not None and space < int(blksize) + 4:
            pytest.skip(
                "this host's default UDP receive buffer (%d) cannot hold a %s-octet block" % (space, blksize)
            )
    # Lock-step round trips are the cost, not octets: a file of more than MAX_BLOCKS blocks proves
    # nothing more about the block size and cannot finish in curl's deadline on a slow host.
    served = (root / name).read_bytes()[: int(blksize or 512) * MAX_BLOCKS + 3]
    (root / name).write_bytes(served)
    server = make_server(root)
    out = tmp_path / "out.bin"
    extra = ["--tftp-blksize", blksize] if blksize else []
    proc = curl(*extra, "-o", str(out), "tftp://127.0.0.1:%d/%s" % (server.server_address[1], name))
    assert proc.returncode == 0, proc.stderr
    assert out.read_bytes() == served


@pytest.mark.parametrize("blksize", [None, "1428"])
def test_curl_uploads_to_server(root, make_server, tmp_path, blksize):
    server = make_server(root, writable=True)
    extra = ["--tftp-blksize", blksize] if blksize else []
    proc = curl(
        *extra, "-T", str(root / "big.bin"), "tftp://127.0.0.1:%d/curl-up.bin" % server.server_address[1]
    )
    assert proc.returncode == 0, proc.stderr
    assert (root / "curl-up.bin").read_bytes() == (root / "big.bin").read_bytes()


def test_curl_sees_errors(root, make_server, tmp_path):
    server = make_server(root)
    proc = curl("-o", str(tmp_path / "x"), "tftp://127.0.0.1:%d/missing.bin" % server.server_address[1])
    assert proc.returncode != 0
