"""Packet events, trace hooks, reading a capture through pktcap, flow reconstruction, filters."""

from __future__ import annotations

import io
import os
import random
import struct
import time

import pktcap
import pytest

import tftp
import tftp.capture
import tftp.exceptions
from conftest import client_for, needs_ipv6
from tftp import TFTPOpcode
from tftp.packet import encode_ack, encode_data, encode_error, encode_oack, encode_request
from tftp.capture import (
    FlowTracker,
    PacketEvent,
    analyze,
    compile_filter,
    summarize,
    trace_to,
)
from pktcap import CapturedDatagram, read_datagrams

# -- events ------------------------------------------------------------------------


def test_summaries():
    assert (
        summarize(encode_request(TFTPOpcode.RRQ, "a.bin", mode="octet", options={"blksize": 1428}))
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
    assert "[s1] 10.0.0.5:2000 > 10.0.0.1:69 DATA 3 (2 bytes)" in str(event)
    out = PacketEvent(0, "out", ("::1", 5), ("::1", 6), encode_ack(1))
    assert out.source == ("::1", 5) and "[::1]:5 > [::1]:6" in str(out)


def test_a_request_event_is_json_with_its_options():
    import json

    request = PacketEvent(
        0,
        "in",
        ("10.0.0.1", 69),
        ("10.0.0.5", 2000),
        encode_request(1, "f", mode="octet", options={"blksize": 8}),
    )
    assert json.loads(json.dumps(request.to_dict()))["options"] == {"blksize": "8"}
    refusal = PacketEvent(0, "out", ("10.0.0.1", 69), ("10.0.0.5", 2000), encode_error(8, "no"))
    assert json.loads(json.dumps(refusal.to_dict()))["code"] == 8


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
    with pktcap.PcapWriter(path) as writer:
        server = make_server(root, host=host, trace=trace_to(writer), writable=True)
        client = client_for(server, host=host, blksize=1024, windowsize=4)
        client.get("big.bin")
        client.put("uploaded.bin", b"u" * 3000)
        with pytest.raises(tftp.FileNotFound):
            client.get("missing")
        server.shutdown()
        assert server.wait_closed(5.0)
    analysis = analyze(path, ports=[server.server_address[1]])
    by_name = {t.filename: t for t in analysis.transfers}
    big = by_name["big.bin"]
    assert big.is_complete and big.data() == (root / "big.bin").read_bytes()
    assert (big.blksize, big.windowsize, big.tsize) == (1024, 4, 300_001)
    assert big.acknowledged["blksize"] == "1024" and big.missing_blocks == ()
    up = by_name["uploaded.bin"]
    assert up.operation == "write" and up.is_complete and up.data() == b"u" * 3000
    missing = by_name["missing"]
    assert missing.error == (1, "file not found", "server") and not missing.is_complete
    assert all(e.session for e in analysis.events)


# -- reading a capture: what a caller of analyze() sees from pktcap's reader -------------------------

_PCAP_HEADER = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 101)


def _block(block_type, body):
    return struct.pack("<II", block_type, 12 + len(body)) + body + struct.pack("<I", 12 + len(body))


_SECTION = _block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
_INTERFACE = _block(1, struct.pack("<HHI", 101, 0, 65535))


def _ipv4_udp(src, dst, payload, ident=1, flags_offset=0):
    header = struct.pack(
        "!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), ident, flags_offset, 64, 17, 0, src, dst
    )
    return header + payload


def _pcap_record(packet):
    return struct.pack("<IIII", 1_700_000_000, 0, len(packet), len(packet)) + packet


#: Damaged captures and what each claims; every one is a ``pktcap.CaptureFormatError``.
_DAMAGED = {
    "not a capture": b"hello world",
    "a pcap record claiming 1 GiB": _PCAP_HEADER + struct.pack("<IIII", 0, 0, 1 << 30, 1 << 30) + b"x" * 8,
    "a pcap record over the frame ceiling": _PCAP_HEADER
    + struct.pack("<IIII", 0, 0, 300_000, 300_000)
    + b"x" * 300_000,
    "a pcapng block claiming 1 GiB": _SECTION + _INTERFACE + struct.pack("<II", 6, 1 << 30) + b"x" * 8,
    "a packet block with a 4-octet body": _SECTION + _INTERFACE + _block(6, b""),
    "an interface block cut after its header": _SECTION + struct.pack("<II", 1, 20),
    "a packet block cut after its header": _SECTION + _INTERFACE + struct.pack("<II", 6, 64),
    "a simple packet block cut after its header": _SECTION + _INTERFACE + struct.pack("<II", 3, 64),
    "a section header of length 8": struct.pack("<II", 0x0A0D0D0A, 8) + b"\x4d\x3c\x2b\x1a",
    "a section header with no byte-order magic": struct.pack("<II", 0x0A0D0D0A, 28) + b"\xff" * 20,
    "a pcap header cut short": _PCAP_HEADER[:10],
    "a pcap record cut inside its header": _PCAP_HEADER + b"\0" * 7,
}


@pytest.mark.parametrize("blob", _DAMAGED.values(), ids=list(_DAMAGED))
def test_a_damaged_capture_is_a_format_error_and_costs_no_memory_of_its_claim(blob):
    import tracemalloc

    tracemalloc.start()
    try:
        with pytest.raises(pktcap.CaptureFormatError):
            analyze(io.BytesIO(blob))
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 2 * 2**20, "a %d-octet capture took %d octets to refuse" % (len(blob), peak)


def test_the_capture_format_error_is_pktcaps_and_not_a_tftp_error():
    with pytest.raises(pktcap.CaptureFormatError) as caught:
        analyze(io.BytesIO(b"hello world"))
    assert isinstance(caught.value, ValueError) and not isinstance(caught.value, tftp.TFTPError)
    assert not hasattr(tftp.exceptions, "CaptureFormatError") and not hasattr(
        tftp.capture, "CaptureFormatError"
    )


@pytest.mark.parametrize("container", ["pcap", "pcapng"])
def test_random_bytes_after_a_valid_header_are_a_format_error_or_a_capture(container):
    rng = random.Random(3247023)
    head = _PCAP_HEADER if container == "pcap" else _SECTION + _INTERFACE
    for _ in range(1500):
        body = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 120)))
        if container == "pcap":
            body = struct.pack("<IIII", 0, 0, rng.randint(0, 80), 0) + body
        else:
            body = struct.pack("<II", rng.choice((1, 3, 6, 6, 6)), rng.randint(12, 64)) + body
        try:
            analyze(io.BytesIO(head + body))
        except pktcap.CaptureFormatError:
            pass


def test_an_empty_input_and_a_header_alone_are_captures_with_nothing_in_them():
    for blob in (b"", _PCAP_HEADER, _SECTION + _INTERFACE):
        analysis = analyze(io.BytesIO(blob))
        assert (analysis.events, analysis.transfers) == ([], [])


def test_a_capture_of_a_link_type_nothing_dissects_holds_no_transfer():
    wifi = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 105) + _pcap_record(b"\0" * 60)
    assert analyze(io.BytesIO(wifi)).transfers == []


def test_a_text_stream_is_a_type_error_and_a_missing_path_an_os_error(tmp_path):
    with pytest.raises(TypeError):
        analyze(io.StringIO("not bytes"))
    with pytest.raises(FileNotFoundError):
        analyze(tmp_path / "missing.pcap")


def test_analyze_follows_datagrams_it_is_given_without_reading_a_file():
    analysis = analyze(_flow((C, S, encode_request(TFTPOpcode.RRQ, "f")), (T, C, encode_data(1, b"x"))))
    assert [t.filename for t in analysis.transfers] == ["f"] and analysis.transfers[0].data() == b"x"


def _fragments_of_one_datagram(count):
    """IPv4 fragments of one datagram: the last first, then every piece from offset 0 but one hole."""
    src, dst = bytes([10, 0, 0, 5]), bytes([10, 0, 0, 1])
    frames = [_ipv4_udp(src, dst, b"y" * 8, ident=7, flags_offset=count + 1)]
    for index in range(count):
        frames.append(_ipv4_udp(src, dst, b"y" * 8, ident=7, flags_offset=0x2000 | index))
    return _PCAP_HEADER + b"".join(_pcap_record(frame) for frame in frames)


def _best_of(runs, blob):
    best = None
    for _ in range(runs):
        started = time.perf_counter()
        analyze(io.BytesIO(blob))
        best = min(best or 1e9, time.perf_counter() - started)
    return best


def test_fragments_that_never_complete_cost_time_in_proportion_to_their_number():
    """Linear work is a ratio of 4 for four times the fragments; re-sorting every piece is 16."""
    small, large = _best_of(3, _fragments_of_one_datagram(2000)), _best_of(
        3, _fragments_of_one_datagram(8000)
    )
    assert large < 9 * small, "%d fragments %.3f s, %d fragments %.3f s" % (2000, small, 8000, large)


# -- flow tracking ------------------------------------------------------------------------------


def _flow(*packets, start=0.0):
    return [CapturedDatagram(start + i * 0.01, src, dst, data) for i, (src, dst, data) in enumerate(packets)]


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
    assert not transfer.is_complete and transfer.data() == b"a" * 512
    assert len(events) == 6 and all(e.session == transfer.session for e in events)


def test_flow_rollover_and_netascii():
    tracker = FlowTracker(keep_payloads=True)
    packets = [(C, S, encode_request(TFTPOpcode.RRQ, "t", mode="netascii", options={"blksize": 8}))]
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
    assert transfer.is_complete and transfer.missing_blocks == ()
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
    with pytest.raises(pktcap.CaptureFilterError) as caught:
        compile_filter(expression)
    assert isinstance(caught.value, ValueError) and not isinstance(caught.value, tftp.TFTPError)


def test_a_filter_that_changed_meaning_with_the_grammar():
    # No longer an error: the first "=" ends the key, so a value may hold "=" and "!".
    pattern = compile_filter("file=a!=b")
    assert pattern(_event(encode_request(TFTPOpcode.RRQ, "a!=b")))
    assert not pattern(_event(encode_request(TFTPOpcode.RRQ, "a")))


@pytest.mark.parametrize(
    "expression",
    ["op=RRQ and", "file=*.efi and", "file=a or b", "op=RRQ or op=WRQ"],
    ids=["a trailing and", "a trailing and after a pattern", "or in a value", "or between clauses"],
)
def test_a_filter_with_a_dangling_and_or_an_or_is_refused(expression):
    with pytest.raises(pktcap.CaptureFilterError):
        compile_filter(expression)


def test_a_clause_the_keys_refuse_is_named_in_the_error():
    with pytest.raises(pktcap.CaptureFilterError, match="colour"):
        compile_filter("op=RRQ and colour=red")


@pytest.mark.parametrize(
    "expression,matches",
    [
        ("host=10.0.0.5", True),
        ("src=[2001:db8::5]:1000", False),
        ("dst=2001:db8::/32", False),
        ("src=10.0.0.5:2000,[::1]:9", True),
        ("host=fe80::/10", False),
        ("dst=::ffff:0:0/96", False),
        ("dst=::1", False),
        ("src=:2000", True),
    ],
)
def test_address_forms_of_the_address_keys(expression, matches):
    assert compile_filter(expression)(_event(encode_request(TFTPOpcode.RRQ, "f"))) is matches


def test_an_address_with_a_zone_matches_its_network_and_a_mapped_one_its_ipv4_host():
    zoned = _event(encode_ack(1), src=("fe80::1%eth0", 5), dst=("fe80::2%eth0", 6))
    assert compile_filter("src=fe80::/10 and dst=fe80::2")(zoned)
    assert compile_filter("src=[fe80::1]:5")(zoned)
    mapped = _event(encode_ack(1), src=("::ffff:192.0.2.9", 5))
    assert compile_filter("src=192.0.2.0/24")(mapped) and not compile_filter("src=198.51.100.0/24")(mapped)


@pytest.mark.parametrize(
    "value", ["10.0.0.5:", ":", ":x", ":99999", "[::1", "[10.0.0.5]:5", "host:80:extra", "10.0.0.5:70000"]
)
def test_a_malformed_address_is_refused_when_the_filter_is_compiled(value):
    with pytest.raises(pktcap.CaptureFilterError):
        compile_filter("host=" + value)


def test_an_endpoint_is_written_with_brackets_for_ipv6_and_a_question_mark_for_none():
    from tftp.capture._events import _endpoint

    assert (_endpoint(("10.0.0.5", 69)), _endpoint(("::1", 69)), _endpoint(("fe80::1%eth0", 69))) == (
        "10.0.0.5:69",
        "[::1]:69",
        "[fe80::1%eth0]:69",
    )
    assert (_endpoint(()), _endpoint(None), _endpoint(("", 69))) == ("?", "?", "?")


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
    assert (transfer.size, transfer.retransmissions, transfer.missing_blocks) == (1546, 1, ((3, 3),))
    assert transfer.is_complete
    record = transfer.to_dict()
    assert (record["bytes"], record["retransmissions"], record["missing_blocks"]) == (1546, 1, [[3, 3]])


# -- a failing hook, and the observer's address ---------------------------------------------------


def _hook_records(caplog):
    return [r for r in caplog.records if "trace hook" in r.getMessage()]


def test_a_failing_trace_hook_is_logged_once_with_its_traceback_and_does_not_end_the_transfer(
    root, make_server, caplog
):
    calls = []

    def broken(event):
        calls.append(event)
        raise RuntimeError("hook bug")

    server = make_server(root)
    client = client_for(server, trace=broken, blksize=1428)
    with caplog.at_level("DEBUG"):
        assert client.get("big.bin") == (root / "big.bin").read_bytes()
        assert client.get("one.bin") == b"x"
    assert len(calls) > 8, "the hook was not called for each datagram"
    records = _hook_records(caplog)
    assert len(records) == 1, "%d records for %d datagrams" % (len(records), len(calls))
    assert records[0].exc_info and records[0].exc_info[0] is RuntimeError


def test_a_failing_server_trace_hook_is_logged_once(root, make_server, caplog):
    calls = []

    def broken(event):
        calls.append(event)
        raise RuntimeError("hook bug")

    server = make_server(root, trace=broken)
    with caplog.at_level("DEBUG"):
        assert client_for(server).get("big.bin") == (root / "big.bin").read_bytes()
        server.shutdown()
        assert server.wait_closed(5.0)
    assert len(calls) > 8
    assert len(_hook_records(caplog)) == 1


def test_a_failing_trace_hook_is_logged_once_by_the_asyncio_client_too(root, make_server, caplog):
    import asyncio

    calls = []

    def broken(event):
        calls.append(event)
        raise RuntimeError("hook bug")

    server = make_server(root)
    client = tftp.AsyncTFTPClient("127.0.0.1", server.server_address[1], timeout=0.5, trace=broken)
    with caplog.at_level("DEBUG"):
        assert asyncio.run(client.get("big.bin")) == (root / "big.bin").read_bytes()
    assert len(calls) > 8
    records = _hook_records(caplog)
    assert len(records) == 1 and records[0].exc_info


@pytest.mark.parametrize("host", ["127.0.0.1", pytest.param("::1", marks=needs_ipv6)])
def test_the_clients_events_name_the_address_it_uses_towards_the_server(root, make_server, host):
    import asyncio

    events = []
    server = make_server(root, host=host)
    client_for(server, host=host, trace=events.append).get("one.bin")
    aevents = []
    client = tftp.AsyncTFTPClient(host, server.server_address[1], timeout=0.5, trace=aevents.append)
    asyncio.run(client.get("one.bin"))
    for seen in (events, aevents):
        assert seen and {e.local[0] for e in seen} == {host}, "local: %s" % sorted({e.local[0] for e in seen})
        assert {e.remote[0] for e in seen} == {host}


class _Recorder:
    def __init__(self):
        self.calls = []

    def write(self, time, source, destination, payload):
        self.calls.append((time, source, destination, payload))


def test_trace_to_hands_the_writer_the_time_both_ends_and_the_datagram():
    writer = _Recorder()
    hook = trace_to(writer)
    incoming = PacketEvent(5.5, "in", ("10.0.0.1", 69), ("10.0.0.5", 2000), encode_ack(1), "server")
    outgoing = PacketEvent(6.5, "out", ("10.0.0.1", 69), ("10.0.0.5", 2000), encode_ack(2), "server")
    hook(incoming)
    hook(outgoing)
    assert writer.calls == [
        (5.5, ("10.0.0.5", 2000), ("10.0.0.1", 69), encode_ack(1)),
        (6.5, ("10.0.0.1", 69), ("10.0.0.5", 2000), encode_ack(2)),
    ]


def test_trace_to_writes_a_pcapng_capture_as_well(root, make_server, tmp_path):
    path = tmp_path / "trace.pcapng"
    server = make_server(root)
    with pktcap.PcapngWriter(path) as writer:
        client_for(server, trace=trace_to(writer)).get("513.bin")
    (transfer,) = analyze(path, ports=[server.server_address[1]]).transfers
    assert (
        transfer.is_complete and transfer.size == 513 and transfer.data() == (root / "513.bin").read_bytes()
    )


def test_a_writer_that_fails_costs_the_transfer_nothing_and_one_log_record(root, make_server, caplog):
    class Full:
        def write(self, *args):
            raise OSError("no space left on device")

    server = make_server(root)
    with caplog.at_level("ERROR", logger="tftp.client"):
        assert (
            client_for(server, trace=trace_to(Full())).get("1428x3.bin") == (root / "1428x3.bin").read_bytes()
        )
    assert len([r for r in caplog.records if r.name == "tftp.client"]) == 1


def test_a_pcap_taken_by_the_client_carries_the_address_the_server_saw(root, make_server, tmp_path):
    path = tmp_path / "client.pcap"
    seen = []
    server = make_server(root, trace=seen.append)
    with pktcap.PcapWriter(path) as writer:
        client_for(server, trace=trace_to(writer)).get("one.bin")
    datagrams = list(read_datagrams(path))
    assert datagrams
    assert {d.source[0] for d in datagrams} | {d.destination[0] for d in datagrams} == {"127.0.0.1"}
    request = [e for e in seen if e.opcode_name == "RRQ"][0]
    first = datagrams[0]
    assert first.source == (request.remote[0], request.remote[1]), "the pcap names another client address"


# -- what a capture costs is bounded by its size ---------------------------------------------------


def _tracked(*packets):
    tracker = FlowTracker()
    list(tracker.feed_all(_flow(*packets)))
    return tracker


def test_missing_blocks_are_ranges_and_missing_count_counts_them():
    tracker = _tracked(
        (C, S, encode_request(TFTPOpcode.RRQ, "f")),
        (T, C, encode_data(1, b"a" * 512)),
        (T, C, encode_data(2, b"a" * 512)),
        (T, C, encode_data(10, b"a" * 512)),
        (T, C, encode_data(13, b"a" * 512)),
    )
    (transfer,) = tracker.transfers
    assert transfer.missing_blocks == ((3, 9), (11, 12))
    assert transfer.missing_count == 9
    record = transfer.to_dict()
    assert (record["missing_blocks"], record["missing_count"]) == ([[3, 9], [11, 12]], 9)


def _cut(*packets, cut):
    """The flow of ``packets`` with the datagrams at the indexes in ``cut`` marked as cut by a snap length."""
    datagrams = [d._replace(truncated=i in cut) for i, d in enumerate(_flow(*packets))]
    tracker = FlowTracker()
    list(tracker.feed_all(datagrams))
    return tracker


def test_a_data_the_snap_length_cut_is_a_packet_and_not_the_final_block():
    (transfer,) = _cut(
        (C, S, encode_request(TFTPOpcode.RRQ, "f")),
        (T, C, encode_data(1, b"a" * 512)),
        (T, C, encode_data(2, b"b" * 100)),  # cut: its headers say 512 octets
        (C, T, encode_ack(2)),
        cut={2},
    ).transfers
    assert transfer.packets == 4
    assert not transfer.is_complete and transfer.final_block is None
    assert transfer.missing_blocks == ((2, 2),) and transfer.missing_count == 1
    assert transfer.size == 512 and transfer.data() == b"a" * 512
    assert transfer.to_dict()["missing_blocks"] == [[2, 2]]


def test_a_cut_data_is_not_a_retransmission_and_a_whole_one_after_it_completes_the_transfer():
    (transfer,) = _cut(
        (C, S, encode_request(TFTPOpcode.RRQ, "f")),
        (T, C, encode_data(1, b"a" * 512)),
        (T, C, encode_data(2, b"b" * 100)),
        (T, C, encode_data(2, b"b" * 100)),  # the whole datagram, sent again
        (C, T, encode_ack(2)),
        cut={2},
    ).transfers
    assert (transfer.retransmissions, transfer.missing_blocks) == (0, ())
    assert transfer.is_complete and transfer.data() == b"a" * 512 + b"b" * 100


def test_a_cut_data_ahead_of_a_gap_names_both_in_the_missing_blocks():
    (transfer,) = _cut(
        (C, S, encode_request(TFTPOpcode.RRQ, "f")),
        (T, C, encode_data(1, b"a" * 512)),
        (T, C, encode_data(3, b"c" * 100)),  # cut
        (T, C, encode_data(4, b"d" * 512)),
        (T, C, encode_data(5, b"e" * 7)),
        cut={2},
    ).transfers
    assert transfer.missing_blocks == ((2, 3),) and not transfer.is_complete


def test_a_transfer_with_a_cut_data_is_written_as_partial(tmp_path):
    (transfer,) = _cut(
        (C, S, encode_request(TFTPOpcode.RRQ, "f")),
        (T, C, encode_data(1, b"a" * 512)),
        (T, C, encode_data(2, b"b" * 100)),
        cut={2},
    ).transfers
    assert transfer.write_to(tmp_path).endswith(".partial")


def test_a_datagram_object_with_no_truncated_member_is_read_as_whole():
    tracker = FlowTracker()
    for datagram in _flow(
        (C, S, encode_request(TFTPOpcode.RRQ, "f")), (T, C, encode_data(1, b"x")), (C, T, encode_ack(1))
    ):
        tracker.feed(_Bare(datagram))
    assert tracker.transfers[0].is_complete


class _Bare:
    """The four members ``DatagramLike`` requires, and no ``truncated``."""

    def __init__(self, datagram):
        self.time, self.source = datagram.time, datagram.source
        self.destination, self.payload = datagram.destination, datagram.payload


def test_a_capture_that_jumps_half_the_block_space_costs_nothing():
    """Fifty DATA, 32768 blocks apart: a list of the missing blocks held 1,638,350 numbers (63 MiB)."""
    import tracemalloc

    packets = [(C, S, encode_request(TFTPOpcode.RRQ, "f"))]
    for index in range(50):
        packets.append((T, C, encode_data((32768 * (index + 1)) % 65536, b"x" * 512)))
    tracemalloc.start()
    try:
        tracker = _tracked(*packets)
        (transfer,) = tracker.transfers
        transfer.to_dict()
        transfer.missing_blocks, transfer.missing_count
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024, peak
    assert transfer.packets == 51  # every datagram is counted, a block that cannot be placed is not stored
    assert transfer.missing_count < 64


@pytest.mark.parametrize(
    "window,ahead,placed", [(1, 64, True), (1, 65, False), (128, 128, True), (128, 129, False)]
)
def test_a_data_is_placed_at_most_a_window_ahead(window, ahead, placed):
    packets = [(C, S, encode_request(TFTPOpcode.RRQ, "f", options={"windowsize": window}))]
    packets.append((T, C, encode_oack({"windowsize": window})))
    packets.append((T, C, encode_data(1, b"a" * 512)))
    packets.append((T, C, encode_data(1 + ahead, b"a" * 512)))
    (transfer,) = _tracked(*packets).transfers
    assert (transfer.size == 1024) is placed, transfer.size


# -- a time the platform cannot convert, text from the wire --------------------------------------


def test_a_timestamp_the_platform_cannot_convert_is_printed_as_the_number():
    event = PacketEvent(2.0**40, "seen", S, C, encode_ack(1))
    assert str(event).startswith("1099511627776.000000 ")
    assert str(PacketEvent(float("nan"), "seen", S, C, encode_ack(1))).startswith("nan ")


ESCAPES = "\x1b[2J\x1b]0;owned\x07\n\r" + chr(0x202E)  # ESC sequences, a newline, a right-to-left override


def _raw_request(filename, mode="octet", **options):
    """A request as a hostile client writes it: nothing checks what it holds."""
    fields = [filename, mode] + [item for pair in options.items() for item in pair]
    return b"\x00\x01" + b"".join(field.encode("utf-8", "surrogateescape") + b"\x00" for field in fields)


def test_text_a_peer_chose_is_escaped_in_a_summary():
    request = _raw_request("f" + ESCAPES, "oc" + ESCAPES, **{"x" + ESCAPES: "v" + ESCAPES})
    for data in (
        request,
        encode_oack({"blk" + ESCAPES: "1" + ESCAPES}),
        encode_error(1, "no" + ESCAPES),
        b"\x00\x01f\x00o\xff\x00",
    ):
        line = summarize(data)
        assert line.isprintable(), repr(line)
        assert str(PacketEvent(1.0, "seen", S, C, data)).isprintable()
    assert "\\x1b[2J" in summarize(request) and "\\u202e" in summarize(request)


def test_a_log_line_about_a_request_holds_no_control_character(root, make_server, caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="tftp.server")
    import socket
    import time

    server = make_server(root)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as raw:
        raw.settimeout(2)
        raw.bind(("127.0.0.1", 0))
        raw.sendto(_raw_request("one.bin", "oc" + ESCAPES), server.server_address)
        raw.recvfrom(2048)
        raw.sendto(_raw_request("one.bin" + ESCAPES), server.server_address)
        raw.recvfrom(2048)
        raw.sendto(encode_request(TFTPOpcode.RRQ, "big.bin"), server.server_address)
        _, tid = raw.recvfrom(2048)
        raw.sendto(encode_error(0, "bye" + ESCAPES), tid)
        deadline = time.monotonic() + 3
        while server.stats_snapshot()["failed"] < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
    lines = [r.getMessage() for r in caplog.get_records("call") if r.name == "tftp.server"]
    assert len(lines) >= 3, lines
    assert all(line.isprintable() for line in lines), lines


# -- finished transfers of a live capture ---------------------------------------------------------


def _request_from(port):
    return (("10.1.%d.%d" % (port // 250, port % 250), 2000), S, encode_request(TFTPOpcode.RRQ, "f%d" % port))


def test_a_tracker_holds_at_most_max_tracked_transfers_and_hands_over_the_rest():
    gone = []
    tracker = FlowTracker(on_complete=gone.append, max_tracked=3)
    list(tracker.feed_all(_flow(*[_request_from(port) for port in range(10)])))
    assert len(tracker.transfers) == 3 and len(tracker._by_client) == 3
    assert [t.filename for t in gone] == ["f%d" % n for n in range(7)]  # the quietest first
    assert [t.filename for t in tracker.transfers] == ["f7", "f8", "f9"]


def test_a_finished_transfer_goes_before_an_older_unfinished_one():
    gone = []
    tracker = FlowTracker(on_complete=gone.append, max_tracked=2)
    older, finished = _request_from(1), _request_from(2)
    server_tid = ("10.0.0.1", 41000)
    packets = [
        older,
        finished,
        (server_tid, finished[0], encode_data(1, b"x")),
        (finished[0], server_tid, encode_ack(1)),
        _request_from(3),
    ]
    list(tracker.feed_all(_flow(*packets)))
    assert [t.filename for t in gone] == ["f2"] and gone[0].is_complete
    assert [t.filename for t in tracker.transfers] == ["f1", "f3"]


def test_a_tracker_without_a_bound_keeps_every_transfer():
    packets = _flow(*[_request_from(port) for port in range(1100)])
    tracker = FlowTracker(max_tracked=None)
    list(tracker.feed_all(packets))
    assert len(tracker.transfers) == 1100
    assert len(analyze(iter(packets)).transfers) == 1100
    with pytest.raises(ValueError):
        FlowTracker(max_tracked=0)


# -- writing a transfer's file -----------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["../../outside.bin", "..", ".", "con", "a\x00b", "\\\\host\\share\\x", "sub/x\x1b[2J", "x" * 400, ""],
)
def test_a_transfer_is_written_under_the_directory_whatever_its_name(tmp_path, name):
    tracker = _tracked(
        (C, S, encode_request(TFTPOpcode.RRQ, "x")),
        (T, C, encode_data(1, b"payload")),
        (C, T, encode_ack(1)),
    )
    (transfer,) = tracker.transfers
    transfer.filename = name
    target = tmp_path / "recovered"
    path = transfer.write_to(target)
    assert os.path.dirname(os.path.realpath(path)) == os.path.realpath(target)
    assert os.path.basename(path).startswith(transfer.session + "-") and len(os.path.basename(path)) < 130
    with open(path, "rb") as written:
        assert written.read() == b"payload" and not path.endswith(".partial")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["recovered"]


def test_a_transfer_with_gaps_is_written_as_partial_and_one_without_data_is_not_written(tmp_path):
    tracker = _tracked(
        (C, S, encode_request(TFTPOpcode.RRQ, "f")),
        (T, C, encode_data(1, b"a" * 512)),
        (T, C, encode_data(3, b"b")),
    )
    (transfer,) = tracker.transfers
    assert transfer.write_to(tmp_path).endswith("-f.partial")
    bare = FlowTracker(keep_payloads=False)
    list(bare.feed_all(_flow((C, S, encode_request(TFTPOpcode.RRQ, "f")), (T, C, encode_data(1, b"x")))))
    assert bare.transfers[0].write_to(tmp_path) is None


# -- a seeded fuzz --------------------------------------------------------------------------------


def test_nothing_a_capture_holds_raises_or_prints_a_control_character():
    import random

    rng = random.Random(20261006)
    seeds = [
        encode_request(TFTPOpcode.RRQ, "f", options={"blksize": 8, "windowsize": 4}),
        encode_request(TFTPOpcode.WRQ, "g", mode="netascii"),
        encode_oack({"blksize": 1428, "tsize": 5, "windowsize": 2, "rollover": 1}),
        encode_data(1, b"payload"),
        encode_ack(7),
        encode_error(1, "no such file"),
    ]
    tracker = FlowTracker(max_tracked=16)
    clock = 0.0
    for _ in range(4000):
        data = bytearray(
            rng.choice(seeds)
            if rng.random() < 0.8
            else bytes(rng.randrange(256) for _ in range(rng.randrange(0, 40)))
        )
        for _ in range(rng.randrange(0, 4)):
            if data:
                data[rng.randrange(len(data))] = rng.randrange(256)
        if rng.random() < 0.2 and data:
            del data[rng.randrange(len(data)) :]
        clock += 0.001
        source, destination = rng.choice([(C, S), (T, C), (C, T), (S, C)])
        event = tracker.feed(CapturedDatagram(clock, source, destination, bytes(data)))
        assert summarize(bytes(data)).isprintable()
        if event is not None:
            assert str(event).isprintable()
    for transfer in tracker.transfers:
        transfer.to_dict(), transfer.missing_blocks, transfer.data(), repr(transfer)


def test_one_packet_that_cannot_be_read_fails_its_transfer_and_not_the_capture(monkeypatch):
    real = FlowTracker._observe

    def observe(self, transfer, source, payload, *rest):
        if payload[:4] == b"\x00\x04\x00\x09":
            raise ValueError("a reading nobody expected")
        return real(self, transfer, source, payload, *rest)

    monkeypatch.setattr(FlowTracker, "_observe", observe)
    tracker = _tracked(
        (C, S, encode_request(TFTPOpcode.RRQ, "f")),
        (T, C, encode_data(1, b"a" * 512)),
        (C, T, encode_ack(9)),
        (T, C, encode_data(2, b"b")),
    )
    (transfer,) = tracker.transfers
    assert transfer.error == (0, "unreadable packet: ValueError", "capture")
    assert transfer.size == 513 and transfer.packets == 4


# -- combining trace hooks ----------------------------------------------------------------------


def test_no_hook_and_one_hook_combine_to_themselves():
    from tftp.capture import combine_hooks

    assert combine_hooks() is None and combine_hooks(None, None) is None
    assert combine_hooks(None, print) is print


def test_combined_hooks_see_every_event_in_order(root, make_server):
    from tftp.capture import combine_hooks

    log = []
    server = make_server(root)
    client = client_for(server)
    client.trace = combine_hooks(lambda e: log.append(("a", e)), None, lambda e: log.append(("b", e)))
    assert client.get("one.bin") == b"x"
    names = [name for name, _ in log]
    assert names and len(names) % 2 == 0 and names == ["a", "b"] * (len(names) // 2)
    assert all(log[i][1] is log[i + 1][1] for i in range(0, len(log), 2))  # the same event, to both


def test_a_failing_hook_does_not_keep_the_others_from_the_event(caplog):
    import logging

    from tftp.capture import combine_hooks

    seen = []

    def failing(event):
        raise OSError("disk full")

    hook = combine_hooks(failing, seen.append, seen.append)
    with pytest.raises(OSError, match="disk full"):
        hook("event")
    assert seen == ["event", "event"]
    first = combine_hooks(seen.append, failing)
    with pytest.raises(OSError):
        first("again")
    assert seen[-1] == "again"


def test_a_combined_hook_whose_member_fails_costs_the_transfer_nothing(root, make_server, caplog):
    import logging

    from tftp.capture import combine_hooks

    seen = []

    def failing(event):
        raise OSError("disk full")

    client = client_for(make_server(root))
    client.trace = combine_hooks(failing, seen.append)
    with caplog.at_level(logging.ERROR, logger="tftp.client"):
        assert client.get("big.bin") == (root / "big.bin").read_bytes()
    assert seen  # the member after the failing one was still called for every datagram
    assert len([r for r in caplog.records if "trace hook failed" in r.getMessage()]) == 1
