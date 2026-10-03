"""Hosts, networks and interfaces given as objects, not only strings."""

from __future__ import annotations

import ipaddress
from types import SimpleNamespace

import netimps
import pytest

import tftp
from conftest import client_for
from tftp.backends import UpstreamHandler
from tftp.relay import Relay, Upstream, by_interface, by_subnet, upstream

LOOPBACK = ipaddress.ip_address("127.0.0.1")


@pytest.mark.parametrize(
    "host",
    [LOOPBACK, ipaddress.ip_interface("127.0.0.1/8"), netimps.Host("127.0.0.1"), "127.0.0.1"],
    ids=["address", "interface", "Host", "str"],
)
def test_client_host_forms(root, make_server, host):
    server = make_server(root)
    client = tftp.Client(host, server.server_address[1], timeout=0.5)
    assert client.get("one.bin") == b"x"
    assert client.host is host  # kept as given


def test_client_host_string_with_port(root, make_server):
    server = make_server(root)
    assert tftp.Client("127.0.0.1:%d" % server.server_address[1]).get("one.bin") == b"x"


def test_format_url_host_forms():
    assert tftp.format_url(ipaddress.ip_address("2001:db8::1"), "f", 70) == "tftp://[2001:db8::1]:70/f"
    assert tftp.format_url(ipaddress.ip_interface("10.0.0.5/24"), "f") == "tftp://10.0.0.5/f"
    assert tftp.format_url(netimps.Host("boot.lan"), "f") == "tftp://boot.lan/f"


def test_upstream_forms():
    host = netimps.Host("10.0.0.5")
    assert upstream(LOOPBACK) == Upstream("127.0.0.1", 69)
    assert upstream((host, 70)) == Upstream(host, 70)
    assert upstream("[2001:db8::1]:6969") == Upstream("2001:db8::1", 6969)


def _context(peer, local=None, index=0):
    return SimpleNamespace(peer=(peer, 1000), local_address=local, interface_index=index)


def test_by_subnet_key_forms():
    route = by_subnet(
        {
            ipaddress.ip_network("10.0.0.0/8"): "a",
            ipaddress.ip_interface("10.1.2.3/16"): "b",  # its network, 10.1.0.0/16
            ("192.0.2.0", 24): "c",
            ipaddress.ip_address("198.51.100.7"): "d",  # a /32
        }
    )
    assert route(None, _context("10.9.9.9")).host == "a"
    assert route(None, _context("::ffff:10.1.200.1")).host == "b"  # mapped, longest prefix
    assert route(None, _context("192.0.2.200")).host == "c"
    assert route(None, _context("198.51.100.7")).host == "d"
    assert route(None, _context("198.51.100.8")) is None


def test_by_interface_key_forms():
    iface = netimps.get_interfaces()[0]
    route = by_interface(
        {
            ipaddress.ip_address("192.0.2.1"): "by-address",
            ipaddress.ip_interface("192.0.2.2/24"): "by-interface-address",
            netimps.Host("192.0.2.3"): "by-host",
            iface: "by-netimps-interface",
            7777: "by-index",
        }
    )
    assert route(None, _context("x", "192.0.2.1")).host == "by-address"
    assert route(None, _context("x", "::ffff:192.0.2.2")).host == "by-interface-address"
    assert route(None, _context("x", "192.0.2.3")).host == "by-host"
    assert route(None, _context("x", None, iface.index)).host == "by-netimps-interface"
    assert route(None, _context("x", None, 7777)).host == "by-index"
    with pytest.raises(TypeError):
        by_interface({True: "x"})


def test_relay_and_proxy_to_typed_upstreams(root, make_server):
    upstream_server = make_server(root)
    port = upstream_server.server_address[1]
    relay = Relay((LOOPBACK, port), "127.0.0.1", 0).start()
    try:
        assert client_for(relay).get("one.bin") == b"x"
    finally:
        relay.close()
    proxy = make_server(UpstreamHandler((netimps.Host("127.0.0.1"), port)))
    assert client_for(proxy).get("one.bin") == b"x"


@pytest.mark.parametrize("host", [LOOPBACK, netimps.Host("127.0.0.1")], ids=["address", "Host"])
def test_server_listens_on_typed_host(root, host):
    with tftp.Server(root, host, 0, timeout=0.5).start() as server:
        assert tftp.Client("127.0.0.1", server.server_address[1], timeout=0.5).get("one.bin") == b"x"


def test_client_local_address_typed(root, make_server):
    server = make_server(root)
    client = client_for(server, local_address=(LOOPBACK, 0))
    assert client.get("one.bin") == b"x"
