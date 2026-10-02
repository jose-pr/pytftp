"""Protocol behaviour checked on the wire, with hand-driven raw sockets."""

from __future__ import annotations

import socket
import sys
import threading

import pytest

import tftp
from conftest import client_for
from tftp import ErrorCode, Opcode, decode, encode_ack, encode_data, encode_error, encode_oack, encode_request


def raw_socket(timeout: float = 2.0) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(timeout)
    return sock


def expect(sock: socket.socket):
    data, addr = sock.recvfrom(70000)
    return decode(data), addr


def test_unknown_tid_gets_error_5_and_transfer_survives(root, make_server):
    server = make_server(root)
    with raw_socket() as client, raw_socket() as stranger:
        client.sendto(encode_request(Opcode.RRQ, "513.bin"), server.server_address)
        packet, tid = expect(client)
        assert packet == tftp.Data(1, (root / "513.bin").read_bytes()[:512])
        assert tid[1] != server.server_address[1]  # a fresh transfer ID

        stranger.sendto(encode_ack(1), tid)
        error, source = expect(stranger)
        assert error.code == ErrorCode.UNKNOWN_TID and source == tid

        client.sendto(encode_ack(1), tid)
        packet, _ = expect(client)
        assert packet == tftp.Data(2, (root / "513.bin").read_bytes()[512:])
        client.sendto(encode_ack(2), tid)


def test_duplicate_request_does_not_start_a_second_transfer(root, make_server):
    server = make_server(root)
    with raw_socket() as client:
        request = encode_request(Opcode.RRQ, "one.bin")
        client.sendto(request, server.server_address)
        client.sendto(request, server.server_address)
        first, tid = expect(client)
        assert first == tftp.Data(1, b"x")
        client.sendto(encode_ack(1), tid)
        client.settimeout(0.3)
        with pytest.raises(socket.timeout):
            expect(client)  # neither a second DATA 1 nor anything else


def test_server_retransmits_on_timeout_and_ignores_duplicate_acks(root, make_server):
    server = make_server(root, timeout=0.2)
    data = (root / "1428x3.bin").read_bytes()
    with raw_socket() as client:
        client.sendto(encode_request(Opcode.RRQ, "1428x3.bin"), server.server_address)
        first, tid = expect(client)
        again, _ = expect(client)  # no ACK sent: the same block comes back
        assert first == again == tftp.Data(1, data[:512])
        client.sendto(encode_ack(1), tid)
        client.sendto(encode_ack(1), tid)  # duplicate: must not cause a resend
        second, _ = expect(client)
        assert second.block == 2
        client.settimeout(0.1)
        with pytest.raises(socket.timeout):
            expect(client)  # Sorcerer's Apprentice would have sent DATA 2 again
        client.sendto(encode_error(ErrorCode.NOT_DEFINED, "bye"), tid)


def test_oack_then_ack0_handshake(root, make_server):
    server = make_server(root)
    with raw_socket() as client:
        client.sendto(
            encode_request(Opcode.RRQ, "513.bin", "octet", {"blksize": 600, "tsize": 0, "bogus": 1}),
            server.server_address,
        )
        oack, tid = expect(client)
        assert oack == tftp.OptionAck({"blksize": "600", "tsize": "513"})
        client.sendto(encode_ack(0), tid)
        data, _ = expect(client)
        assert data == tftp.Data(1, (root / "513.bin").read_bytes())
        client.sendto(encode_ack(1), tid)


def test_client_refusing_the_oack_ends_the_transfer(root, make_server):
    results = []
    server = make_server(root, on_complete=results.append)
    with raw_socket() as client:
        client.sendto(encode_request(Opcode.RRQ, "513.bin", "octet", {"blksize": 600}), server.server_address)
        _, tid = expect(client)
        client.sendto(encode_error(ErrorCode.OPTION_REFUSED), tid)
        client.settimeout(0.3)
        with pytest.raises(socket.timeout):
            expect(client)


def test_write_dally_reacknowledges_last_block(root, make_server):
    server = make_server(root, writable=True)
    with raw_socket() as client:
        client.sendto(encode_request(Opcode.WRQ, "dally.bin"), server.server_address)
        ack0, tid = expect(client)
        assert ack0 == tftp.Ack(0)
        client.sendto(encode_data(1, b"short"), tid)
        assert expect(client)[0] == tftp.Ack(1)
        client.sendto(encode_data(1, b"short"), tid)  # as if our ACK was lost
        assert expect(client)[0] == tftp.Ack(1)
    assert (root / "dally.bin").read_bytes() == b"short"


@pytest.mark.parametrize(
    "request_bytes,code",
    [
        (encode_request(Opcode.RRQ, "one.bin", "mail"), ErrorCode.ILLEGAL_OPERATION),
        (encode_request(Opcode.RRQ, "one.bin", "weird"), ErrorCode.ILLEGAL_OPERATION),
        (b"\x00\x01\x00octet\x00", ErrorCode.ILLEGAL_OPERATION),
    ],
)
def test_bad_requests_are_answered(root, make_server, request_bytes, code):
    server = make_server(root)
    with raw_socket() as client:
        client.sendto(request_bytes, server.server_address)
        error, _ = expect(client)
        assert error.code == code


@pytest.mark.parametrize("junk", [b"", b"\x00", b"\x00\x03\x00\x01hi", b"\x00\x04\x00\x01", b"\xff\xff"])
def test_non_requests_to_the_listener_are_ignored(root, make_server, junk):
    server = make_server(root)
    with raw_socket(0.2) as client:
        client.sendto(junk, server.server_address)
        with pytest.raises(socket.timeout):
            expect(client)
    assert client_for(server).get("one.bin") == b"x"  # still serving


def test_server_busy(root, make_server):
    server = make_server(root, max_sessions=1, timeout=2)
    with raw_socket() as first, raw_socket() as second:
        first.sendto(encode_request(Opcode.RRQ, "513.bin"), server.server_address)
        _, tid = expect(first)  # holds the only session
        second.sendto(encode_request(Opcode.RRQ, "513.bin"), server.server_address)
        error, _ = expect(second)
        assert error.code == ErrorCode.NOT_DEFINED and "busy" in error.message
        first.sendto(encode_error(0, "done"), tid)


def _bindable(address: str) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind((address, 0))
        return True
    except OSError:
        return False


def test_reply_comes_from_the_address_the_request_was_sent_to(root, make_server):
    if not _bindable("127.0.0.2"):
        pytest.skip("127.0.0.2 is not a local address here (macOS configures only 127.0.0.1)")
    server = make_server(root, host="0.0.0.0")
    if sys.platform in ("linux", "win32"):
        assert server.supports_pktinfo
    elif not server.supports_pktinfo:
        pytest.skip("no pktinfo on this platform")
    with raw_socket() as client:
        client.sendto(encode_request(Opcode.RRQ, "one.bin"), ("127.0.0.2", server.server_address[1]))
        packet, source = expect(client)
        assert packet == tftp.Data(1, b"x")
        assert source[0] == "127.0.0.2"
        client.sendto(encode_ack(1), source)


def test_reply_address_on_dual_stack_listener(root, make_server):
    if not _bindable("127.0.0.2"):
        pytest.skip("127.0.0.2 is not a local address here")
    server = make_server(root, host="::")
    if not server.dual_stack:
        pytest.skip("no dual-stack sockets here")
    with raw_socket() as client:
        client.sendto(encode_request(Opcode.RRQ, "one.bin"), ("127.0.0.2", server.server_address[1]))
        _, source = expect(client)
        if server.supports_pktinfo:
            assert source[0] == "127.0.0.2"
        client.sendto(encode_ack(1), source)


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


def test_client_falls_back_when_options_are_refused():
    def script(packet):
        if isinstance(packet, tftp.Request):
            if packet.options:
                return [encode_error(ErrorCode.OPTION_REFUSED)]
            return [encode_data(1, b"hi")]
        return []

    fake = FakeServer(script)
    try:
        assert tftp.Client(*fake.address, timeout=0.5).get("f") == b"hi"
        assert [bool(r.options) for r in fake.requests if isinstance(r, tftp.Request)] == [True, False]
        with pytest.raises(tftp.RemoteError):
            tftp.Client(*fake.address, timeout=0.5, fallback=False).get("f")
    finally:
        fake.close()


def test_client_refuses_an_oack_it_did_not_ask_for():
    def script(packet):
        if isinstance(packet, tftp.Request):
            return [encode_oack({"blksize": "9000"})]
        return []

    fake = FakeServer(script)
    try:
        with pytest.raises(tftp.ProtocolError) as info:
            tftp.Client(*fake.address, timeout=0.5, blksize=1428).get("f")
        assert info.value.code == ErrorCode.OPTION_REFUSED
        fake.thread.join(0.5)
        assert any(isinstance(p, tftp.Error) and p.code == ErrorCode.OPTION_REFUSED for p in fake.requests)
    finally:
        fake.close()


def test_client_reports_remote_errors():
    def script(packet):
        return (
            [encode_error(ErrorCode.ACCESS_VIOLATION, "go away")] if isinstance(packet, tftp.Request) else []
        )

    fake = FakeServer(script)
    try:
        with pytest.raises(tftp.RemoteError) as info:
            tftp.Client(*fake.address, timeout=0.5).get("f")
        assert (info.value.code, info.value.message) == (ErrorCode.ACCESS_VIOLATION, "go away")
    finally:
        fake.close()


def test_client_accepts_host_with_port():
    def script(packet):
        return [encode_data(1, b"ok")] if isinstance(packet, tftp.Request) else []

    fake = FakeServer(script)
    try:
        assert tftp.Client("127.0.0.1:%d" % fake.address[1], 1).get("f") == b"ok"
    finally:
        fake.close()
