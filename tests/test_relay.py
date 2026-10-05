"""The transparent relay, end to end over loopback."""

from __future__ import annotations

import io
import os
import socket
import threading
import time

import pytest

import tftp
from conftest import client_for, needs_ipv6
from tftp import TFTPErrorCode, TFTPOpcode, decode, encode_ack, encode_data, encode_error
from tftp.relay import TFTPRelay, RouteTable, by_prefix, by_subnet


@pytest.fixture
def make_relay():
    relays = []

    def make(route, host="127.0.0.1", **kwargs):
        relay = TFTPRelay(route, host, 0, **kwargs).start()
        relays.append(relay)
        return relay

    yield make
    for relay in relays:
        relay.close()


def upstream_of(server):
    return ("127.0.0.1", server.server_address[1])


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    return predicate()


def test_download_and_upload_through_the_relay(root, make_server, make_relay):
    server = make_server(root, writable=True)
    ends = []
    relay = make_relay(upstream_of(server), on_session_end=ends.append, linger=0.1)
    client = client_for(relay, windowsize=8)
    assert client.get("big.bin") == (root / "big.bin").read_bytes()
    data = os.urandom(20_000)
    client.put("via-relay.bin", data)
    assert (root / "via-relay.bin").read_bytes() == data
    assert wait_for(lambda: len(ends) == 2)
    down, up = ends
    assert (down.operation, down.reason, down.bytes_to_client) == ("read", "complete", 300_001)
    assert (up.operation, up.reason, up.bytes_from_client) == ("write", "complete", 20_000)
    assert relay.active_sessions == 0


def test_client_sees_the_relay_and_negotiates_with_the_upstream(root, make_server, make_relay):
    server = make_server(root, options=tftp.ServerOptions(max_blksize=1000))
    relay = make_relay(upstream_of(server))
    result = client_for(relay, blksize=4000).download("513.bin", io.BytesIO())
    assert result.peer[0] == "127.0.0.1" and result.peer[1] not in (
        relay.server_address[1],
        server.server_address[1],
    )
    assert result.negotiated.blksize == 1000  # the upstream's answer, passed through


class FakeUpstream:
    """Records requests exactly as received and runs a scripted reply."""

    def __init__(self, script):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(5)
        self.address = self.sock.getsockname()
        self.received = []
        self.script = script
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            self.script(self)
        except (OSError, AssertionError) as exc:  # pragma: no cover - surfaced by the test
            self.received.append(exc)

    def close(self):
        self.sock.close()


def test_unknown_options_pass_through_byte_for_byte(make_relay):
    request = b"\x00\x01Boot\\File\x00OcTeT\x00X-Vendor\x00abc\x00BLKSIZE\x00600\x00"

    def script(fake):
        data, peer = fake.sock.recvfrom(2048)
        fake.received.append(data)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tid:
            tid.bind(("127.0.0.1", 0))
            tid.settimeout(5)
            tid.sendto(b"\x00\x06X-Vendor\x00xyz\x00blksize\x00600\x00", peer)
            fake.received.append(tid.recvfrom(100)[0])  # ACK 0
            tid.sendto(encode_data(1, b"ok"), peer)
            fake.received.append(tid.recvfrom(100)[0])  # ACK 1

    fake = FakeUpstream(script)
    relay = make_relay(fake.address)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.settimeout(5)
        client.sendto(request, relay.server_address)
        oack, tid = client.recvfrom(2048)
        assert oack == b"\x00\x06X-Vendor\x00xyz\x00blksize\x00600\x00"  # unchanged
        client.sendto(encode_ack(0), tid)
        assert decode(client.recvfrom(2048)[0]) == tftp.DataPacket(1, b"ok")
        client.sendto(encode_ack(1), tid)
    fake.thread.join(5)
    assert fake.received[0] == request  # case, order, unknown option: untouched
    assert fake.received[1:] == [encode_ack(0), encode_ack(1)]
    fake.close()


def test_retransmitted_request_is_forwarded_until_answered(make_relay):
    def script(fake):
        first, peer1 = fake.sock.recvfrom(2048)
        second, peer2 = fake.sock.recvfrom(2048)  # the client's retry, relayed
        fake.received += [first, second, peer1 == peer2]
        fake.sock.sendto(encode_data(1, b"late"), peer2)
        fake.sock.recvfrom(100)

    fake = FakeUpstream(script)
    relay = make_relay(fake.address)
    assert tftp.TFTPClient("127.0.0.1", relay.server_address[1], timeout=0.3, backoff=1).get("f") == b"late"
    fake.thread.join(5)
    assert fake.received[0] == fake.received[1] and fake.received[2] is True
    fake.close()


def test_wrong_tids_get_error_5_on_both_legs(root, make_server, make_relay):
    server = make_server(root, timeout=2)
    relay = make_relay(upstream_of(server))
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client,
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stranger,
    ):
        for s in (client, stranger):
            s.bind(("127.0.0.1", 0))
            s.settimeout(3)
        client.sendto(tftp.encode_request(TFTPOpcode.RRQ, "1428x3.bin"), relay.server_address)
        _, tid = client.recvfrom(2048)
        stranger.sendto(encode_ack(1), tid)  # client-facing leg, wrong source
        error, _ = stranger.recvfrom(100)
        assert decode(error).code == TFTPErrorCode.UNKNOWN_TID
        client.sendto(encode_ack(1), tid)  # the real transfer carries on
        assert decode(client.recvfrom(2048)[0]).block == 2
        client.sendto(encode_error(0, "done"), tid)


def test_upstream_errors_pass_through(root, make_server, make_relay):
    server = make_server(root)
    ends = []
    relay = make_relay(upstream_of(server), on_session_end=ends.append, linger=0.1)
    with pytest.raises(tftp.FileNotFound):
        client_for(relay).get("missing")
    assert wait_for(lambda: ends)
    assert ends[0].reason == "error" and ends[0].error == (1, "file not found")


def test_idle_sessions_are_cleaned_up(root, make_server, make_relay):
    server = make_server(root, timeout=5)
    ends = []
    relay = make_relay(upstream_of(server), idle_timeout=0.3, on_session_end=ends.append)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.settimeout(3)
        client.sendto(tftp.encode_request(TFTPOpcode.RRQ, "big.bin"), relay.server_address)
        client.recvfrom(2048)  # then vanish
    assert wait_for(lambda: ends)
    assert ends[0].reason == "idle" and relay.active_sessions == 0


def test_routing(root, tmp_path_factory, make_server, make_relay):
    other = tmp_path_factory.mktemp("other")
    (other / "one.bin").write_bytes(b"other")
    a, b = make_server(root), make_server(other)
    route = RouteTable([by_prefix({"other/": upstream_of(b)}), by_subnet({"10.0.0.0/8": upstream_of(b)})])
    relay = make_relay(route)
    with pytest.raises(tftp.AccessViolation):
        client_for(relay).get("one.bin")  # no route and no default
    relay2 = make_relay(RouteTable([by_subnet({"127.0.0.0/8": upstream_of(a)})], default=upstream_of(b)))
    assert client_for(relay2).get("one.bin") == b"x"


def test_routing_by_prefix_picks_longest(root, tmp_path_factory, make_server, make_relay):
    other = tmp_path_factory.mktemp("other")
    (other / "sub").mkdir()
    (other / "sub" / "nested.bin").write_bytes(b"from-other")
    a, b = make_server(root), make_server(other)
    relay = make_relay(by_prefix({"": upstream_of(a), "sub/": upstream_of(b)}))
    assert client_for(relay).get("sub/nested.bin") == b"from-other"
    assert client_for(relay).get("one.bin") == b"x"


def test_concurrent_and_repeated_transfers(root, make_server, make_relay):
    server = make_server(root)
    relay = make_relay(upstream_of(server))
    expected = (root / "big.bin").read_bytes()
    errors = []

    def fetch():
        try:
            client = client_for(relay, windowsize=4)
            for _ in range(3):  # the same client, several transfers
                assert client.get("big.bin") == expected
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=fetch) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []


def test_trace_sees_both_legs(root, make_server, make_relay):
    server = make_server(root)
    events = []
    relay = make_relay(upstream_of(server), trace=events.append)
    client_for(relay).get("one.bin")
    assert wait_for(lambda: len(events) >= 8)
    legs = {(e.leg, e.direction, e.opcode_name) for e in events}
    assert ("client", "in", "RRQ") in legs and ("upstream", "out", "RRQ") in legs
    assert ("upstream", "in", "DATA") in legs and ("client", "out", "DATA") in legs
    assert len({e.session for e in events}) == 1
    assert all(e.role == "relay" for e in events)
    assert "RRQ 'one.bin' octet" in events[0].format()


@needs_ipv6
def test_ipv6_client_ipv4_upstream(root, make_server, make_relay):
    server = make_server(root)
    relay = make_relay(upstream_of(server), host="::1")
    assert client_for(relay, host="::1").get("513.bin") == (root / "513.bin").read_bytes()


def test_relay_stats(root, make_server, make_relay):
    server = make_server(root)
    relay = make_relay(upstream_of(server), linger=0.05)
    client_for(relay).get("513.bin")
    with pytest.raises(tftp.FileNotFound):
        client_for(relay).get("missing")
    assert wait_for(lambda: relay.stats["completed"] + relay.stats["failed"] == 2)
    snapshot = relay.stats_snapshot()
    assert (snapshot["requests"], snapshot["started"], snapshot["completed"], snapshot["failed"]) == (
        2,
        2,
        1,
        1,
    )
    assert snapshot["bytes_to_clients"] == 513 and snapshot["active"] == 0


# -- the relay's upstream named by a link-local address ------------------------------------------


def test_relay_hears_an_upstream_named_by_a_link_local_address_with_its_zone(link_local, make_relay):
    from tftp.backends import MemoryBackend

    with tftp.TFTPServer(
        MemoryBackend({"f": b"through the relay"}), "::", 0, timeout=0.5
    ).start() as upstream:
        relay = make_relay("[%s]:%d" % (link_local, upstream.server_address[1]))
        client = tftp.TFTPClient("127.0.0.1", relay.server_address[1], timeout=0.5, retries=2)
        assert client.get("f") == b"through the relay"
