from __future__ import annotations

import os
import socket

import pytest

import tftp


def _ipv6_loopback() -> bool:
    if not socket.has_ipv6:
        return False
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sock:
            sock.bind(("::1", 0))
        return True
    except OSError:
        return False


HAS_IPV6 = _ipv6_loopback()
needs_ipv6 = pytest.mark.skipif(not HAS_IPV6, reason="no IPv6 loopback on this host")


@pytest.fixture
def root(tmp_path):
    """A served directory with a few files of awkward sizes."""
    files = {
        "empty.bin": b"",
        "one.bin": b"x",
        "511.bin": os.urandom(511),
        "512.bin": os.urandom(512),
        "513.bin": os.urandom(513),
        "1428x3.bin": os.urandom(1428 * 3),
        "big.bin": os.urandom(300_001),
        "text.txt": b"line one\nline two\r\nbare\rcr\n",
    }
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "nested.bin").write_bytes(b"nested")
    return tmp_path


@pytest.fixture
def make_server():
    """Start servers on a free port; every one is closed after the test."""
    servers = []

    def make(root_or_handler, host="127.0.0.1", **kwargs):
        kwargs.setdefault("timeout", 0.5)
        server = tftp.TFTPServer(root_or_handler, host=host, port=0, **kwargs).start()
        servers.append(server)
        return server

    yield make
    for server in servers:
        server.close()


def client_for(server, host=None, **kwargs) -> tftp.TFTPClient:
    address = server.server_address
    if host is None:
        host = address[0]
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
    kwargs.setdefault("timeout", 0.5)
    kwargs.setdefault("retries", 3)
    return tftp.TFTPClient(host, address[1], **kwargs)


def _link_local():
    """``(address text, interface index)`` of a link-local IPv6 address of this host, or ``None``."""
    try:
        import netimps

        for iface in netimps.get_interfaces():
            for ip in iface.ips:
                address = getattr(ip, "ip", ip)
                if address.version == 6 and address.is_link_local and iface.index:
                    return str(address).split("%")[0], iface.index
    except Exception:
        return None
    return None


@pytest.fixture
def link_local():
    """A link-local IPv6 address of this host with its zone, ``"fe80::1%7"``; skips without one."""
    found = _link_local()
    if found is None:
        pytest.skip("no link-local IPv6 address on this host")
    return "%s%%%d" % found


class RequestSpy(tftp.FilesystemBackend):
    """A filesystem handler that keeps every request it is asked to open.

    ``requests`` holds the ``RequestPacket`` as the server decoded it from the
    wire, so a test judges what a client sent, not what it was told to send.
    """

    def __init__(self, root, **kwargs) -> None:
        super().__init__(root, **kwargs)
        self.requests: list = []

    def open_read(self, context):
        self.requests.append(context.request)
        return super().open_read(context)

    def open_write(self, context, size):
        self.requests.append(context.request)
        return super().open_write(context, size)


@pytest.fixture
def spy_server(root, make_server):
    """``(spy, base)``: a server over ``root`` through a ``RequestSpy``, and its ``tftp://`` base URL."""

    def make(**kwargs):
        writable = kwargs.pop("writable", True)
        spy = RequestSpy(root, writable=writable, overwrite=True)
        server = make_server(spy, **kwargs)
        return spy, "tftp://127.0.0.1:%d/" % server.server_address[1]

    return make
