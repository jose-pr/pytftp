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


class FakePeer:
    """A UDP server that answers by script, for what a real server never sends.

    ``script(data)`` gets each datagram received and returns the datagrams to
    send back, each ``bytes`` or ``(seconds_to_wait, bytes)``. ``seen`` holds
    ``(seconds since the first datagram, bytes)`` for everything received, as
    it came off the wire. The thread ends when the socket is closed.
    """

    def __init__(self, script=None) -> None:
        import threading
        import time

        self.script = script or (lambda data: [])
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self._closed = False
        self.port = self.sock.getsockname()[1]
        self.seen: list = []
        self._clock = time.monotonic
        self._thread = threading.Thread(target=self._run, name="fake-peer", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        import time

        began = None
        try:
            while True:
                try:
                    data, peer = self.sock.recvfrom(70000)
                except socket.timeout:
                    if self._closed:
                        return
                    continue
                began = self._clock() if began is None else began
                self.seen.append((self._clock() - began, data))
                for reply in self.script(data):
                    wait, packet = reply if isinstance(reply, tuple) else (0, reply)
                    if wait:
                        time.sleep(wait)
                    self.sock.sendto(packet, peer)
        except OSError:
            pass

    def close(self) -> None:
        self._closed = True
        self._thread.join(5)
        self.sock.close()

    def __enter__(self) -> "FakePeer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


#: ``(id, request is a read, datagram)``: first answers a server may not send.
BAD_FIRST_ANSWERS = [
    ("rrq-data-block-9", True, b"\x00\x03\x00\x09tail"),
    ("rrq-data-cut-to-2", True, b"\x00\x03"),
    ("rrq-data-cut-to-3", True, b"\x00\x03\x00"),
    ("rrq-ack", True, b"\x00\x04\x00\x00"),
    ("rrq-error-cut-to-2", True, b"\x00\x05"),
    ("wrq-ack-cut-to-2", False, b"\x00\x04"),
    ("wrq-ack-cut-to-3", False, b"\x00\x04\x00"),
    ("wrq-ack-block-7", False, b"\x00\x04\x00\x07"),
    ("wrq-data-1", False, b"\x00\x03\x00\x01x"),
]


class _RefusingSocket(socket.socket):
    """A UDP socket whose ``sendto`` fails as a host does for a datagram it cannot send.

    Datagrams longer than ``limit`` octets fail with ``OSError(EMSGSIZE)``
    once ``allowed`` of them have gone out. ``sent`` holds the length of every
    datagram that did.
    """

    limit = 0
    allowed = 0

    def sendto(self, data, *address):
        import errno

        if len(data) > self.limit:
            if self.allowed <= 0:
                raise OSError(errno.EMSGSIZE, "refused by the stand-in")
            self.allowed -= 1
        self.sent.append(len(data))
        return super().sendto(data, *address)


def refusing(sock: socket.socket, *, limit: int = 100, allowed: int = 0) -> socket.socket:
    """``sock`` (consumed) as a socket that refuses datagrams over ``limit`` octets after ``allowed`` big ones."""
    timeout = sock.gettimeout()
    fake = _RefusingSocket(sock.family, sock.type, sock.proto, fileno=sock.detach())
    fake.settimeout(timeout)
    fake.limit, fake.allowed, fake.sent = limit, allowed, []
    return fake


@pytest.fixture
def symlink():
    """``symlink(link, target)``: create one, or skip where the host does not let a test."""

    def make(link, target) -> None:
        try:
            os.symlink(target, link, target_is_directory=os.path.isdir(target))
        except (OSError, NotImplementedError) as exc:
            pytest.skip("this host does not let a test create a symbolic link: %s" % exc)

    return make


class RivalPair:
    """A server socket and a second one on another host address that answers first.

    The first request the server socket receives is answered with DATA block 1
    from the rival (``127.0.0.2``) and then from the real server
    (``127.0.0.1``), each with its own payload, which is shorter than a block:
    the transfer is over for whoever the client takes the answer from.
    """

    def __init__(self, rival: bytes = b"rival", real: bytes = b"real") -> None:
        import threading

        self.real = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rival = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.real.bind(("127.0.0.1", 0))
            self.rival.bind(("127.0.0.2", 0))
        except OSError as exc:
            self.close()
            pytest.skip("no second loopback address to answer from: %s" % exc)
        self.real.settimeout(10)
        self.port = self.real.getsockname()[1]
        self._payloads = (rival, real)
        self._thread = threading.Thread(target=self._run, name="rival-pair", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            _, client = self.real.recvfrom(2048)
            self.rival.sendto(b"\x00\x03\x00\x01" + self._payloads[0], client)
            self.real.sendto(b"\x00\x03\x00\x01" + self._payloads[1], client)
        except OSError:
            pass

    def close(self) -> None:
        self.real.close()
        self.rival.close()
        if hasattr(self, "_thread"):
            self._thread.join(5)


@pytest.fixture
def rivals():
    """``rivals()`` makes a :class:`RivalPair`; each one is closed after the test."""
    made = []

    def make(**kwargs) -> RivalPair:
        made.append(RivalPair(**kwargs))
        return made[-1]

    yield make
    for pair in made:
        pair.close()


def wait_until(predicate, timeout: float = 5.0) -> bool:
    """Poll ``predicate`` until it is true or ``timeout`` seconds pass; its last answer.

    A state a server reaches after it has answered (a counter, a released
    transfer) is waited for here, with the assertion on that same state.
    """
    import time

    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        time.sleep(0.01)
    return predicate()
