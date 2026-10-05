"""Packet events, trace hooks, pcap/pcapng decoding, flow reconstruction, filters."""

from __future__ import annotations

import io
import os
import struct

import pytest

import tftp
from conftest import client_for, needs_ipv6
from tftp import TFTPOpcode, encode_ack, encode_data, encode_error, encode_oack, encode_request
from tftp.capture import (
    CaptureFilterError,
    FlowTracker,
    PacketEvent,
    PcapWriter,
    UdpDatagram,
    analyze,
    compile_filter,
    read_datagrams,
    summarize,
)

# -- events ------------------------------------------------------------------------


def test_summaries():
    assert (
        summarize(encode_request(TFTPOpcode.RRQ, "a.bin", "octet", {"blksize": 1428}))
        == "RRQ 'a.bin' octet blksize=1428"
    )
    assert summarize(encode_data(7, b"xyz")) == "DATA 7 (3 bytes)"
    assert summarize(encode_ack(65535)) == "ACK 65535"
    assert summarize(encode_error(1, "nope")) == "ERROR 1 'nope'"
    assert summarize(encode_oack({"tsize": 5})) == "OACK tsize=5"
    assert summarize(b"\x00").startswith("short")
    assert summarize(b"\x00\x09") == "malformed (unknown opcode 9)"


def test_event_fields_and_json():
    event = PacketEvent(
        1_700_000_000.5, "in", ("10.0.0.1", 69), ("10.0.0.5", 2000), encode_data(3, b"ab"), "server", "s1"
    )
    assert (event.opcode_name, event.block, event.payload_size) == ("DATA", 3, 2)
    assert event.source == ("10.0.0.5", 2000) and event.destination == ("10.0.0.1", 69)
    record = event.to_dict()
    assert record["source"] == "10.0.0.5:2000" and record["block"] == 3 and "payload" not in record
    assert event.to_dict(payload=True)["payload"] == "6162"
    assert "[s1] 10.0.0.5:2000 > 10.0.0.1:69 DATA 3 (2 bytes)" in event.format()
    out = PacketEvent(0, "out", ("::1", 5), ("::1", 6), encode_ack(1))
    assert out.source == ("::1", 5) and "[::1]:5 > [::1]:6" in out.format()


# -- trace hooks ------------------------------------------------------------------------


def test_client_and_server_trace(root, make_server):
    server_events, client_events = [], []
    server = make_server(root, trace=server_events.append)
    client_for(server, trace=client_events.append, windowsize=2).get("1428x3.bin")
    ops = [e.opcode_name for e in client_events]
    assert ops[0] == "RRQ" and "OACK" in ops and ops.count("DATA") >= 4
    assert {e.direction for e in client_events} == {"in", "out"}
    assert len({e.session for e in client_events}) == 1 and client_events[0].role == "client"
    assert all(e.role == "server" for e in server_events)
    assert [e.opcode_name for e in server_events][0] == "RRQ"
    assert len({e.session for e in server_events}) == 1


def test_a_failing_trace_hook_does_not_break_transfers(root, make_server):
    def broken(event):
        raise RuntimeError("hook bug")

    server = make_server(root, trace=broken)
    assert client_for(server, trace=broken).get("one.bin") == b"x"


# -- pcap writing and reading ---------------------------------------------------------------


@pytest.mark.parametrize("host", ["127.0.0.1", pytest.param("::1", marks=needs_ipv6)])
def test_pcap_roundtrip_reconstructs_transfers(root, make_server, tmp_path_factory, host):
    path = tmp_path_factory.mktemp("cap") / "trace.pcap"
    with PcapWriter(path) as writer:
        server = make_server(root, host=host, trace=writer, writable=True)
        client = client_for(server, host=host, blksize=1024, windowsize=4)
        client.get("big.bin")
        client.put("uploaded.bin", b"u" * 3000)
        with pytest.raises(tftp.FileNotFound):
            client.get("missing")
        server.stop()
    analysis = analyze(path, ports=[server.server_address[1]])
    by_name = {t.filename: t for t in analysis.transfers}
    big = by_name["big.bin"]
    assert big.complete and big.data() == (root / "big.bin").read_bytes()
    assert (big.blksize, big.windowsize, big.tsize) == (1024, 4, 300_001)
    assert big.acknowledged["blksize"] == "1024" and big.missing_blocks == []
    up = by_name["uploaded.bin"]
    assert up.operation == "write" and up.complete and up.data() == b"u" * 3000
    missing = by_name["missing"]
    assert missing.error == (1, "file not found", "server") and not missing.complete
    assert all(e.session for e in analysis.events)


def _ipv4(src, dst, payload, ident=1, offset=0, more=False):
    flags = (0x2000 if more else 0) | (offset // 8)
    header = struct.pack(
        "!BBHHHBBH4s4s",
        0x45,
        0,
        20 + len(payload),
        ident,
        flags,
        64,
        17,
        0,
        bytes(map(int, src.split("."))),
        bytes(map(int, dst.split("."))),
    )
    return header + payload


def _udp(sport, dport, payload):
    return struct.pack("!HHHH", sport, dport, 8 + len(payload), 0) + payload


def _ether(ip, vlan=False, v6=False):
    ethertype = struct.pack("!H", 0x86DD if v6 else 0x0800)
    tag = b"\x81\x00\x00\x05" if vlan else b""
    return b"\x02" * 6 + b"\x04" * 6 + tag + ethertype + ip


def _pcapng(frames, linktype=1, nanoseconds=False):
    out = io.BytesIO()
    shb_body = struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1)
    out.write(
        struct.pack("<II", 0x0A0D0D0A, 12 + len(shb_body)) + shb_body + struct.pack("<I", 12 + len(shb_body))
    )
    options = struct.pack("<HHB3x", 9, 1, 9) + struct.pack("<HH", 0, 0) if nanoseconds else b""
    idb_body = struct.pack("<HHI", linktype, 0, 65535) + options
    out.write(struct.pack("<II", 1, 12 + len(idb_body)) + idb_body + struct.pack("<I", 12 + len(idb_body)))
    for i, frame in enumerate(frames):
        stamp = (1_000_000 + i) * (1_000_000_000 if nanoseconds else 1_000_000)
        padded = frame + b"\0" * (-len(frame) % 4)
        body = struct.pack("<IIIII", 0, stamp >> 32, stamp & 0xFFFFFFFF, len(frame), len(frame)) + padded
        out.write(struct.pack("<II", 6, 12 + len(body)) + body + struct.pack("<I", 12 + len(body)))
    out.seek(0)
    return out


def test_pcapng_ethernet_vlan_and_fragments():
    big = _udp(50000, 50001, encode_data(1, os.urandom(3000)))  # 3012 bytes of UDP: 3 fragments
    frames = [
        _ether(_ipv4("10.0.0.5", "10.0.0.1", _udp(50000, 69, encode_request(TFTPOpcode.RRQ, "f")))),
        _ether(_ipv4("10.0.0.1", "10.0.0.5", big[:1480], ident=9, offset=0, more=True), vlan=True),
        _ether(_ipv4("10.0.0.1", "10.0.0.5", big[2960:], ident=9, offset=2960)),  # out of order
        _ether(_ipv4("10.0.0.1", "10.0.0.5", big[1480:2960], ident=9, offset=1480, more=True)),
        b"\x02" * 12 + b"\x08\x06" + b"\0" * 28,  # ARP: ignored
    ]
    datagrams = list(read_datagrams(_pcapng(frames)))
    assert [d.source for d in datagrams] == [("10.0.0.5", 50000), ("10.0.0.1", 50000)]
    assert datagrams[1].payload == big[8:]
    assert datagrams[0].time == pytest.approx(1_000_000)


def test_pcapng_nanoseconds_linux_sll_and_ipv6_fragments():
    payload = _udp(1000, 69, encode_request(TFTPOpcode.WRQ, "v6"))
    src = bytes(15) + b"\x01"
    first, second = payload[:16], payload[16:]

    def v6_fragment(chunk, offset, more):
        frag = struct.pack("!BBHI", 17, 0, (offset // 8) << 3 | (1 if more else 0), 77)
        return struct.pack("!IHBB", 0x60000000, 8 + len(chunk), 44, 64) + src + src + frag + chunk

    sll = lambda ip: struct.pack("!HHH8sH", 0, 772, 0, b"", 0x86DD) + ip  # noqa: E731
    frames = [sll(v6_fragment(first, 0, True)), sll(v6_fragment(second, 16, False))]
    datagrams = list(read_datagrams(_pcapng(frames, linktype=113, nanoseconds=True)))
    assert len(datagrams) == 1 and datagrams[0].source == ("::1", 1000)
    assert tftp.decode(datagrams[0].payload).filename == "v6"
    assert datagrams[0].time == pytest.approx(1_000_001)  # the last fragment's


def test_not_a_capture():
    from tftp.capture import CaptureFormatError

    with pytest.raises(CaptureFormatError):
        list(read_datagrams(io.BytesIO(b"hello world")))
    assert list(read_datagrams(io.BytesIO(b""))) == []


# -- flow tracking ------------------------------------------------------------------------------


def _flow(*packets, start=0.0):
    return [UdpDatagram(start + i * 0.01, src, dst, data) for i, (src, dst, data) in enumerate(packets)]


C, S, T = ("10.0.0.5", 2000), ("10.0.0.1", 69), ("10.0.0.1", 40000)


def test_flow_retransmission_error_and_stray():
    datagrams = _flow(
        (C, S, encode_request(TFTPOpcode.RRQ, "f")),
        (C, S, encode_request(TFTPOpcode.RRQ, "f")),  # request retransmitted
        (T, C, encode_data(1, b"a" * 512)),
        (T, C, encode_data(1, b"a" * 512)),  # DATA retransmitted
        (C, T, encode_ack(1)),
        (("10.9.9.9", 5), C, encode_data(2, b"evil")),  # stray: not part of the transfer
        (C, T, encode_error(0, "client gave up")),
    )
    tracker = FlowTracker()
    events = list(tracker.feed_all(datagrams))
    (transfer,) = tracker.transfers
    assert transfer.request_retransmissions == 1 and transfer.retransmissions == 1
    assert transfer.server_tid == T and transfer.error == (0, "client gave up", "client")
    assert not transfer.complete and transfer.data() == b"a" * 512
    assert len(events) == 6 and all(e.session == transfer.session for e in events)


def test_flow_rollover_and_netascii():
    tracker = FlowTracker(keep_payloads=True)
    packets = [(C, S, encode_request(TFTPOpcode.RRQ, "t", "netascii", {"blksize": 8}))]
    packets.append((T, C, encode_oack({"blksize": 8})))
    count = 65540
    for logical in range(1, count + 1):
        wire = logical % 65536
        size = 8 if logical < count else 2
        packets.append((T, C, encode_data(wire, b"ab\r\ncd\r\n"[:size] if size == 2 else b"ab\r\ncdef")))
    packets.append((C, T, encode_ack(count % 65536)))
    for datagram in _flow(*packets):
        tracker.feed(datagram)
    (transfer,) = tracker.transfers
    assert transfer.complete and transfer.missing_blocks == []
    assert len(transfer.data(decode_netascii=False)) == (count - 1) * 8 + 2
    assert transfer.data().count(b"\n") == count - 1 + 0  # CR LF -> LF


def test_flow_answer_from_another_address():
    other = ("10.0.0.99", 41000)
    tracker = FlowTracker()
    for datagram in _flow((C, S, encode_request(TFTPOpcode.RRQ, "f")), (other, C, encode_data(1, b"x"))):
        tracker.feed(datagram)
    assert tracker.transfers[0].server_tid == other and tracker.transfers[0].data() == b"x"


# -- filters --------------------------------------------------------------------------------------


def _event(data, src=("10.0.0.5", 2000), dst=("10.0.0.1", 69), **kw):
    return PacketEvent(0, "seen", dst, src, data, **kw)


@pytest.mark.parametrize(
    "expression,matches",
    [
        ("op=RRQ", True),
        ("op=rrq,wrq", True),
        ("op=DATA", False),
        ("host=10.0.0.0/8", True),
        ("host=192.168.0.0/16", False),
        ("src=10.0.0.5:2000", True),
        ("src=:2000 and dst=:69", True),
        ("dst=:70", False),
        ("port=69", True),
        ("file=*.efi", True),
        ("file=*.bin", False),
        ("op=RRQ and file=boot/*", True),
        ("op!=RRQ", False),
        ("session=c1", True),
        ("session!=c1", False),
    ],
)
def test_filters(expression, matches):
    event = _event(encode_request(TFTPOpcode.RRQ, "boot/x.efi"), session="c1")
    assert compile_filter(expression)(event) is matches


def test_filter_numbers_and_mapped_addresses():
    error = _event(encode_error(2, "no"), src=("::ffff:10.0.0.1", 69), dst=("::1", 1))
    assert compile_filter("op=ERROR and code=1,2")(error)
    assert compile_filter("src=10.0.0.0/8")(error)
    assert compile_filter("block=3")(_event(encode_ack(3)))
    assert compile_filter("")(error) and compile_filter(None)(error)


@pytest.mark.parametrize(
    "expression", ["op", "colour=red", "host=not-an-ip", "port=x", "block=x", "src=1.2.3.4:x"]
)
def test_filter_errors(expression):
    with pytest.raises(CaptureFilterError):
        compile_filter(expression)


# -- the counters do not depend on keeping payloads -------------------------------------------


def _lossy_capture():
    # blocks 1, 2, 2 again, 4 (3 never captured), 5 (the short last one)
    packets = [(C, S, encode_request(TFTPOpcode.RRQ, "f"))]
    for number, size in [(1, 512), (2, 512), (2, 512), (4, 512), (5, 10)]:
        packets.append((T, C, encode_data(number, b"d" * size)))
        packets.append((C, T, encode_ack(number)))
    return _flow(*packets)


@pytest.mark.parametrize("keep", [True, False])
def test_flow_counters_are_the_same_with_or_without_payloads(keep):
    tracker = FlowTracker(keep_payloads=keep)
    list(tracker.feed_all(_lossy_capture()))
    (transfer,) = tracker.transfers
    assert (transfer.bytes, transfer.retransmissions, transfer.missing_blocks) == (1546, 1, [3])
    assert transfer.complete
    record = transfer.to_dict()
    assert (record["bytes"], record["retransmissions"], record["missing_blocks"]) == (1546, 1, 1)
