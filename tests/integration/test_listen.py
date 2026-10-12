"""`listen=` on the servers and the relays: several sockets behind one server, judged on the wire."""

from __future__ import annotations

import asyncio
import socket
import time

import netimps
import pytest

import tftp
from conftest import client_for, free_ports, needs_ipv6, wait_until
from tftp import AsyncTFTPServer, TFTPErrorCode, TFTPOpcode, decode
from tftp.packet import encode_ack, encode_request
from tftp.relay import AsyncTFTPRelay, TFTPRelay


def _port_is_free(port, family=socket.AF_INET, host="127.0.0.1"):
    with socket.socket(family, socket.SOCK_DGRAM) as probe:
        probe.bind((host, port))


def _udp(family, host):
    sock = socket.socket(family, socket.SOCK_DGRAM)
    sock.bind((host, 0))
    sock.settimeout(2.0)
    return sock


@needs_ipv6
@pytest.mark.parametrize("kind", ["sync", "async"])
def test_a_server_given_two_addresses_serves_a_transfer_on_each(root, kind):
    """Each reply comes from the address its request was sent to, which the client's socket saw."""
    sources = {}

    def exchange(port_of):
        for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
            with _udp(family, host) as client:
                client.sendto(encode_request(TFTPOpcode.RRQ, "one.bin"), (host, port_of[host]))
                data, source = client.recvfrom(2048)
                assert decode(data) == tftp.DataPacket(1, b"x")
                client.sendto(encode_ack(1), source)
                sources[host] = source[0]

    ports = free_ports(2)
    listen = ["127.0.0.1:%d" % ports[0], "[::1]:%d" % ports[1]]
    if kind == "sync":
        with tftp.TFTPServer(root, listen=listen, port=0, timeout=0.5).start() as server:
            assert [a[1] for a in server.server_addresses] == list(ports)
            exchange({"127.0.0.1": ports[0], "::1": ports[1]})
    else:

        async def main():
            async with AsyncTFTPServer(
                tftp.server.ThreadedHandler(tftp.FilesystemBackend(root)), listen=listen, port=0, timeout=0.5
            ) as server:
                await server.start()
                await asyncio.get_running_loop().run_in_executor(
                    None, exchange, {"127.0.0.1": ports[0], "::1": ports[1]}
                )

        asyncio.run(main())
    assert sources == {"127.0.0.1": "127.0.0.1", "::1": "::1"}


@needs_ipv6
def test_the_limits_count_across_the_sockets(root):
    with tftp.TFTPServer(
        root, listen=["127.0.0.1", "::1"], port=0, timeout=2.0, max_sessions=1
    ).start() as server:
        port, port6 = server.server_addresses[0][1], server.server_addresses[1][1]
        with _udp(socket.AF_INET, "127.0.0.1") as first, _udp(socket.AF_INET6, "::1") as second:
            first.sendto(
                encode_request(TFTPOpcode.RRQ, "513.bin", options={"blksize": 8}), ("127.0.0.1", port)
            )
            first.recvfrom(2048)  # an OACK or DATA: the transfer is in flight, never acknowledged
            second.sendto(encode_request(TFTPOpcode.RRQ, "one.bin"), ("::1", port6))
            error = decode(second.recvfrom(2048)[0])
            assert error.code == TFTPErrorCode.NOT_DEFINED and "busy" in error.message
            assert server.active_sessions == 1


def test_each_socket_of_a_server_is_closed_whatever_ends_it(root, monkeypatch):
    first, second = free_ports(2)
    listen = ["127.0.0.1:%d" % first, "127.0.0.1:%d" % second]
    server = tftp.TFTPServer(root, listen=listen, port=0).start()
    server.close()
    for port in (first, second):
        _port_is_free(port)

    def refuse():
        raise OSError("socketpair refused")

    monkeypatch.setattr(socket, "socketpair", refuse)  # the step after the listener is bound
    with pytest.raises(OSError, match="socketpair refused"):
        tftp.TFTPServer(root, listen=listen, port=0).start()
    for port in (first, second):
        _port_is_free(port)


def test_a_listen_that_fails_on_its_second_entry_leaves_the_first_closed(root):
    first, second = free_ports(2)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as holder:
        holder.bind(("127.0.0.1", second))
        with pytest.raises(OSError):
            tftp.TFTPServer(root, listen=["127.0.0.1:%d" % first, "127.0.0.1:%d" % second], port=0).bind()
    _port_is_free(first)


@pytest.mark.parametrize("loop_factory", [None])
def test_the_asyncio_relay_over_two_sockets_leaves_no_task_and_no_socket(loop_factory):
    first, second = free_ports(2)

    async def main():
        before = asyncio.all_tasks()
        relay = AsyncTFTPRelay(
            "127.0.0.1:9", listen=["127.0.0.1:%d" % first, "127.0.0.1:%d" % second], port=0
        )
        await relay.start()
        with _udp(socket.AF_INET, "127.0.0.1") as client:  # a request each socket takes without a route
            for port in (first, second):
                client.sendto(encode_request(TFTPOpcode.RRQ, "f"), ("127.0.0.1", port))
        await asyncio.sleep(0.2)
        await relay.aclose()
        await asyncio.sleep(0)
        return asyncio.all_tasks() - before - {asyncio.current_task()}

    assert asyncio.run(main()) == set()
    for port in (first, second):
        _port_is_free(port)


def test_the_blocking_relay_over_two_sockets_relays_on_each(root, make_server):
    upstream = make_server(root)
    first, second = free_ports(2)
    relay = TFTPRelay(
        ("127.0.0.1", upstream.server_address[1]),
        listen=["127.0.0.1:%d" % first, "127.0.0.1:%d" % second],
        port=0,
    ).start()
    try:
        for port in (first, second):
            assert tftp.TFTPClient("127.0.0.1", port, timeout=0.5).get("one.bin") == b"x"
        assert relay.stats["completed"] == 2 or wait_until(lambda: relay.stats["completed"] == 2)
    finally:
        relay.close()


def test_a_datagram_outside_an_endpoints_limit_is_dropped_before_the_server_sees_it(root):
    loopback = netimps.get_interface("127.0.0.1")
    others = [i for i in netimps.get_interfaces() if i.index and loopback and i.index != loopback.index]
    if loopback is None or not others:
        pytest.skip("needs the loopback adapter and another one")
    with tftp.TFTPServer(root, listen=loopback, port=0, timeout=0.5).start() as server:
        port = server.server_address[1]
        assert client_for(server, host="127.0.0.1").get("one.bin") == b"x"  # admitted
        requests = server.stats["requests"]
        server._listener.endpoints[0].interfaces = (others[0],)  # the limit is now another adapter
        with _udp(socket.AF_INET, "127.0.0.1") as client:
            client.settimeout(1.0)
            client.sendto(encode_request(TFTPOpcode.RRQ, "one.bin"), ("127.0.0.1", port))
            with pytest.raises(socket.timeout):
                client.recvfrom(2048)
        assert server.stats["requests"] == requests  # nothing read it as a request
        assert server._listener.not_admitted == 1


@pytest.mark.parametrize("ignore", [True, False])
def test_a_request_to_the_broadcast_address_is_ignored_when_the_server_says_so(root, ignore):
    with tftp.TFTPServer(root, listen="*", port=0, timeout=0.5, ignore_broadcast=ignore).start() as server:
        if not server.has_pktinfo:
            pytest.skip("this host reports no destination address, so a broadcast looks unicast")
        port = server.server_address[1]
        with _udp(socket.AF_INET, "0.0.0.0") as client:
            client.settimeout(1.5)
            client.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            try:
                client.sendto(encode_request(TFTPOpcode.RRQ, "one.bin"), ("255.255.255.255", port))
            except OSError as exc:
                pytest.skip("cannot broadcast from this host: %s" % exc)
            started = time.monotonic()
            try:
                data, _ = client.recvfrom(2048)
            except socket.timeout:
                data = None
            assert time.monotonic() - started < 3
        if ignore:
            assert data is None
        elif data is None:
            pytest.skip("this host does not deliver a limited broadcast to a local socket")
        else:
            assert decode(data) == tftp.DataPacket(1, b"x")
