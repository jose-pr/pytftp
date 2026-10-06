"""What the library does with host, port and timeout arguments it is given."""

from __future__ import annotations

import logging
import socket

import netimps
import pytest

import tftp
from conftest import client_for
from tftp._sockets import fit_window
from tftp import AsyncTFTPClient
from tftp.relay import TFTPRelay, Upstream

# -- a host name as the listen address -----------------------------------------


@pytest.mark.parametrize("host", ["localhost", netimps.Host("localhost")], ids=["str", "Host"])
def test_server_listens_on_a_host_name(root, host):
    with tftp.TFTPServer(root, host=host, port=0, timeout=0.5).start() as server:
        address, port = server.server_address[:2]
        assert socket.getaddrinfo("localhost", port, type=socket.SOCK_DGRAM)  # the name resolves
        assert tftp.TFTPClient(address, port, timeout=0.5).get("one.bin") == b"x"


def test_relay_listens_on_a_host_name(root, make_server):
    behind = make_server(root)
    relay = TFTPRelay(("127.0.0.1", behind.server_address[1]), host="localhost", port=0).start()
    try:
        assert client_for(relay).get("one.bin") == b"x"
    finally:
        relay.close()


def test_a_host_name_and_an_interface_are_refused_together(root):
    with pytest.raises(ValueError, match="host or interface"):
        tftp.TFTPServer(root, host="localhost", port=0, interface=netimps.get_interface("127.0.0.1"))


# -- max_timeout ----------------------------------------------------------------


@pytest.mark.parametrize("cls", [tftp.TFTPClient, AsyncTFTPClient])
def test_max_timeout_below_timeout_is_refused_when_the_client_is_built(cls):
    with pytest.raises(ValueError, match="max_timeout"):
        cls("127.0.0.1", 69, timeout=0.5, max_timeout=0.1)
    assert cls("127.0.0.1", 69, timeout=0.5, max_timeout=0.5).max_timeout == 0.5


# -- a name that does not resolve ---------------------------------------------------


@pytest.mark.parametrize("cls", [tftp.TFTPClient, AsyncTFTPClient])
def test_a_name_that_does_not_resolve_is_netimps_resolution_error(cls):
    # RFC 6761: ".invalid" never resolves.
    with pytest.raises(netimps.ResolutionError) as caught:
        cls("no-such-host.invalid", timeout=0.2, retries=0)._endpoint()
    assert isinstance(caught.value, OSError)
    assert not isinstance(caught.value, socket.gaierror)


def test_the_sync_client_raises_it_from_a_transfer():
    with pytest.raises(netimps.ResolutionError):
        tftp.TFTPClient("no-such-host.invalid", timeout=0.2, retries=0).get("f")


def test_the_async_client_raises_it_from_a_transfer():
    import asyncio

    async def run():
        await AsyncTFTPClient("no-such-host.invalid", timeout=0.2, retries=0).get("f")

    with pytest.raises(netimps.ResolutionError):
        asyncio.run(run())


# -- host:port text and argument types --------------------------------------------


@pytest.mark.parametrize("text", ["boot.lan:+70", "boot.lan:7_0", "[10.0.0.5]:70", "[boot.lan]:70"])
def test_upstream_refuses_malformed_host_port_text(text):
    with pytest.raises(ValueError):
        Upstream.parse(text)


def test_client_refuses_malformed_host_port_text_before_sending():
    with pytest.raises(ValueError):
        tftp.TFTPClient("127.0.0.1:+70", timeout=0.2).get("one.bin")


def test_server_refuses_a_bracketed_ipv4_address(root):
    with pytest.raises(ValueError):
        tftp.TFTPServer(root, host="[10.0.0.5]", port=0).start()


def test_a_host_that_is_not_a_host_is_a_type_error():
    with pytest.raises(TypeError):
        Upstream.parse(None)
    with pytest.raises(TypeError):
        TFTPRelay(None)
    with pytest.raises(TypeError):
        tftp.TFTPClient(None).get("one.bin")


# -- socket buffers -----------------------------------------------------------------


class _CappedSocket:
    """A socket whose kernel grants at most ``cap`` bytes per buffer."""

    def __init__(self, cap):
        self.cap = cap
        self.sizes = {}

    def getsockopt(self, level, option):
        return self.sizes.get(option, 4096)

    def setsockopt(self, level, option, value):
        self.sizes[option] = min(value, self.cap)


def test_fit_window_reports_the_grant_and_leaves_the_logging_to_netimps(caplog):
    sock = _CappedSocket(cap=100_000)
    with caplog.at_level(logging.DEBUG):
        granted = fit_window(sock, 8192, 64)
    assert granted == (100_000, 100_000)
    assert [r.name for r in caplog.records if r.levelno >= logging.DEBUG and r.name.startswith("tftp")] == []


# -- scoped addresses ---------------------------------------------------------------


def test_a_zone_is_dropped_before_an_address_is_compared():
    from types import SimpleNamespace

    from tftp.cli.handlers import client_directory
    from tftp.relay import by_interface

    assert client_directory(("fe80::1%eth0", 1000)) == "fe80--1"
    assert client_directory(("::ffff:10.0.0.7", 1000)) == "10.0.0.7"
    route = by_interface({"fe80::1": "10.9.9.9:70"})
    context = SimpleNamespace(interface_index=0, local_address="fe80::1%eth0")
    assert route(None, context) == Upstream.parse("10.9.9.9:70")


def test_fit_window_makes_room_for_one_datagram_of_the_largest_block_size():
    """The platform's own answer: a datagram of 65464 octets and its header fits both buffers."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        fit_window(sock, 65464, 1)
        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF) >= 65464 + 4
        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF) >= 65464 + 4
