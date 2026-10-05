"""Hosts, networks and interfaces given as objects, not only strings."""

from __future__ import annotations

import ipaddress
from types import SimpleNamespace

import netimps
import pytest

import tftp
from conftest import client_for
from tftp.backends import UpstreamBackend
from tftp.relay import TFTPRelay, Upstream, by_interface, by_subnet

LOOPBACK = ipaddress.ip_address("127.0.0.1")


@pytest.mark.parametrize(
    "host",
    [LOOPBACK, ipaddress.ip_interface("127.0.0.1/8"), netimps.Host("127.0.0.1"), "127.0.0.1"],
    ids=["address", "interface", "Host", "str"],
)
def test_client_host_forms(root, make_server, host):
    server = make_server(root)
    client = tftp.TFTPClient(host, server.server_address[1], timeout=0.5)
    assert client.get("one.bin") == b"x"
    assert client.host is host  # kept as given


def test_client_host_string_with_port(root, make_server):
    server = make_server(root)
    assert tftp.TFTPClient("127.0.0.1:%d" % server.server_address[1]).get("one.bin") == b"x"


def test_url_host_forms():
    assert str(tftp.TFTPURL(ipaddress.ip_address("2001:db8::1"), 70, "f")) == "tftp://[2001:db8::1]:70/f"
    assert str(tftp.TFTPURL(ipaddress.ip_interface("10.0.0.5/24"), 69, "f")) == "tftp://10.0.0.5/f"
    assert str(tftp.TFTPURL(netimps.Host("boot.lan"), 69, "f")) == "tftp://boot.lan/f"


def test_upstream_forms():
    host = netimps.Host("10.0.0.5")
    assert Upstream.parse(LOOPBACK) == Upstream("127.0.0.1", 69)
    assert Upstream.parse((host, 70)) == Upstream(host, 70)
    assert Upstream.parse("[2001:db8::1]:6969") == Upstream("2001:db8::1", 6969)
    assert Upstream.parse("boot.lan:70") == Upstream("boot.lan", 70)
    assert Upstream.parse("boot.lan") == Upstream("boot.lan", 69)
    same = Upstream("h", 70)
    assert Upstream.parse(same) is same


def test_an_upstream_is_a_value():
    import copy
    import pickle

    one = Upstream("h", 70)
    assert (
        one == Upstream("h", 70)
        and hash(one) == hash(Upstream("h", 70))
        and len({one, Upstream("h", 70)}) == 1
    )
    assert one != Upstream("h", 69) and one != Upstream("g", 70) and Upstream("h") == Upstream("h", 69)
    assert one.__eq__(("h", 70)) is NotImplemented
    assert one != ("h", 70) and ("h", 70) != one and one not in {("h", 70)}
    with pytest.raises(TypeError):
        tuple(one)
    with pytest.raises(AttributeError):
        one.port = 71  # type: ignore[misc]
    assert eval(repr(one), {"Upstream": Upstream}) == one
    for clone in (copy.copy(one), copy.deepcopy(one), pickle.loads(pickle.dumps(one))):
        assert clone == one and type(clone) is Upstream


@pytest.mark.parametrize("port", [0, 65536, -1])
def test_an_upstream_port_is_checked(port):
    with pytest.raises(tftp.TFTPValueError):
        Upstream("h", port)
    with pytest.raises(tftp.TFTPValueError):
        Upstream.parse(("h", port))


@pytest.mark.parametrize("port", ["70", True, 70.0, None])
def test_an_upstream_port_is_an_int(port):
    with pytest.raises(TypeError):
        Upstream("h", port)


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
    relay = TFTPRelay((LOOPBACK, port), host="127.0.0.1", port=0).start()
    try:
        assert client_for(relay).get("one.bin") == b"x"
    finally:
        relay.close()
    proxy = make_server(UpstreamBackend((netimps.Host("127.0.0.1"), port)))
    assert client_for(proxy).get("one.bin") == b"x"


@pytest.mark.parametrize("host", [LOOPBACK, netimps.Host("127.0.0.1")], ids=["address", "Host"])
def test_server_listens_on_typed_host(root, host):
    with tftp.TFTPServer(root, host=host, port=0, timeout=0.5).start() as server:
        assert tftp.TFTPClient("127.0.0.1", server.server_address[1], timeout=0.5).get("one.bin") == b"x"


def test_client_local_address_typed(root, make_server):
    server = make_server(root)
    client = client_for(server, src=(LOOPBACK, 0))
    assert client.get("one.bin") == b"x"


# -- listening on one adapter ------------------------------------------------------


def _loopback():
    iface = netimps.get_interface("127.0.0.1")
    if iface is None:
        pytest.skip("no loopback interface reported")
    return iface


@pytest.mark.parametrize("form", ["Interface", "name", "address"])
def test_server_on_one_interface(root, form):
    iface = _loopback()
    spec = {"Interface": iface, "name": iface.name, "address": "127.0.0.1"}[form]
    # IPv4 by default: the adapter's primary address, or the one named.
    expected = "127.0.0.1" if form == "address" else str(iface.primary_ip(ipv6=False).ip)
    with tftp.TFTPServer(root, port=0, interface=spec, timeout=0.5).start() as server:
        host, port = server.server_address[:2]
        assert host == expected
        assert tftp.TFTPClient(host, port, timeout=0.5).get("one.bin") == b"x"


def test_interface_family_follows_a_wildcard_host(root):
    iface = _loopback()
    with tftp.TFTPServer(root, host="0.0.0.0", port=0, interface=iface).start() as server:
        assert server.server_address[0] == str(iface.primary_ip(ipv6=False).ip)
    v6 = iface.primary_ip(ipv6=True)
    if v6 is None:
        pytest.skip("loopback has no IPv6 address here")
    with tftp.TFTPServer(root, host="::", port=0, interface=iface, timeout=0.5).start() as server:
        host, port = server.server_address[:2]
        assert ipaddress.ip_address(host.split("%")[0]) == v6.ip
        assert tftp.TFTPClient(host, port, timeout=0.5).get("one.bin") == b"x"


def test_interface_errors(root):
    with pytest.raises(ValueError):
        tftp.TFTPServer(root, host="127.0.0.1", port=0, interface=_loopback())  # host or interface
    with pytest.raises(ValueError):
        tftp.TFTPServer(root, port=0, interface="no-such-adapter-xyz")


def test_relay_on_one_interface(root, make_server):
    upstream_server = make_server(root)
    relay = TFTPRelay((LOOPBACK, upstream_server.server_address[1]), port=0, interface=_loopback()).start()
    try:
        assert relay.server_address[0] == str(_loopback().primary_ip(ipv6=False).ip)
        assert client_for(relay).get("one.bin") == b"x"
    finally:
        relay.close()
