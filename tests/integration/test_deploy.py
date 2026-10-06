"""Deployment features: transfer port ranges."""

from __future__ import annotations

import socket
import time

import pytest

import tftp
from tftp.relay import TFTPRelay
from conftest import client_for, free_ports, wait_until


def test_port_range_values():
    ports = tftp.server.PortRange(5, 7)
    assert list(ports) == [5, 6, 7] and len(ports) == 3
    assert 5 in ports and 7 in ports and 8 not in ports and "5" not in ports and True not in ports
    assert list(ports) == [5, 6, 7]  # iterating moves nothing
    assert str(ports) == "5:7" and repr(ports) == "PortRange(5, 7)"
    for bad in ((0, 5), (9, 8), (1, 70000), (-1, 3)):
        with pytest.raises(tftp.TFTPValueError):
            tftp.server.PortRange(*bad)
    for bad in (("1", 5), (1.0, 5), (True, 5), (None, 5)):
        with pytest.raises(TypeError):
            tftp.server.PortRange(*bad)


def test_port_range_is_a_value():
    import copy
    import pickle

    ports = tftp.server.PortRange(4000, 4010)
    same = tftp.server.PortRange(4000, 4010)
    assert ports == same and hash(ports) == hash(same) and len({ports, same}) == 1
    assert ports != tftp.server.PortRange(4000, 4011) and ports != tftp.server.PortRange(4001, 4010)
    assert ports.__eq__((4000, 4010)) is NotImplemented
    assert ports != (4000, 4010) and ports != range(4000, 4011) and ports != "4000:4010"
    with pytest.raises(AttributeError):
        ports.low = 1  # type: ignore[misc]
    with pytest.raises(AttributeError):
        ports.nothing = 1  # type: ignore[attr-defined]
    assert eval(repr(ports), {"PortRange": tftp.server.PortRange}) == ports
    for clone in (copy.copy(ports), copy.deepcopy(ports), pickle.loads(pickle.dumps(ports))):
        assert clone == ports and type(clone) is tftp.server.PortRange


@pytest.mark.parametrize(
    "text,expected", [("4000:4010", (4000, 4010)), ("4000-4010", (4000, 4010)), ("5:5", (5, 5))]
)
def test_port_range_parse(text, expected):
    ports = tftp.server.PortRange.parse(text)
    assert (ports.low, ports.high) == expected
    assert tftp.server.PortRange.parse(str(ports)) == ports and tftp.server.PortRange.try_parse(text) == ports


@pytest.mark.parametrize(
    "text",
    ["", "10", "9:8", "0:5", "1:70000", "a:b", "+5:9", "5_0:90", "5:", ":5", "1:2:3", "1:-2", " 5:9", "५:९"],
)
def test_port_range_parse_refuses(text):
    with pytest.raises(tftp.TFTPValueError):
        tftp.server.PortRange.parse(text)
    assert tftp.server.PortRange.try_parse(text) is None
    assert tftp.server.PortRange.try_parse(text, default="x") == "x"


def test_port_range_parse_wants_text():
    for bad in (None, 5, b"1:2", (1, 2)):
        with pytest.raises(TypeError):
            tftp.server.PortRange.parse(bad)
        with pytest.raises(TypeError):
            tftp.server.PortRange.try_parse(bad)


def test_port_range_like_forms():
    from tftp.server._session import as_port_range  # internal: no public name

    want = tftp.server.PortRange(10, 12)
    assert as_port_range(None) is None and as_port_range(want) is want
    for like in ((10, 12), [10, 12], range(10, 13), "10:12"):
        assert as_port_range(like) == want
    with pytest.raises(tftp.TFTPValueError):
        as_port_range(range(5, 5))
    with pytest.raises(tftp.TFTPValueError):
        as_port_range(range(5, 9, 2))
    with pytest.raises(tftp.TFTPValueError):
        as_port_range((9, 8))
    for bad in (5, (1, 2, 3), b"1:2", 1.5):
        with pytest.raises(TypeError):
            as_port_range(bad)


def test_the_allocator_walks_round_robin_and_belongs_to_whoever_holds_it():
    from tftp.server._session import PortAllocator  # internal: no public name

    ports = tftp.server.PortRange(5, 7)
    first, second = PortAllocator(ports), PortAllocator(ports)
    assert first.ordered() == [5, 6, 7]
    first.taken(5)
    assert first.ordered() == [6, 7, 5]
    first.taken(7)
    assert first.ordered() == [5, 6, 7]  # wraps after the last port
    assert second.ordered() == [5, 6, 7]  # the other one did not move
    assert ports == tftp.server.PortRange(5, 7)


def _results_after(results, count, seconds=5.0):
    deadline = time.monotonic() + seconds  # the server reports after the client returns
    while len(results) < count and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(results) == count


def test_two_servers_given_one_range_do_not_share_a_cursor(root, make_server):
    ports = free_ports(3)
    shared = tftp.server.PortRange(ports[0], ports[-1])
    one_results, other_results = [], []
    one = make_server(root, port_range=shared, on_complete=one_results.append)
    other = make_server(root, port_range=shared, on_complete=other_results.append)
    assert one.port_range is shared and other.port_range is shared
    client_for(one).get("one.bin")
    _results_after(one_results, 1)
    client_for(other).get("one.bin")
    _results_after(other_results, 1)
    # Each starts at the bottom of the range: the first transfer of the second
    # server did not continue from where the first server stopped.
    assert one_results[0].local[1] == ports[0]
    assert other_results[0].local[1] == ports[0]
    client_for(one).get("one.bin")
    _results_after(one_results, 2)
    assert one_results[1].local[1] == ports[1]


def test_transfers_use_ports_from_the_range(root, make_server):
    ports = free_ports(3)
    results = []
    server = make_server(root, port_range=(ports[0], ports[-1]), on_complete=results.append)
    client = client_for(server)
    for _ in range(4):
        assert client.get("one.bin") == (root / "one.bin").read_bytes()
    deadline = time.monotonic() + 3  # the server reports after the client returns
    while len(results) < 4 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(results) == 4 and all(r.local[1] in ports for r in results)
    # Round-robin: consecutive transfers do not reuse the port just released.
    assert results[0].local[1] != results[1].local[1]


def test_full_range_answers_busy(root, make_server):
    ports = free_ports(2)
    server = make_server(root, port_range=ports)
    held = []
    try:
        for port in ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            held.append(sock)
            sock.bind(("127.0.0.1", port))
        with pytest.raises(tftp.RemoteError, match="busy"):
            client_for(server, retries=0, fallback=False).get("one.bin")
        assert wait_until(lambda: server.stats_snapshot()["refused"] == 1)
    finally:
        for sock in held:
            sock.close()
    assert client_for(server).get("one.bin") == (root / "one.bin").read_bytes()


def test_relay_port_range(root, make_server):
    ports = free_ports(4)
    upstream = make_server(root)
    events = []
    relay = TFTPRelay(
        ("127.0.0.1", upstream.server_address[1]),
        host="127.0.0.1",
        port=0,
        port_range=ports,
        trace=events.append,
    ).start()
    try:
        assert client_for(relay).get("one.bin") == (root / "one.bin").read_bytes()
    finally:
        relay.close()
    sent = [e for e in events if e.direction == "out"]
    assert sent and all(e.local[1] in ports for e in sent)


def test_v4_client_of_dual_stack_listener_without_pktinfo(root, make_server):
    """No destination address: the reply must still reach a v4 client (netimps
    answers it from a v4 socket, at Datagram.reply_address)."""
    server = make_server(root, "::", reply_from_request_address=False)
    if not server.is_dual_stack:
        pytest.skip("no dual-stack listener here")
    assert not server.has_pktinfo
    client = tftp.TFTPClient("127.0.0.1", server.server_address[1], timeout=0.5, retries=1)
    assert client.get("one.bin") == b"x"
    ports = free_ports(2)
    ranged = make_server(root, "::", reply_from_request_address=False, port_range=ports)
    assert tftp.TFTPClient("127.0.0.1", ranged.server_address[1], timeout=0.5).get("one.bin") == b"x"
