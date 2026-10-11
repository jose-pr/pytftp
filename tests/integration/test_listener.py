"""The listening sockets of a server: one endpoint, several, and what each does with a datagram."""

from __future__ import annotations

import errno
import socket

import netimps
import pytest

from conftest import free_ports, needs_ipv6, raw_socket
from tftp import TFTPErrorCode, TFTPOpcode, decode
from tftp.packet import encode_request
from tftp.server._listener import Listener  # internal: no public name

REQUEST = encode_request(TFTPOpcode.RRQ, "one.bin")


def _poll(listener, count=1, timeout=2.0):
    """The first ``count`` arrivals the listener yields within ``timeout`` seconds."""
    import time

    found = []
    deadline = time.monotonic() + timeout
    while len(found) < count and time.monotonic() < deadline:
        arrival = listener.recv()
        if arrival is None:
            time.sleep(0.005)
        else:
            found.append(arrival)
    return found


def test_one_endpoint_reports_where_the_datagram_was_sent():
    listener = Listener("127.0.0.1", 0)
    try:
        assert len(listener.endpoints) == 1
        port = listener.addresses[0][1]
        with raw_socket() as client:
            client.sendto(REQUEST, ("127.0.0.1", port))
            (arrival,) = _poll(listener)
            assert arrival.sender == client.getsockname()
            assert arrival.local == "127.0.0.1"
            assert arrival.ifindex > 0  # an address-bound socket still reports its arrival interface
            assert arrival.endpoint is listener.endpoints[0]
    finally:
        listener.close()


def test_two_endpoints_answer_from_the_socket_the_request_reached():
    first, second = free_ports(2)
    listener = Listener(["127.0.0.1:%d" % first, "127.0.0.1:%d" % second], 0)
    try:
        assert [address[1] for address in listener.addresses] == [first, second]
        with raw_socket() as one, raw_socket() as two:
            one.sendto(b"\x00\x09junk", ("127.0.0.1", second))  # not a request: only the socket matters
            two.sendto(b"\x00\x09junk", ("127.0.0.1", first))
            arrivals = _poll(listener, 2)
            by_sender = {arrival.sender: arrival for arrival in arrivals}
            assert by_sender[one.getsockname()].endpoint is listener.endpoints[1]
            assert by_sender[two.getsockname()].endpoint is listener.endpoints[0]
            for client, arrival in ((one, by_sender[one.getsockname()]), (two, by_sender[two.getsockname()])):
                listener.reply_error(arrival, TFTPErrorCode.NOT_DEFINED, "x")
                data, source = client.recvfrom(2048)
                assert decode(data).code == TFTPErrorCode.NOT_DEFINED
                assert (
                    source[1] == arrival.endpoint.socket.getsockname()[1]
                )  # the wire: the port it was sent to
    finally:
        listener.close()


def test_a_busy_endpoint_does_not_starve_the_other():
    first, second = free_ports(2)
    listener = Listener(["127.0.0.1:%d" % first, "127.0.0.1:%d" % second], 0)
    try:
        with raw_socket() as busy, raw_socket() as quiet:
            for _ in range(8):
                busy.sendto(b"\x00\x09a", ("127.0.0.1", first))
            quiet.sendto(b"\x00\x09b", ("127.0.0.1", second))
            arrivals = _poll(listener, 9)
            assert len(arrivals) == 9
            position = [arrival.sender for arrival in arrivals].index(quiet.getsockname())
            assert position <= 1  # taken in turn, not after the first endpoint's eight
    finally:
        listener.close()


def test_a_datagram_a_limited_endpoint_does_not_admit_is_dropped_and_counted():
    loopback = netimps.get_interface("127.0.0.1")
    others = [i for i in netimps.get_interfaces() if i.index and loopback and i.index != loopback.index]
    if loopback is None or not others:
        pytest.skip("needs the loopback adapter and another one")
    listener = Listener(loopback, 0)
    try:
        endpoint = listener.endpoints[0]
        assert endpoint.interfaces  # the limit travels with the endpoint
        port = listener.addresses[0][1]
        with raw_socket() as client:
            client.sendto(REQUEST, ("127.0.0.1", port))
            assert len(_poll(listener)) == 1  # loopback is admitted
            endpoint.interfaces = (others[0],)
            client.sendto(REQUEST, ("127.0.0.1", port))
            assert _poll(listener, timeout=0.5) == []
            assert listener.not_admitted == 1
    finally:
        listener.close()


def test_close_closes_every_endpoint_even_when_one_fails():
    first, second = free_ports(2)
    listener = Listener(["127.0.0.1:%d" % first, "127.0.0.1:%d" % second], 0)
    sockets = [endpoint.socket for endpoint in listener.endpoints]

    class Refusing:
        def __init__(self, endpoint):
            self.endpoint = endpoint

        def close(self):
            raise OSError("closing refused")

    held = listener.endpoints[0]
    listener.endpoints = (Refusing(held), listener.endpoints[1])
    with pytest.raises(OSError, match="closing refused"):
        listener.close()
    assert sockets[1].fileno() == -1  # the one after the failure was still closed
    held.close()


def test_a_listener_that_cannot_finish_closes_every_socket(monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("endpoint refused")

    monkeypatch.setattr(netimps, "UDPEndpoint", refuse)
    first, second = free_ports(2)
    with pytest.raises(OSError, match="endpoint refused"):
        Listener(["127.0.0.1:%d" % first, "127.0.0.1:%d" % second], 0)
    for port in (first, second):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", port))  # free again


@needs_ipv6
def test_no_listen_is_both_families_on_one_port():
    listener = Listener(None, 0)
    try:
        assert [e.socket.family for e in listener.endpoints] == [socket.AF_INET, socket.AF_INET6]
        assert len({address[1] for address in listener.addresses}) == 1
        port = listener.addresses[0][1]
        with raw_socket() as v4, socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as v6:
            v6.bind(("::1", 0))
            v4.sendto(b"\x00\x09a", ("127.0.0.1", port))
            v6.sendto(b"\x00\x09b", ("::1", port))
            arrivals = _poll(listener, 2)
            assert sorted(a.endpoint.socket.family for a in arrivals) == [socket.AF_INET, socket.AF_INET6]
            assert all(a.sender[0] in ("127.0.0.1", "::1") for a in arrivals)  # no v4-mapped sender
    finally:
        listener.close()


def test_no_listen_on_a_host_without_ipv6_is_the_ipv4_socket_alone(monkeypatch):
    real = netimps.bind_listen

    def bind_listen(listen=None, *args, **kwargs):
        if listen == "::":
            raise OSError(errno.EAFNOSUPPORT, "no IPv6 here")
        return real(listen, *args, **kwargs)

    monkeypatch.setattr(netimps, "bind_listen", bind_listen)
    listener = Listener(None, 0)
    try:
        assert [e.socket.family for e in listener.endpoints] == [socket.AF_INET]
    finally:
        listener.close()


@needs_ipv6
def test_a_taken_ipv6_port_fails_the_default_listener_and_leaves_nothing_open():
    with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as holder:
        holder.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        holder.bind(("::", 0))
        port = holder.getsockname()[1]
        with pytest.raises(OSError):
            Listener(None, port)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("0.0.0.0", port))  # the IPv4 half was closed again


def test_without_packet_info_a_wildcard_endpoint_knows_no_destination():
    listener = Listener("*", 0, pktinfo=False)
    try:
        assert not listener.has_pktinfo
        port = listener.addresses[0][1]
        with raw_socket() as client:
            client.sendto(REQUEST, ("127.0.0.1", port))
            (arrival,) = _poll(listener)
            assert arrival.local is None and arrival.ifindex == 0
    finally:
        listener.close()
