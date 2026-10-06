"""A send the host refuses ends that transfer, with the reason, on every server and client.

The stand-in socket refuses what a host cannot send (``EMSGSIZE``) from a
chosen datagram on, so a transfer of full 512-octet blocks (516-octet
datagrams) fails at a size every platform can send.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import socket
import sys
import time

import pytest

import tftp
from conftest import client_for, refusing
from tftp import AsyncTFTPClient, AsyncTFTPServer, TFTPOpcode, decode
from tftp.packet import encode_ack, encode_request

REASON = "refused by the stand-in"


def _wrap_first_reply_socket(server, **kwargs):
    """Make the first transfer socket the server opens a refusing one; later ones are plain."""
    original = server._listener.reply_socket
    wrapped = []

    def reply_socket(arrival, ports=None):
        sock, peer = original(arrival, ports)
        if not wrapped:
            sock = refusing(sock, **kwargs)
            wrapped.append(sock)
        return sock, peer

    server._listener.reply_socket = reply_socket
    return wrapped


def _drive(raw: socket.socket, address, name: str):
    """Request ``name``, acknowledge every DATA, and return the packets seen up to an ERROR or silence."""
    seen = []
    raw.sendto(encode_request(TFTPOpcode.RRQ, name), address)
    while True:
        try:
            data, tid = raw.recvfrom(2048)
        except socket.timeout:
            return seen
        packet = decode(data)
        seen.append(packet)
        if isinstance(packet, tftp.ErrorPacket):
            return seen
        if isinstance(packet, tftp.DataPacket):
            raw.sendto(encode_ack(packet.block), tid)


def _raw() -> socket.socket:
    raw = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    raw.bind(("127.0.0.1", 0))
    raw.settimeout(5)
    return raw


def _settled(stats):
    """The served transfer is counted and released: the two counters move one after the other."""
    return stats["completed"] >= 1 and stats["active"] == 0


@pytest.mark.parametrize("allowed", [0, 1], ids=["first_data", "later_data"])
def test_a_send_the_host_refuses_ends_that_transfer_and_not_the_server(root, make_server, caplog, allowed):
    results = []
    server = make_server(root, on_complete=results.append)
    wrapped = _wrap_first_reply_socket(server, allowed=allowed)
    with caplog.at_level(logging.INFO, logger="tftp.server"):
        with _raw() as raw:
            seen = _drive(raw, server.server_address, "1428x3.bin")
        assert isinstance(seen[-1], tftp.ErrorPacket), seen
        assert sum(isinstance(p, tftp.DataPacket) for p in seen) == allowed
        assert client_for(server).get("one.bin") == b"x"  # the next transfer is served
    assert len(wrapped) == 1 and len(results) >= 1
    failed = results[0]
    assert not failed.is_ok and failed.bytes <= 512 * (allowed + 1)
    assert any(REASON in message for message in caplog.messages), caplog.messages
    deadline = time.monotonic() + 5
    while not _settled(server.stats_snapshot()) and time.monotonic() < deadline:
        time.sleep(0.01)
    stats = server.stats_snapshot()
    assert stats["completed"] == 1 and stats["active"] == 0


def test_the_async_server_ends_a_transfer_whose_send_the_host_refuses(root, caplog):
    results = []

    async def main():
        server = AsyncTFTPServer(str(root), host="127.0.0.1", port=0, timeout=0.5, on_complete=results.append)
        async with server:
            await server.start()
            _wrap_first_reply_socket(server, allowed=1)
            loop = asyncio.get_running_loop()
            raw = _raw()
            try:
                seen = await loop.run_in_executor(None, _drive, raw, server.server_address, "1428x3.bin")
            finally:
                raw.close()
            assert isinstance(seen[-1], tftp.ErrorPacket), seen
            assert sum(isinstance(p, tftp.DataPacket) for p in seen) == 1
            client = AsyncTFTPClient("127.0.0.1", server.server_address[1], timeout=0.5)
            assert await client.get("one.bin") == b"x"
            deadline = time.monotonic() + 5
            while not _settled(server.stats_snapshot()) and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            return server.stats_snapshot()

    with caplog.at_level(logging.INFO, logger="tftp.server"):
        stats = asyncio.run(asyncio.wait_for(main(), 30))
    assert not results[0].is_ok and results[0].bytes <= 1024
    assert any(REASON in message for message in caplog.messages), caplog.messages
    assert stats["completed"] == 1 and stats["active"] == 0


def test_the_blocking_client_raises_the_error_of_a_send_the_host_refuses(root, make_server):
    server = make_server(root, writable=True)
    client = client_for(server)
    original = client._socket
    client._socket = lambda family: refusing(original(family), allowed=0)
    with pytest.raises(OSError) as info:
        client.put("refused.bin", b"x" * 2000)
    assert info.value.errno == errno.EMSGSIZE
    assert not (root / "refused.bin").exists()
    client._socket = original
    client.put("fine.bin", b"x" * 2000)  # the server went on
    assert (root / "fine.bin").read_bytes() == b"x" * 2000


def test_the_asyncio_client_raises_the_error_of_a_send_the_host_refuses(root, make_server):
    """Only a selector loop sends through ``socket.sendto``, where the stand-in can refuse."""
    import netimps

    server = make_server(root, writable=True)
    plain = netimps.bind

    def bind(*args, **kwargs):
        return refusing(plain(*args, **kwargs), allowed=0)

    async def main():
        client = AsyncTFTPClient("127.0.0.1", server.server_address[1], timeout=0.5, retries=2)
        netimps.bind = bind
        try:
            with pytest.raises(OSError) as info:
                await client.put("refused.bin", b"x" * 2000)
        finally:
            netimps.bind = plain
        assert info.value.errno == errno.EMSGSIZE
        await client.put("fine.bin", b"x" * 2000)

    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    loop = factory()
    try:
        loop.run_until_complete(asyncio.wait_for(main(), 30))
    finally:
        loop.close()
    assert not (root / "refused.bin").exists()
    assert (root / "fine.bin").read_bytes() == b"x" * 2000
