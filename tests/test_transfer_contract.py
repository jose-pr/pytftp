"""One set of transfer scenarios, run for each server and each client in every pairing.

The servers are the selector-driven ``TFTPServer`` and the asyncio
``AsyncTFTPServer``; the clients are ``TFTPClient`` and ``AsyncTFTPClient``,
the latter on each event loop Windows has. The judge is the file the server
holds and the octets the client received.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

import tftp
from conftest import run_async

CLIENTS = [("sync", None)]
if hasattr(asyncio, "ProactorEventLoop"):
    CLIENTS += [("async-selector", asyncio.SelectorEventLoop), ("async-proactor", asyncio.ProactorEventLoop)]
else:
    CLIENTS += [("async", None)]


class AsyncServerThread:
    """An ``AsyncTFTPServer`` on its own loop in a thread, so a synchronous test can drive it."""

    def __init__(self, directory):
        self.directory = directory
        self.ready = threading.Event()
        self.error = None
        self.address = None
        self.thread = threading.Thread(target=self._run, name="async-server", daemon=True)
        self.thread.start()
        assert self.ready.wait(10) and self.error is None, self.error

    def _run(self):
        try:
            asyncio.run(self._main())
        except BaseException as exc:  # reported to the test through ``error``
            self.error = exc
            self.ready.set()

    async def _main(self):
        self.loop = asyncio.get_running_loop()
        self.stop = asyncio.Event()
        async with tftp.AsyncTFTPServer(
            str(self.directory), host="127.0.0.1", port=0, writable=True, overwrite=True, timeout=0.5
        ) as server:
            await server.start()
            self.server = server
            self.address = server.server_address
            self.ready.set()
            await self.stop.wait()

    def close(self):
        self.loop.call_soon_threadsafe(self.stop.set)
        self.thread.join(10)


@pytest.fixture(params=["sync-server", "async-server"])
def server_port(request, root, make_server):
    """The port of a writable server over ``root``, of the kind under test."""
    if request.param == "sync-server":
        yield make_server(root, writable=True, overwrite=True).server_address[1]
        return
    thread = AsyncServerThread(root)
    yield thread.address[1]
    thread.close()


class Driver:
    """One client, called the same way whichever kind it is."""

    def __init__(self, kind, loop_factory, port, **options):
        options.setdefault("timeout", 0.5)
        options.setdefault("retries", 3)
        self.loop_factory = loop_factory
        self.is_async = kind != "sync"
        cls = tftp.AsyncTFTPClient if self.is_async else tftp.TFTPClient
        self.client = cls("127.0.0.1", port, **options)

    def call(self, method, *args, **kwargs):
        result = getattr(self.client, method)(*args, **kwargs)
        if self.is_async:
            return run_async(result, self.loop_factory, timeout=60)
        return result


@pytest.fixture(params=CLIENTS, ids=[kind for kind, _ in CLIENTS])
def driver(request, server_port):
    kind, factory = request.param

    def make(**options):
        return Driver(kind, factory, server_port, **options)

    return make


SHAPES = [(512, 1), (8, 1), (1428, 16)]


@pytest.mark.parametrize("name", ["empty.bin", "512.bin", "513.bin", "1428x3.bin"])
@pytest.mark.parametrize("blksize, windowsize", SHAPES)
def test_a_download_is_the_file_whatever_the_block_and_window(
    driver, root, tmp_path, name, blksize, windowsize
):
    expected = (root / name).read_bytes()
    result = driver(blksize=blksize, windowsize=windowsize).call("download", name, str(tmp_path / "out"))
    assert (tmp_path / "out").read_bytes() == expected
    assert result.bytes == len(expected) and result.is_ok
    assert result.negotiated.blksize == blksize and result.negotiated.windowsize == windowsize
    assert result.blocks == len(expected) // blksize + 1


@pytest.mark.parametrize("size", [0, 1, 513, 5000])
@pytest.mark.parametrize("blksize, windowsize", SHAPES)
def test_an_upload_is_the_file_the_server_holds(driver, root, size, blksize, windowsize):
    data = bytes((n * 31) % 251 for n in range(size))
    result = driver(blksize=blksize, windowsize=windowsize).call("put", "up-%d.bin" % size, data)
    assert (root / ("up-%d.bin" % size)).read_bytes() == data
    assert result.bytes == size and result.is_ok


def test_a_file_that_is_not_there_is_file_not_found(driver):
    with pytest.raises(tftp.FileNotFound):
        driver().call("get", "missing.bin")


@pytest.mark.parametrize("name", ["../outside.bin", "sub/../../outside.bin"])
def test_a_name_that_climbs_out_of_the_root_is_refused(driver, name):
    with pytest.raises(tftp.RemoteError) as info:
        driver().call("get", name)
    assert info.value.code in (tftp.TFTPErrorCode.ACCESS_VIOLATION, tftp.TFTPErrorCode.FILE_NOT_FOUND)


def test_the_size_probe_says_the_size_and_moves_no_data(driver, root):
    assert driver().call("size", "513.bin") == 513


def test_netascii_moves_the_file_unchanged_in_both_directions(driver, root, tmp_path):
    from tftp.netascii import encode

    text = (root / "text.txt").read_bytes()
    result = driver(blksize=8).call("download", "text.txt", str(tmp_path / "out"), mode="netascii")
    assert (tmp_path / "out").read_bytes() == text and result.bytes == len(encode(text))
    driver().call("put", "text-up.txt", text, mode="netascii")
    assert (root / "text-up.txt").read_bytes() == text


def test_a_large_download_arrives_whole_with_a_window(driver, root, tmp_path):
    result = driver(blksize=1428, windowsize=16).call("download", "big.bin", str(tmp_path / "out"))
    assert (tmp_path / "out").read_bytes() == (root / "big.bin").read_bytes() and result.retransmits == 0
