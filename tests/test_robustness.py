"""Robustness: duplicate OACKs, backoff, limits, stalls, broadcast, fuzzing."""

from __future__ import annotations

import io
import os
import random
import socket

import pytest

import tftp
from conftest import client_for
from test_engine import Link, neg
from tftp import ErrorCode, Opcode, decode, encode_ack, encode_data, encode_error, encode_oack, encode_request
from tftp.exceptions import WouldBlock
from tftp.transfer import as_readinto, as_write

# -- duplicate OACK --------------------------------------------------------


def test_receiver_reacks_a_repeated_oack():
    link = Link()
    receiver = tftp.Receiver(link.from_receiver, as_write(io.BytesIO()), neg(), 3, 0.0, reply=encode_ack(0))
    oack = encode_oack({"blksize": "512"})
    receiver.handle(memoryview(oack), len(oack), 0.1)
    assert not receiver.done
    assert link.sent["r"] == [encode_ack(0), encode_ack(0)]


def test_sender_ignores_a_repeated_oack():
    link = Link()
    sender = tftp.Sender(link.from_sender, as_readinto(io.BytesIO(b"x" * 600)), neg(), 3, 0.0)
    oack = encode_oack({"blksize": "512"})
    sender.handle(memoryview(oack), len(oack), 0.1)
    assert not sender.done and len(link.sent["s"]) == 1


def test_client_download_survives_lost_ack0(root, make_server):
    """The server repeats its OACK when our ACK 0 is lost; the client re-ACKs."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as fake:
        fake.bind(("127.0.0.1", 0))
        fake.settimeout(3)
        import threading

        def serve():
            request, client = fake.recvfrom(2048)
            oack = encode_oack({"blksize": "600"})
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tid:
                tid.bind(("127.0.0.1", 0))
                tid.settimeout(3)
                tid.sendto(oack, client)
                tid.recvfrom(100)  # ACK 0 -- "lost": answer with the OACK again
                tid.sendto(oack, client)
                assert decode(tid.recvfrom(100)[0]) == tftp.Ack(0)
                tid.sendto(encode_data(1, b"done"), client)
                assert decode(tid.recvfrom(100)[0]) == tftp.Ack(1)

        thread = threading.Thread(target=serve)
        thread.start()
        client = tftp.Client("127.0.0.1", fake.getsockname()[1], blksize=600, timeout=2)
        assert client.get("f") == b"done"
        thread.join(5)


# -- backoff and time limits ------------------------------------------------


def test_exponential_backoff_schedule():
    link = Link()
    receiver = tftp.Receiver(
        link.from_receiver, as_write(io.BytesIO()), neg(timeout=1.0), 10, 0.0, reply=encode_ack(0)
    )
    now, waits = 0.0, []
    for _ in range(6):
        waits.append(receiver.deadline - now)
        now = receiver.deadline
        receiver.on_timeout(now)
    assert waits == [1, 2, 4, 8, 8, 8]  # capped at 8 x timeout by default
    data = encode_data(1, b"x" * 512)
    receiver.handle(memoryview(data), len(data), now)
    assert receiver.deadline - now == 1  # progress resets the wait


def test_backoff_disabled_and_custom_cap():
    link = Link()
    receiver = tftp.Receiver(
        link.from_receiver,
        as_write(io.BytesIO()),
        neg(timeout=1.0),
        10,
        0.0,
        reply=encode_ack(0),
        backoff=1.0,
    )
    receiver.on_timeout(receiver.deadline)
    assert receiver.deadline == 2.0
    sender = tftp.Sender(
        Link().from_sender, as_readinto(io.BytesIO(b"x" * 100)), neg(timeout=1.0), 10, 0.0, max_timeout=3
    )
    for _ in range(4):
        sender.on_timeout(sender.deadline)
    assert sender.deadline - 0 == pytest.approx(1 + 2 + 3 + 3 + 3)


def test_expiry_ends_a_transfer():
    link = Link()
    receiver = tftp.Receiver(
        link.from_receiver,
        as_write(io.BytesIO()),
        neg(timeout=1.0),
        100,
        0.0,
        reply=encode_ack(0),
        expires=2.5,
    )
    while not receiver.done:
        receiver.on_timeout(receiver.deadline)
    assert isinstance(receiver.error, tftp.TransferTimeoutError)
    assert "time limit" in receiver.error.message


def test_client_max_duration(root):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as silent:
        silent.bind(("127.0.0.1", 0))
        client = tftp.Client(
            "127.0.0.1", silent.getsockname()[1], timeout=0.05, retries=100, max_duration=0.3
        )
        with pytest.raises(tftp.TransferTimeoutError):
            client.get("x")


# -- stalls (WouldBlock) -----------------------------------------------------


class Trickle(io.RawIOBase):
    """A source that has nothing ready every other read."""

    def __init__(self, data: bytes) -> None:
        self.data = io.BytesIO(data)
        self.ready = False

    def readable(self):
        return True

    def readinto(self, buffer):
        self.ready = not self.ready
        if not self.ready:
            raise WouldBlock()
        return self.data.readinto(buffer)


def test_sender_pauses_and_resumes():
    data = os.urandom(512 * 5 + 7)
    sink = io.BytesIO()
    link = Link()
    receiver = tftp.Receiver(link.from_receiver, as_write(sink), neg(windowsize=2), 3, 0.0)
    sender = tftp.Sender(link.from_sender, as_readinto(Trickle(data)), neg(windowsize=2), 3, 0.0)
    for _ in range(1000):
        if sender.done and receiver.done:
            break
        if sender.stalled:
            if sender._hi < sender._base:
                assert sender.deadline is None  # nothing outstanding: no peer timeout
            sender.resume(0.0)
        while link.to_receiver:
            packet = link.to_receiver.pop(0)
            receiver.handle(memoryview(packet), len(packet), 0.0)
        while link.to_sender:
            packet = link.to_sender.pop(0)
            sender.handle(memoryview(packet), len(packet), 0.0)
    assert sink.getvalue() == data
    assert sender.retransmits == 0


def test_receiver_holds_block_while_sink_is_full():
    class Valve:
        _tftp_copies_ = True

        def __init__(self):
            self.open = False
            self.out = bytearray()

        def write(self, data):
            if not self.open:
                raise WouldBlock()
            self.out += data

    valve = Valve()
    link = Link()
    receiver = tftp.Receiver(link.from_receiver, as_write(valve), neg(), 3, 0.0)
    block = encode_data(1, b"a" * 512)
    receiver.handle(memoryview(block), len(block), 0.0)
    assert receiver.stalled and link.sent["r"] == []  # no ACK: backpressure
    receiver.handle(memoryview(block), len(block), 0.5)  # resend while stalled
    assert link.sent["r"] == []
    valve.open = True
    receiver.resume(1.0)
    assert link.sent["r"] == [encode_ack(1)] and bytes(valve.out) == b"a" * 512


def test_server_resumes_a_stalled_handler_from_another_thread(make_server):
    import threading
    import time

    class Slow:
        """A reader filled by another thread, waking the server when it has data."""

        def __init__(self):
            self.buffer = bytearray()
            self.eof = False
            self.lock = threading.Lock()
            self.wakeup = None

        def set_wakeup(self, callback):
            self.wakeup = callback

        def feed(self, data, eof=False):
            with self.lock:
                self.buffer += data
                self.eof = eof
            self.wakeup()

        def readinto(self, view):
            with self.lock:
                if len(self.buffer) < len(view) and not self.eof:
                    raise WouldBlock()
                n = min(len(view), len(self.buffer))
                view[:n] = self.buffer[:n]
                del self.buffer[:n]
                return n

        def close(self):
            pass

    stream = Slow()

    class Handler:
        def open_read(self, context):
            return stream

    server = make_server(Handler())
    payload = os.urandom(3000)

    def producer():
        for i in range(0, len(payload), 700):
            time.sleep(0.05)
            stream.feed(payload[i : i + 700])
        stream.feed(b"", eof=True)

    threading.Thread(target=producer, daemon=True).start()
    assert client_for(server, tsize=False, timeout=1.0, retries=5).get("slow") == payload


# -- typed errors -------------------------------------------------------------


@pytest.mark.parametrize(
    "code,cls",
    [
        (1, tftp.FileNotFound),
        (2, tftp.AccessViolation),
        (3, tftp.DiskFull),
        (4, tftp.IllegalOperation),
        (5, tftp.UnknownTransferID),
        (6, tftp.FileAlreadyExists),
        (7, tftp.NoSuchUser),
        (8, tftp.OptionNegotiationError),
        (0, tftp.RemoteError),
        (99, tftp.RemoteError),
    ],
)
def test_remote_errors_are_typed(code, cls):
    error = tftp.RemoteError.from_code(code, "msg")
    assert type(error) is cls and error.code == code and error.message == "msg"
    assert isinstance(error, tftp.RemoteError)


def test_client_raises_typed_errors(root, make_server):
    server = make_server(root)
    with pytest.raises(tftp.FileNotFound):
        client_for(server).get("missing")
    with pytest.raises(tftp.AccessViolation):
        client_for(server).put("x", b"y")


def test_shutdown_aborts_running_transfers(root, make_server):
    results = []
    server = make_server(root, on_complete=results.append, timeout=5)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as raw:
        raw.settimeout(2)
        raw.sendto(encode_request(Opcode.RRQ, "big.bin"), server.server_address)
        raw.recvfrom(2048)
        server.stop()
        error = decode(raw.recvfrom(2048)[0])
        assert error.code == ErrorCode.NOT_DEFINED and "shutting down" in error.message


# -- limits and broadcast --------------------------------------------------


@pytest.mark.parametrize(
    "request_bytes,fragment",
    [
        (encode_request(Opcode.RRQ, "a" * 600), "filename"),
        (encode_request(Opcode.RRQ, "f", "octet", {"opt%d" % i: 1 for i in range(20)}), "options"),
        (encode_request(Opcode.RRQ, "f", "octet", {"blksize": "1" * 300}), "option"),
        (encode_request(Opcode.RRQ, "f" * 1100), "too large"),
    ],
)
def test_request_limits(root, make_server, request_bytes, fragment):
    server = make_server(root)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as raw:
        raw.settimeout(2)
        raw.sendto(request_bytes, server.server_address)
        error = decode(raw.recvfrom(2048)[0])
    assert error.code == ErrorCode.ILLEGAL_OPERATION and fragment in error.message


def test_sessions_per_client(root, make_server):
    server = make_server(root, limits=tftp.ServerLimits(max_sessions_per_client=1), timeout=3)
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as a,
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as b,
    ):
        for raw in (a, b):
            raw.settimeout(2)
            raw.bind(("127.0.0.1", 0))
        a.sendto(encode_request(Opcode.RRQ, "big.bin"), server.server_address)
        a.recvfrom(2048)
        b.sendto(encode_request(Opcode.RRQ, "big.bin"), server.server_address)
        error = decode(b.recvfrom(2048)[0])
        assert error.code == ErrorCode.NOT_DEFINED and "busy" in error.message


def _arrival(local, interface=None):
    import ipaddress
    from types import SimpleNamespace

    from tftp.server.listener import Arrival

    datagram = SimpleNamespace(destination=None if local is None else ipaddress.ip_address(local))
    return Arrival(b"", ("", 0), local, 0, datagram, interface)


def test_broadcast_detection():
    from tftp.server.listener import Listener

    for address in ("255.255.255.255", "224.0.0.1", "ff02::1", "::ffff:255.255.255.255", "::ffff:224.0.0.1"):
        assert Listener.is_broadcast(_arrival(address)), address
    for address in ("127.0.0.1", "::1", None):
        assert not Listener.is_broadcast(_arrival(address)), address


def test_subnet_broadcast_is_detected_from_the_arrival_interface():
    import netimps

    from tftp.server.listener import Listener

    for iface in netimps.get_interfaces():
        for address in iface.ipv4:
            if address.network.prefixlen < 31:
                assert Listener.is_broadcast(_arrival(str(address.network.broadcast_address), iface))
                assert not Listener.is_broadcast(_arrival(str(address.ip), iface))
                return
    pytest.skip("no IPv4 interface with a broadcast address")


# -- fuzzing -------------------------------------------------------------------


def _random_packets(seed: int, count: int):
    rng = random.Random(seed)
    templates = [
        encode_request(Opcode.RRQ, "file", "octet", {"blksize": 1024, "tsize": 0}),
        encode_request(Opcode.WRQ, "file"),
        encode_data(1, b"abc"),
        encode_ack(3),
        tftp.encode_error(1, "x"),
        encode_oack({"blksize": 9}),
    ]
    for _ in range(count):
        kind = rng.random()
        if kind < 0.4:
            yield bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 64)))
        else:
            packet = bytearray(rng.choice(templates))
            for _ in range(rng.randint(1, 4)):
                if packet and rng.random() < 0.5:
                    packet[rng.randrange(len(packet))] = rng.getrandbits(8)
                elif packet:
                    del packet[rng.randrange(len(packet)) :]
                else:
                    packet += bytes([rng.getrandbits(8)])
            yield bytes(packet)


def test_decode_fuzz_never_raises_anything_but_malformed():
    for packet in _random_packets(1, 20_000):
        try:
            decode(packet)
        except tftp.TFTPDecodeError:
            pass


def test_engine_fuzz_never_raises():
    for seed in range(5):
        link = Link()
        receiver = tftp.Receiver(link.from_receiver, as_write(io.BytesIO()), neg(windowsize=4), 3, 0.0)
        sender = tftp.Sender(
            link.from_sender, as_readinto(io.BytesIO(os.urandom(5000))), neg(windowsize=4), 3, 0.0
        )
        for packet in _random_packets(seed, 2000):
            for side in (receiver, sender):
                side.handle(memoryview(packet), len(packet), 0.0)


def test_server_survives_fuzzing(root, make_server):
    server = make_server(root)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as raw:
        raw.bind(("127.0.0.1", 0))
        raw.settimeout(0.01)
        for packet in _random_packets(7, 400):
            raw.sendto(packet, server.server_address)
            try:
                while True:
                    _, tid = raw.recvfrom(70000)
                    if tid != server.server_address[:2]:
                        raw.sendto(next(_random_packets(9, 1)) or b"\x00", tid)
            except (socket.timeout, OSError):
                pass
    assert client_for(server, timeout=1).get("513.bin") == (root / "513.bin").read_bytes()


def test_unconnected_udp_survives_port_unreachable():
    """Positive control for disabling Windows' SIO_UDP_CONNRESET reports.

    Without it, Windows raises ConnectionResetError on the next receive after
    a datagram to a closed port; with it, the receive simply times out, as on
    every other platform.
    """
    from tftp.server.listener import Listener
    from tftp.server.session import bind_transfer

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as closed:
        closed.bind(("127.0.0.1", 0))
        dead = closed.getsockname()
    listener = Listener("127.0.0.1", 0)
    client = tftp.Client("127.0.0.1", 69)
    makers = {
        "client": lambda: client._socket(socket.AF_INET),
        "transfer": lambda: bind_transfer("127.0.0.1", socket.AF_INET),
        "listener": lambda: listener.sock,
    }
    try:
        for make in makers.values():
            sock = make()
            try:
                sock.settimeout(0.2)
                sock.sendto(b"x", dead)
                with pytest.raises(socket.timeout):
                    sock.recvfrom(100)
            finally:
                if sock is not listener.sock:
                    sock.close()
    finally:
        listener.close()


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows-only behaviour")
def test_plain_windows_socket_does_report_connreset():
    """The negative control: proves the test above can tell the difference."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as closed:
        closed.bind(("127.0.0.1", 0))
        dead = closed.getsockname()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.settimeout(0.2)
        sock.sendto(b"x", dead)
        with pytest.raises(ConnectionResetError):
            sock.recvfrom(100)


def test_interface_lookups_reuse_netimps_cache():
    """Per-request broadcast checks and per-transfer MTU lookups list adapters
    at most once per netimps cache period, not once per call."""
    import ipaddress

    import netimps

    from tftp.client import _mtu_blksize
    from tftp.server.listener import Listener

    netimps.clear_interface_cache()
    before = netimps.interface_enumerations()
    for _ in range(5):
        assert not Listener.is_broadcast(_arrival("10.9.9.9"))  # no interface: falls back to a listing
    assert netimps.interface_enumerations() - before == 1
    netimps.clear_interface_cache()
    before = netimps.interface_enumerations()
    blksizes = {_mtu_blksize(ipaddress.ip_address("127.0.0.1")) for _ in range(5)}
    assert netimps.interface_enumerations() - before <= 1 and len(blksizes) == 1


# -- what a transfer holds, and for how long -------------------------------------------------


def test_an_unanswered_oack_holds_no_window_memory():
    import gc
    import tracemalloc

    gc.collect()

    big = neg(blksize=65464, windowsize=64, timeout=255.0)
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        sender = tftp.Sender(
            lambda packet: None,
            as_readinto(io.BytesIO(b"x" * 5)),
            big,
            5,
            0.0,
            oack=encode_oack({"windowsize": 64}),
        )
        held = tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()
    assert held < 64 * 1024, "%d octets held for a request nothing has answered" % held
    assert sender.deadline is not None


def test_a_transfer_holds_the_blocks_it_has_read_not_the_window():
    import gc
    import tracemalloc

    gc.collect()

    big = neg(blksize=65464, windowsize=64)
    sent = []
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        tftp.Sender(sent.append, as_readinto(io.BytesIO(b"x" * 100)), big, 5, 0.0)
        held = tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()
    assert len(sent) == 1
    assert 65_000 < held < 200_000  # one block, not 64


def test_a_finished_transfer_is_not_pinned_by_its_stale_timer_entry(make_server):
    """The timer heap keeps an entry until its deadline; it must not keep the transfer."""
    import gc
    import time
    import weakref

    class Stream(io.BytesIO):
        pass

    streams = []

    class Handler:
        _tftp_fast_open_ = True

        def open_read(self, context):
            stream = Stream(b"x")
            streams.append(weakref.ref(stream))
            return stream

    server = make_server(Handler(), options=tftp.ServerOptions(max_windowsize=64))
    client = client_for(server, timeout=255, windowsize=64, blksize=1428)
    assert client.get("f") == b"x"
    deadline = time.monotonic() + 5
    while server.active_sessions and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.active_sessions == 0 and server._timers  # a stale entry is still queued
    while time.monotonic() < deadline:  # the loop thread may still be unwinding
        gc.collect()
        if streams[0]() is None:
            break
        time.sleep(0.01)
    assert streams[0]() is None


def test_the_default_bounds_are_finite():
    assert tftp.ServerLimits().max_idle == 60.0
    with pytest.raises(ValueError):
        tftp.ServerLimits(max_idle=0)
    assert tftp.ServerLimits(max_idle=None).max_idle is None
    server = tftp.Server(".", "127.0.0.1", 0)
    try:
        assert server.max_sessions == 500
    finally:
        server.close()


def test_a_silent_peer_ends_the_transfer_at_max_idle_whatever_timeout_it_negotiated():
    sent = []
    sender = tftp.Sender(
        sent.append,
        as_readinto(io.BytesIO(b"x" * 5000)),
        neg(timeout=255.0, blksize=512),
        5,
        100.0,
        oack=encode_oack({"timeout": 255}),
        max_idle=10.0,
    )
    assert sender.deadline == 110.0  # not 355
    sender.on_timeout(110.0)
    assert sender.done and isinstance(sender.error, tftp.TransferTimeoutError)
    assert "no datagram" in sender.error.message


def test_a_peer_that_keeps_sending_is_never_idle_however_long_the_transfer_takes():
    link = Link()
    sender = tftp.Sender(
        link.from_sender, as_readinto(io.BytesIO(b"x" * 512 * 40)), neg(timeout=255.0), 5, 0.0, max_idle=10.0
    )
    now = 0.0
    for block in range(1, 40):
        now += 8.0  # a slow peer: one ACK every 8 s, 312 s in all
        assert sender.deadline is not None and sender.deadline > now
        sender.handle(memoryview(encode_ack(block)), 4, now)
        assert not sender.done, "ended at %.0f s" % now
    sender.on_timeout(sender.deadline)  # then it goes quiet
    assert sender.done and isinstance(sender.error, tftp.TransferTimeoutError)


def test_the_receiver_ends_when_the_peer_goes_quiet_and_not_before():
    receiver = tftp.Receiver(
        lambda packet: None,
        as_write(io.BytesIO()),
        neg(timeout=255.0),
        5,
        0.0,
        reply=encode_ack(0),
        max_idle=10.0,
    )
    data = encode_data(1, b"x" * 512)
    receiver.handle(memoryview(data), len(data), 6.0)
    assert receiver.deadline == 16.0
    receiver.on_timeout(16.0)
    assert receiver.done and isinstance(receiver.error, tftp.TransferTimeoutError)


def test_time_spent_waiting_on_the_local_source_is_not_idle_time():
    class Slow:
        blocked = True
        reads = 0

        def readinto(self, view):
            if self.blocked:
                raise WouldBlock
            self.reads += 1
            if self.reads > 1:
                return 0  # the end of the source
            view[:3] = b"abc"
            return 3

    slow = Slow()
    sender = tftp.Sender(lambda packet: None, as_readinto(slow), neg(timeout=255.0), 5, 0.0, max_idle=10.0)
    assert sender.stalled
    slow.blocked = False
    sender.resume(500.0)  # the source took 500 s; the peer was never asked
    assert not sender.done
    assert sender.deadline == 510.0


def test_a_request_nothing_follows_up_is_released_after_max_idle(root, make_server):
    import time

    results = []
    server = make_server(root, limits=tftp.ServerLimits(max_idle=0.5), timeout=30, on_complete=results.append)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as raw:
        raw.bind(("127.0.0.1", 0))
        raw.settimeout(2)
        options = {"blksize": 8000, "windowsize": 8, "timeout": 255}
        raw.sendto(encode_request(Opcode.RRQ, "big.bin", options=options), server.server_address)
        assert decode(raw.recvfrom(70000)[0]).options["timeout"] == "255"
        deadline = time.monotonic() + 5
        while server.active_sessions and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.active_sessions == 0
    assert len(results) == 1 and isinstance(results[0].error, tftp.TransferTimeoutError)


def test_a_slow_client_is_not_cut_off_by_max_idle(root, make_server):
    """Each ACK is well inside max_idle of the last; the whole transfer outlasts it."""
    import time

    results = []
    # A second between an ACK and the bound: a stalled runner must not look idle.
    max_idle, pause = 1.5, 0.4
    server = make_server(
        root, limits=tftp.ServerLimits(max_idle=max_idle), timeout=30, on_complete=results.append
    )
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as raw:
        raw.bind(("127.0.0.1", 0))
        raw.settimeout(3)
        raw.sendto(encode_request(Opcode.RRQ, "big.bin", options={"blksize": 8192}), server.server_address)
        _, tid = raw.recvfrom(70000)
        raw.sendto(encode_ack(0), tid)
        started = time.monotonic()
        for block in range(1, 6):
            packet, _ = raw.recvfrom(70000)
            assert decode(packet).block == block
            time.sleep(pause)
            raw.sendto(encode_ack(block), tid)
        assert time.monotonic() - started > max_idle
        assert server.active_sessions == 1 and not results
        raw.sendto(encode_error(0, "done"), tid)
