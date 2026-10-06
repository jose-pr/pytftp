from __future__ import annotations

import http.server
import os
import random
import socket
import threading
from typing import Callable, List, Optional

import pytest

import tftp
import tftp.relay
from tftp.options import Negotiated
from tftp.packet import decode

# The options a server allows to serve the x-list directory listing.
LISTING = tftp.TFTPServerOptions(allowed=tftp.options.STANDARD_OPTIONS | tftp.options.LISTING_OPTIONS)


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


#: Example addresses that must not reach the network.
PLACEHOLDERS = ("example.net", "192.0.2.", "10.0.0.20", "/srv/tftp", "port=69")


@pytest.fixture
def served(tmp_path, monkeypatch):
    """A writable server on a free loopback port, and every server the blocks start."""
    root = tmp_path / "srv"
    for directory in ("images", "pxelinux.cfg", "logs"):
        (root / directory).mkdir(parents=True)
    payload = bytes(range(256)) * 50
    for name in ("images/vmlinuz", "vmlinuz", "pxelinux.0"):
        (root / name).write_bytes(payload)
    (root / "pxelinux.cfg" / "default").write_bytes(b"DEFAULT linux\n")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    created = []

    def remember(cls):
        original = cls.__init__

        def init(self, *args, **kwargs):
            kwargs.setdefault("host", "127.0.0.1")
            kwargs.setdefault("port", 0)
            original(self, *args, **kwargs)
            created.append(self)

        monkeypatch.setattr(cls, "__init__", init)

    remember(tftp.TFTPServer)
    remember(tftp.relay.TFTPRelay)
    server = tftp.TFTPServer(root, writable=True, timeout=0.5).start()
    yield server, root, tmp_path
    for item in created:
        item.close()


def substitute(code, server, root):
    port = str(server.server_address[1])
    pairs = [
        ('"boot.example.net"', '"127.0.0.1", ' + port),
        ("boot.example.net", "127.0.0.1:" + port),
        ('"192.0.2.1"', '"127.0.0.1", ' + port),
        ("tftp://192.0.2.1/", "tftp://127.0.0.1/"),
        ('"/srv/tftp"', repr(str(root))),
        ('host="::"', 'host="127.0.0.1"'),
        ("port=69", "port=0"),
        ('default="10.0.0.20"', 'default="127.0.0.1:%s"' % port),
        ('"10.0.0.20"', '"127.0.0.1"'),
        ('"boot.pcapng"', '"relay.pcap"'),
        (".serve_forever()", ".start()"),
    ]
    for old, new in pairs:
        code = code.replace(old, new)
    return code


class HttpStore(http.server.BaseHTTPRequestHandler):
    """A web server over a dictionary of ``path -> body``: GET, and PUT (plain or chunked)."""

    store: dict = {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        body = self.store.get(self.path)
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self):
        if self.headers.get("Transfer-Encoding") == "chunked":
            data = bytearray()
            while True:
                size = int(self.rfile.readline().strip(), 16)
                if not size:
                    self.rfile.readline()
                    break
                data += self.rfile.read(size)
                self.rfile.readline()
        else:
            data = self.rfile.read(int(self.headers["Content-Length"]))
        self.store[self.path] = bytes(data)
        self.send_response(201)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture
def web():
    HttpStore.store = {"/images/kernel": os.urandom(200_000), "/images/a%20b": b"spaced"}
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), HttpStore)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    yield "http://127.0.0.1:%d" % httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


# -- a path that loses, repeats, delays and reorders datagrams, by a seeded schedule -----------


class LossyPath:
    """A UDP forwarder between a real client and a real server on loopback.

    The client talks to ``address``; every datagram is forwarded to the server
    (and back) except where the schedule intervenes. A fault is decided by the
    datagram's direction, opcode and block number and applies to its first
    occurrence only, so a retransmission always gets through and a transfer's
    time is bounded. ``seed`` fixes which of a transfer's ``blocks`` suffer
    which fault: ``drop``, ``dup`` or ``delay`` (a delay shorter than the
    transfer's timeout reorders the datagram behind its successors);
    ``handshake`` loses one datagram of the start once: the ``"request"``, the
    first ``"oack"`` or the first ``"ack0"``. ``counts`` holds the datagrams
    seen, by direction and opcode, as they came off the wire.

    ``inject`` sends from a third socket to the client; what that socket
    receives is kept in ``stray_replies``.
    """

    FAULTS = ("drop", "dup", "delay")

    def __init__(
        self, server_address, *, seed, blocks, faults=6, delay=0.15, handshake=None, data_to="client"
    ):
        import heapq
        import random
        import threading

        self.seed = seed
        self.server_address = server_address
        self.counts = {}
        self.stray_replies = []
        self.faults = {}
        rng = random.Random(seed)
        other = "server" if data_to == "client" else "client"
        for _ in range(faults):
            opcode = rng.choice((3, 4))
            toward = data_to if opcode == 3 else other
            kind = rng.choice(self.FAULTS)
            self.faults[(toward, opcode, rng.randint(1, blocks))] = (kind, delay * rng.uniform(0.5, 1.5))
        self.handshake = handshake
        self._seen = set()
        self._heap = []
        self._heapq = heapq
        self._order = 0
        self._client = None
        self._tid = None
        self._injections = []
        self._stop = threading.Event()
        self.listen = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.listen.bind(("127.0.0.1", 0))
        self.upstream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.upstream.bind(("127.0.0.1", 0))
        self.stray = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.stray.bind(("127.0.0.1", 0))
        self.address = self.listen.getsockname()
        self._thread = threading.Thread(target=self._run, name="lossy-path", daemon=True)
        self._thread.start()

    def describe(self) -> str:
        """What a failure message says, so the run can be repeated."""
        return "lossy path seed=%r faults=%r handshake=%r" % (
            self.seed,
            sorted(self.faults.items()),
            self.handshake,
        )

    def inject(self, data: bytes, *, after_datagrams: int = 3) -> None:
        """Send ``data`` to the client from the third socket once ``after_datagrams`` have gone through."""
        self._injections.append([after_datagrams, data])

    def _decide(self, toward, data):
        opcode = data[1] if len(data) >= 2 else -1
        self.counts[(toward, opcode)] = self.counts.get((toward, opcode), 0) + 1
        first = {1: "request", 2: "request", 6: "oack"}.get(opcode)
        if opcode == 4 and toward == "server" and data[2:4] == bytes(2):
            first = "ack0"
        if first is not None and first == self.handshake and first not in self._seen:
            self._seen.add(first)
            return "drop", 0.0
        block = int.from_bytes(data[2:4], "big") if opcode in (3, 4) and len(data) >= 4 else None
        key = (toward, opcode, block)
        if key in self.faults and key not in self._seen:
            self._seen.add(key)
            return self.faults[key]
        return "pass", 0.0

    def _forward(self, sock, data, target, toward):
        import time

        action, delay = self._decide(toward, data)
        if action == "drop":
            return False
        for _ in range(2 if action == "dup" else 1):
            if action == "delay":
                self._order += 1
                self._heapq.heappush(self._heap, (time.monotonic() + delay, self._order, sock, data, target))
            else:
                sock.sendto(data, target)
        return True

    def _run(self) -> None:
        import select
        import time

        socks = [self.listen, self.upstream, self.stray]
        while not self._stop.is_set():
            wait = 0.05
            if self._heap:
                wait = max(0.0, min(wait, self._heap[0][0] - time.monotonic()))
            ready, _, _ = select.select(socks, [], [], wait)
            while self._heap and self._heap[0][0] <= time.monotonic():
                _, _, sock, data, target = self._heapq.heappop(self._heap)
                sock.sendto(data, target)
            for sock in ready:
                try:
                    data, address = sock.recvfrom(70000)
                except OSError:
                    continue
                if sock is self.listen:
                    self._client = address
                    # a request always goes to the well-known port, as a real client sends it
                    request = len(data) >= 2 and data[1] in (1, 2)
                    target = self.server_address if request else self._tid or self.server_address
                    self._forward(self.upstream, data, target, "server")
                elif sock is self.upstream:
                    # the client learns the server's transfer address from the first answer it receives
                    if self._forward(self.listen, data, self._client, "client"):
                        self._tid = address
                else:
                    self.stray_replies.append(data)
                for injection in list(self._injections):
                    injection[0] -= 1
                    if injection[0] <= 0 and self._client is not None:
                        self._injections.remove(injection)
                        self.stray.sendto(injection[1], self._client)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(5)
        for sock in (self.listen, self.upstream, self.stray):
            sock.close()


@pytest.fixture
def lossy_path():
    """``lossy_path(server, **schedule)``: a :class:`LossyPath` to ``server``, closed after the test."""
    made = []

    def make(server, **kwargs) -> LossyPath:
        address = server.server_address
        host = address[0] if address[0] not in ("0.0.0.0", "::") else "127.0.0.1"
        made.append(LossyPath((host, address[1]), **kwargs))
        return made[-1]

    yield make
    for path in made:
        path.close()


def _windows_loop_factories():
    import asyncio

    if os.name == "nt":
        return [asyncio.SelectorEventLoop, asyncio.ProactorEventLoop]
    return [None]


@pytest.fixture(params=_windows_loop_factories(), ids=lambda f: getattr(f, "__name__", "default"))
def loop_factory(request):
    """The event loop class an asyncio test runs on: both of Windows' loops there, the default elsewhere."""
    return request.param


def run_async(coro, loop_factory=None, timeout=60):
    """Run ``coro`` to completion on ``loop_factory``'s loop, bounded by ``timeout`` seconds."""
    import asyncio
    import sys

    if loop_factory is None:
        return asyncio.run(asyncio.wait_for(coro, timeout))
    if sys.version_info >= (3, 12):
        return asyncio.run(asyncio.wait_for(coro, timeout), loop_factory=loop_factory)
    loop = loop_factory()
    try:
        return loop.run_until_complete(asyncio.wait_for(coro, timeout))
    finally:
        loop.close()


# -- no test resolves a name off the host ----------------------------------------------------


def _is_local_name(host) -> bool:
    import ipaddress

    if host is None or host in ("", b""):
        return True
    if isinstance(host, (bytes, bytearray)):
        host = bytes(host).decode("ascii", "replace")
    if host.lower().rstrip(".") in ("localhost", "localhost.localdomain", "ip6-localhost"):
        return True
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    return True


@pytest.fixture(autouse=True)
def no_name_leaves_the_host(monkeypatch, request):
    """Fail a test that asks the system resolver for a name that is not an address or ``localhost``.

    A name under ``.invalid`` is answered "not known" without asking anyone.

    The violation is recorded as well as raised, so a library that swallows
    the error still fails the test.
    """
    violations = []

    def guard(real):
        def guarded(host, *args, **kwargs):
            if isinstance(host, str) and host.lower().rstrip(".").endswith(".invalid"):
                # RFC 6761: a name under .invalid never resolves, so it is answered here, not asked off the host.
                raise socket.gaierror(socket.EAI_NONAME, "name not known (RFC 6761 .invalid)")
            if not _is_local_name(host):
                violations.append(host)
                raise AssertionError("a test resolved %r through the system resolver" % (host,))
            return real(host, *args, **kwargs)

        return guarded

    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        monkeypatch.setattr(socket, name, guard(getattr(socket, name)))
    yield
    if request.node.get_closest_marker("resolves_off_host"):  # the test of this guard
        return
    assert violations == [], "names resolved off the host: %r" % violations


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


class Link:
    """Two queues between a sender and a receiver, with a loss rule.

    ``rule(direction, packet, count)`` returns how many copies to deliver
    (0 = lost, 2 = duplicated); ``count`` numbers packets per direction.
    """

    def __init__(self, rule: Optional[Callable[[str, bytes, int], int]] = None) -> None:
        self.rule = rule or (lambda *_: 1)
        self.to_receiver: List[bytes] = []
        self.to_sender: List[bytes] = []
        self.sent = {"s": [], "r": []}

    def from_sender(self, packet) -> None:
        packet = bytes(packet)
        self.sent["s"].append(packet)
        for _ in range(self.rule("s", packet, len(self.sent["s"]) - 1)):
            self.to_receiver.append(packet)

    def from_receiver(self, packet) -> None:
        packet = bytes(packet)
        self.sent["r"].append(packet)
        for _ in range(self.rule("r", packet, len(self.sent["r"]) - 1)):
            self.to_sender.append(packet)


def neg(**kwargs) -> Negotiated:
    kwargs.setdefault("timeout", 1.0)
    return Negotiated(**kwargs)


def raw_socket(timeout: float = 2.0) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(timeout)
    return sock


class FakeServer:
    """Answers requests with a script: ``script(request) -> list of packets``."""

    def __init__(self, script):
        self.sock = raw_socket(3.0)
        self.address = self.sock.getsockname()
        self.requests = []
        self.script = script
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        try:
            while True:
                data, peer = self.sock.recvfrom(70000)
                packet = decode(data)
                self.requests.append(packet)
                for reply in self.script(packet):
                    self.sock.sendto(reply, peer)
        except OSError:
            pass

    def close(self):
        self.sock.close()


@pytest.fixture
def fake_server():
    """Start servers that answer from a script; every one is closed after the test."""
    servers = []

    def make(script):
        servers.append(FakeServer(script))
        return servers[-1]

    yield make
    for fake in servers:
        fake.close()


def free_ports(count: int) -> range:
    """``count`` consecutive loopback ports that were free a moment ago, chosen at random.

    The ports are not held: the server under test binds them. The block lies below every
    system's ephemeral range, because those hand out the next port to the next bind, which
    would be the server's own socket. A random start keeps two suites on one host apart.
    """
    for _ in range(200):
        start = random.randrange(20000, 30000)
        held = []
        try:
            for port in range(start, start + count):
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                held.append(sock)
                sock.bind(("127.0.0.1", port))
        except OSError:
            continue
        else:
            return range(start, start + count)
        finally:
            for sock in held:
                sock.close()
    pytest.skip("no block of %d consecutive free ports" % count)
