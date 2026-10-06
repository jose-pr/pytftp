"""Both clients and the server through a path that loses, repeats, delays and reorders datagrams.

The schedule is fixed by a seed and printed in every failure message; the
judge is the transfer's octets, and the datagrams the forwarder counted bound
the retransmissions. Every wait has a deadline, so a hung transfer fails in
seconds with the seed in the message.
"""

from __future__ import annotations

import random

import pytest

import tftp
from conftest import run_async

BLKSIZE = 256
BLOCKS = 40
SIZE = BLKSIZE * (BLOCKS - 1) + 100  # the last block is a short one
TIMEOUT = 0.12
DEADLINE = 30
FAULTS = 4
#: Window size, seed and the datagram of the start each schedule also loses once. Each transfer waits out
#: its retransmission timers (0.12 s each) by design, so these take a fraction of a second each.
SCHEDULES = [(1, 11, "request"), (4, 12, "oack"), (16, 13, "ack0")]


def payload(seed: int) -> bytes:
    return random.Random("payload-%d" % seed).randbytes(SIZE)


@pytest.fixture
def served(tmp_path, make_server):
    """``(server, directory)``: a writable server over an empty directory, with a short timeout."""
    directory = tmp_path / "lossy"
    directory.mkdir()
    return make_server(directory, writable=True, overwrite=True, timeout=TIMEOUT), directory


def _options(windowsize):
    return dict(blksize=BLKSIZE, windowsize=windowsize, timeout=TIMEOUT, retries=8, deadline=DEADLINE)


def _bound(windowsize):
    """DATA datagrams one direction may carry: every block once, and three windows again for each fault.

    A timer that fires on a loaded host adds a window of its own, so the bound has room for a few; a
    retransmission storm (each duplicate answered with another window) is far above it.
    """
    return BLOCKS + (FAULTS + 2) * 3 * (windowsize + 1) + windowsize


def _judge(lossy, direction, windowsize, data, got, label):
    where = "%s, %s, windowsize %d" % (lossy.describe(), label, windowsize)
    assert got == data, "octets differ: " + where
    toward = "client" if direction == "download" else "server"
    sent = lossy.counts.get((toward, 3), 0)
    assert BLOCKS <= sent <= _bound(windowsize), "DATA datagrams %d: %s" % (sent, where)


@pytest.mark.parametrize("windowsize, seed, handshake", SCHEDULES)
@pytest.mark.parametrize("direction", ["download", "upload"])
def test_the_synchronous_client_moves_the_octets_through_a_lossy_path(
    served, lossy_path, direction, seed, windowsize, handshake
):
    server, directory = served
    data = payload(seed)
    (directory / "down.bin").write_bytes(data)
    lossy = lossy_path(
        server,
        seed=seed,
        blocks=BLOCKS,
        faults=FAULTS,
        delay=TIMEOUT * 0.6,
        handshake=handshake,
        data_to="client" if direction == "download" else "server",
    )
    client = tftp.TFTPClient(*lossy.address, **_options(windowsize))
    if direction == "download":
        got = client.get("down.bin")
    else:
        client.put("up.bin", data)
        got = (directory / "up.bin").read_bytes()
    _judge(lossy, direction, windowsize, data, got, "synchronous client")


@pytest.mark.parametrize("windowsize, seed, handshake", SCHEDULES)
@pytest.mark.parametrize("direction", ["download", "upload"])
def test_the_asyncio_client_moves_the_octets_through_a_lossy_path(
    served, lossy_path, loop_factory, direction, seed, windowsize, handshake
):
    server, directory = served
    data = payload(seed)
    (directory / "down.bin").write_bytes(data)
    lossy = lossy_path(
        server,
        seed=seed,
        blocks=BLOCKS,
        faults=FAULTS,
        delay=TIMEOUT * 0.6,
        handshake=handshake,
        data_to="client" if direction == "download" else "server",
    )

    async def main():
        client = tftp.AsyncTFTPClient(*lossy.address, **_options(windowsize))
        if direction == "download":
            return await client.get("down.bin")
        await client.put("up.bin", data)
        return (directory / "up.bin").read_bytes()

    got = run_async(main(), loop_factory, timeout=DEADLINE + 10)
    _judge(lossy, direction, windowsize, data, got, "asyncio client")


@pytest.mark.parametrize(
    "junk",
    [b"\x00\x03\x00\x01" + b"x" * 4000, b"\x00", b"\xff\xff\xff\xff"],
    ids=["oversized", "short", "unknown"],
)
def test_a_stranger_sending_to_the_client_mid_transfer_is_told_it_is_unknown_and_the_transfer_is_intact(
    served, lossy_path, junk
):
    server, directory = served
    data = payload(3)
    (directory / "down.bin").write_bytes(data)
    lossy = lossy_path(server, seed=3, blocks=BLOCKS, faults=2, delay=TIMEOUT * 0.6)
    lossy.inject(junk, after_datagrams=6)
    got = tftp.TFTPClient(*lossy.address, **_options(4)).get("down.bin")
    assert got == data, lossy.describe()
    if len(junk) >= 2 and junk[1] in (3, 4):
        import time

        deadline = time.monotonic() + 5
        while not lossy.stray_replies and time.monotonic() < deadline:
            time.sleep(0.02)
        replies = [tftp.decode(reply) for reply in lossy.stray_replies]
        assert [r.code for r in replies if isinstance(r, tftp.ErrorPacket)] == [
            tftp.TFTPErrorCode.UNKNOWN_TID
        ], lossy.describe()


@pytest.mark.parametrize(
    "junk", [b"\x00\x03\x00\x01" + b"x" * 4000, b"\x00\x04\x00\x02"], ids=["oversized", "ack"]
)
def test_a_stranger_sending_to_the_asyncio_client_mid_transfer_is_told_it_is_unknown(
    served, lossy_path, loop_factory, junk
):
    server, directory = served
    data = payload(3)
    (directory / "down.bin").write_bytes(data)
    lossy = lossy_path(server, seed=3, blocks=BLOCKS, faults=2, delay=TIMEOUT * 0.6)
    lossy.inject(junk, after_datagrams=6)

    async def main():
        return await tftp.AsyncTFTPClient(*lossy.address, **_options(4)).get("down.bin")

    assert run_async(main(), loop_factory, timeout=DEADLINE + 10) == data, lossy.describe()
    import time

    deadline = time.monotonic() + 5
    while not lossy.stray_replies and time.monotonic() < deadline:
        time.sleep(0.02)
    replies = [tftp.decode(reply) for reply in lossy.stray_replies]
    assert [r.code for r in replies if isinstance(r, tftp.ErrorPacket)] == [
        tftp.TFTPErrorCode.UNKNOWN_TID
    ], lossy.describe()
