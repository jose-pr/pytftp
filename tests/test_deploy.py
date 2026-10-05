"""Deployment features: transfer port ranges."""

from __future__ import annotations

import socket
import time

import pytest

import tftp
from tftp.relay import TFTPRelay
from conftest import client_for


def _free_ports(count: int) -> range:
    """``count`` consecutive ports that were free a moment ago."""
    for start in range(40000, 60000, 97):
        held = []
        try:
            for port in range(start, start + count):
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                held.append(sock)
                sock.bind(("127.0.0.1", port))
        except OSError:
            continue
        finally:
            for sock in held:
                sock.close()
        return range(start, start + count)
    pytest.skip("no free port block")


def test_port_range_values():
    assert tftp.PortRange.of(None) is None
    assert (tftp.PortRange.of((10, 12)).low, tftp.PortRange.of(range(10, 13)).high) == (10, 12)
    ports = tftp.PortRange(5, 7)
    assert list(ports) == [5, 6, 7] and len(ports) == 3
    assert next(iter(ports)) == 5  # wraps round after a full pass
    for bad in ((0, 5), (9, 8), (1, 70000)):
        with pytest.raises(ValueError):
            tftp.PortRange(*bad)
    with pytest.raises(ValueError):
        tftp.PortRange.of(range(5, 5))


def test_transfers_use_ports_from_the_range(root, make_server):
    ports = _free_ports(3)
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
    ports = _free_ports(2)
    server = make_server(root, port_range=ports)
    held = []
    try:
        for port in ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            held.append(sock)
            sock.bind(("127.0.0.1", port))
        with pytest.raises(tftp.RemoteError, match="busy"):
            client_for(server, retries=0).get("one.bin")
        assert server.stats_snapshot()["refused"] == 1
    finally:
        for sock in held:
            sock.close()
    assert client_for(server).get("one.bin") == (root / "one.bin").read_bytes()


def test_relay_port_range(root, make_server):
    ports = _free_ports(4)
    upstream = make_server(root)
    events = []
    relay = TFTPRelay(
        ("127.0.0.1", upstream.server_address[1]), "127.0.0.1", 0, port_range=ports, trace=events.append
    ).start()
    try:
        assert client_for(relay).get("one.bin") == (root / "one.bin").read_bytes()
    finally:
        relay.close()
    sent = [e for e in events if e.direction == "out"]
    assert sent and all(e.local[1] in ports for e in sent)


# -- pytftp serve's handler wrappers (cli extra) ---------------------------------


@pytest.fixture
def handlers():
    pytest.importorskip("duho")
    from tftp.cli import handlers

    return handlers


def test_remap_rewrites_the_first_matching_rule(root, make_server, handlers):
    rules = [handlers.parse_rule(r"^/?pxelinux/="), handlers.parse_rule(r"\.BIN$=.bin")]
    server = make_server(handlers.Remap(tftp.FilesystemBackend(root), rules))
    client = client_for(server)
    assert client.get("/pxelinux/one.bin") == b"x"
    assert client.get("one.BIN") == b"x"
    with pytest.raises(ValueError):
        handlers.parse_rule("no-equals")
    with pytest.raises(ValueError):
        handlers.parse_rule("(=x")


def test_per_client_root(root, make_server, handlers):
    (root / "127.0.0.1").mkdir()
    (root / "127.0.0.1" / "one.bin").write_bytes(b"mine")
    make = lambda d: tftp.FilesystemBackend(d, writable=True)  # noqa: E731
    server = make_server(handlers.PerClient(str(root), make))
    assert client_for(server).get("one.bin") == b"mine"
    with pytest.raises(tftp.FileNotFound):
        client_for(server).get("513.bin")  # only the client's own directory
    client_for(server).put("up.bin", b"u")
    assert (root / "127.0.0.1" / "up.bin").read_bytes() == b"u"
    assert handlers.client_directory(("::ffff:10.0.0.1", 1)) == "10.0.0.1"
    assert handlers.client_directory(("fe80::1%3", 1)) == "fe80--1"


def test_per_client_fallback(root, make_server, handlers):
    make = lambda d: tftp.FilesystemBackend(d)  # noqa: E731
    assert client_for(make_server(handlers.PerClient(str(root), make))).get("one.bin") == b"x"
    with pytest.raises(tftp.FileNotFound):
        client_for(make_server(handlers.PerClient(str(root), make, fallback=False))).get("one.bin")


def test_case_insensitive_lookup(root, make_server, handlers):
    (root / "Boot").mkdir()
    (root / "Boot" / "BCD").write_bytes(b"bcd")
    server = make_server(handlers.CaseInsensitive(root, writable=True))
    client = client_for(server)
    assert client.get("\\boot\\bcd") == b"bcd"
    assert client.get("ONE.BIN") == b"x"
    client.put("boot/New.Cfg", b"n")
    assert (root / "Boot" / "New.Cfg").read_bytes() == b"n"
    with pytest.raises(tftp.AccessViolation):
        client.get("../BOOT/bcd")


def test_v4_client_of_dual_stack_listener_without_pktinfo(root, make_server):
    """No destination address: the reply must still reach a v4 client (netimps
    answers it from a v4 socket, at Datagram.reply_address)."""
    server = make_server(root, "::", reply_from_request_address=False)
    if not server.dual_stack:
        pytest.skip("no dual-stack listener here")
    assert not server.supports_pktinfo
    client = tftp.TFTPClient("127.0.0.1", server.server_address[1], timeout=0.5, retries=1)
    assert client.get("one.bin") == b"x"
    ports = _free_ports(2)
    ranged = make_server(root, "::", reply_from_request_address=False, port_range=ports)
    assert tftp.TFTPClient("127.0.0.1", ranged.server_address[1], timeout=0.5).get("one.bin") == b"x"
