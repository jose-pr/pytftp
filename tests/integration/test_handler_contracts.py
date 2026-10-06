"""One hook contract per server, each shape a caller can hand in moving real data.

The synchronous server takes plain hooks and synchronous streams; the asyncio
server takes coroutine hooks and asynchronous streams, or a synchronous handler
wrapped by ``ThreadedHandler``. Anything else is refused when the server is
built.
"""

from __future__ import annotations

import asyncio
import io
import logging

import pytest

import tftp
from conftest import client_for
from tftp import AsyncTFTPClient, AsyncTFTPServer, TFTPServer
from tftp.backends import FilesystemBackend, MemoryBackend
from tftp.server import ThreadedHandler

PAYLOAD = bytes(range(256)) * 40


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 30))


def async_client(server, **kwargs):
    kwargs.setdefault("timeout", 0.5)
    kwargs.setdefault("retries", 3)
    return AsyncTFTPClient("127.0.0.1", server.server_address[1], **kwargs)


class Recorder(io.BytesIO):
    """A sink that keeps what it received after ``close``."""

    def close(self):
        self.final = self.getvalue()
        super().close()


class SyncHandler:
    """Plain hooks and synchronous streams, the markers set."""

    opens_fast = True

    def __init__(self):
        self.uploads = {}

    def open_read(self, context):
        return io.BytesIO(PAYLOAD)

    def open_write(self, context, size):
        buffer = self.uploads[context.filename] = Recorder()
        return buffer


class SlowOpenHandler:
    """A synchronous handler that blocks while opening (no ``opens_fast``)."""

    def __init__(self):
        self.threads = []

    def open_read(self, context):
        import threading

        self.threads.append(threading.current_thread())
        return io.BytesIO(PAYLOAD)

    def open_write(self, context, size):
        raise tftp.TFTPError(tftp.TFTPErrorCode.ACCESS_VIOLATION)


class AsyncSource:
    size = len(PAYLOAD)

    def __init__(self):
        self.data = io.BytesIO(PAYLOAD)
        self.closed = False

    async def read(self, n):
        await asyncio.sleep(0)
        return self.data.read(min(n, 1000))

    async def close(self):
        self.closed = True


class AsyncSink:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    async def write(self, chunk):
        await asyncio.sleep(0)
        self.data += chunk

    async def close(self):
        self.closed = True


class CoroutineHandler:
    def __init__(self):
        self.uploads = {}

    async def open_read(self, context):
        await asyncio.sleep(0)
        return AsyncSource()

    async def open_write(self, context, size):
        sink = self.uploads[context.filename] = AsyncSink()
        return sink


def serve_async(handler, scenario, **kwargs):
    async def main():
        kwargs.setdefault("timeout", 0.5)
        async with AsyncTFTPServer(handler, host="127.0.0.1", port=0, **kwargs) as server:
            await server.start()
            await scenario(server)

    run(main())


# -- TFTPServer: plain hooks ---------------------------------------------------------------


def test_a_synchronous_handler_serves_a_synchronous_server(make_server):
    handler = SyncHandler()
    server = make_server(handler, writable=True)
    client = client_for(server)
    assert client.get("x") == PAYLOAD
    client.put("up", b"abc" * 1000)
    assert handler.uploads["up"].final == b"abc" * 1000


def test_a_handler_without_opens_fast_is_opened_in_a_worker_thread(make_server):
    import threading

    handler = SlowOpenHandler()
    server = make_server(handler)
    assert client_for(server).get("x") == PAYLOAD
    assert handler.threads and handler.threads[0] is not threading.main_thread()


def test_coroutine_hooks_are_refused_by_the_synchronous_server():
    with pytest.raises(TypeError, match="AsyncTFTPServer"):
        TFTPServer(CoroutineHandler(), host="127.0.0.1", port=0)


# -- AsyncTFTPServer: coroutine hooks and asynchronous streams ----------------------------------


def test_coroutine_hooks_and_asynchronous_streams_move_data():
    handler = CoroutineHandler()

    async def scenario(server):
        client = async_client(server, windowsize=4)
        assert await client.get("x") == PAYLOAD
        await client.upload("up", AsyncSource())
        for _ in range(100):
            if handler.uploads.get("up") is not None and handler.uploads["up"].closed:
                break
            await asyncio.sleep(0.02)

    serve_async(handler, scenario)
    assert bytes(handler.uploads["up"].data) == PAYLOAD
    assert handler.uploads["up"].closed


def test_a_coroutine_hook_can_refuse_with_a_code():
    class Refusing:
        async def open_read(self, context):
            raise tftp.TFTPError(tftp.TFTPErrorCode.FILE_NOT_FOUND, "no such thing")

        async def open_write(self, context, size):
            raise tftp.TFTPError(tftp.TFTPErrorCode.ACCESS_VIOLATION)

    async def scenario(server):
        with pytest.raises(tftp.FileNotFound):
            await async_client(server, retries=1).get("x")
        with pytest.raises(tftp.AccessViolation):
            await async_client(server, retries=1).put("x", b"1")

    serve_async(Refusing(), scenario)


# -- AsyncTFTPServer: a synchronous handler through the adapter ---------------------------------


@pytest.mark.parametrize("fast", [True, False])
def test_a_synchronous_handler_wrapped_by_the_adapter_serves_the_asyncio_server(fast):
    inner = SyncHandler() if fast else SlowOpenHandler()
    handler = ThreadedHandler(inner)

    async def scenario(server):
        assert await async_client(server).get("x") == PAYLOAD
        if fast:
            await async_client(server).put("up", b"z" * 4000)

    serve_async(handler, scenario, writable=True)
    if fast:
        assert inner.uploads["up"].final == b"z" * 4000
    else:
        import threading

        assert inner.threads and inner.threads[0] is not threading.main_thread()


def test_the_adapter_wraps_a_built_in_backend():
    async def scenario(server):
        assert await async_client(server).get("f") == b"data"

    serve_async(ThreadedHandler(MemoryBackend({"f": b"data"})), scenario)


def test_a_root_directory_needs_no_adapter(tmp_path):
    (tmp_path / "f").write_bytes(b"from disk")

    async def scenario(server):
        assert await async_client(server).get("f") == b"from disk"

    serve_async(str(tmp_path), scenario)


@pytest.mark.parametrize(
    "handler",
    [SyncHandler(), SlowOpenHandler(), MemoryBackend(), lambda: None],
    ids=["marked", "unmarked", "backend", "callable"],
)
def test_a_synchronous_handler_without_the_adapter_is_refused_at_construction(handler):
    with pytest.raises(TypeError, match="ThreadedHandler"):
        AsyncTFTPServer(handler, host="127.0.0.1", port=0)


def test_a_backend_is_refused_by_the_asyncio_server_with_the_class_named(tmp_path):
    with pytest.raises(TypeError, match="FilesystemBackend"):
        AsyncTFTPServer(FilesystemBackend(tmp_path), host="127.0.0.1", port=0)


# -- the optional members ----------------------------------------------------------------------


def test_the_markers_are_public_attributes_and_the_old_names_are_gone():
    assert MemoryBackend.opens_fast is True and FilesystemBackend.opens_fast is True
    assert tftp.AtomicWriter.copies_writes is True
    from tftp.listing import DirectoryListing

    assert DirectoryListing.lists_directories is True
    for owner in (MemoryBackend, FilesystemBackend, tftp.AtomicWriter, DirectoryListing):
        assert not [name for name in dir(owner) if name.startswith("_tftp_")]


def test_a_reader_with_read_only_and_no_optional_member_serves():
    class Bare:
        def __init__(self):
            self.data = io.BytesIO(PAYLOAD)

        def read(self, n):
            return self.data.read(n)

        def close(self):
            pass

    class Handler:
        opens_fast = True

        def open_read(self, context):
            return Bare()

        def open_write(self, context, size):
            raise tftp.TFTPError(tftp.TFTPErrorCode.ACCESS_VIOLATION)

    server = TFTPServer(Handler(), host="127.0.0.1", port=0, timeout=0.5).start()
    try:
        assert client_for(server).get("x") == PAYLOAD
    finally:
        server.close()


# -- nothing is logged at WARNING or above by a clean transfer -----------------------------------


@pytest.fixture
def server_records():
    records = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    collector = Collect(level=logging.WARNING)
    logger = logging.getLogger("tftp.server")
    previous = logger.level
    logger.addHandler(collector)
    logger.setLevel(logging.DEBUG)
    yield records
    logger.removeHandler(collector)
    logger.setLevel(previous)


def test_a_transfer_against_the_synchronous_server_logs_nothing_alarming(server_records, make_server):
    server = make_server(SyncHandler(), writable=True)
    client = client_for(server)
    assert client.get("x") == PAYLOAD
    client.put("up", b"1" * 3000)
    server.close()
    assert [r.getMessage() for r in server_records] == []


@pytest.mark.parametrize("shape", ["coroutine", "adapter"])
def test_a_transfer_against_the_asyncio_server_logs_nothing_alarming(server_records, shape):
    handler = CoroutineHandler() if shape == "coroutine" else ThreadedHandler(SyncHandler())

    async def scenario(server):
        client = async_client(server)
        assert await client.get("x") == PAYLOAD
        await client.put("up", b"1" * 3000)
        await asyncio.sleep(0.1)

    serve_async(handler, scenario, writable=True)
    assert [r.getMessage() for r in server_records] == []


def test_a_transfer_through_the_relay_logs_nothing_alarming(server_records, make_server):
    from tftp.relay import TFTPRelay

    server = make_server(SyncHandler(), writable=True)
    relay_records = []

    class Collect(logging.Handler):
        def emit(self, record):
            relay_records.append(record)

    logger = logging.getLogger("tftp.relay")
    collector = Collect(level=logging.WARNING)
    logger.addHandler(collector)
    relay = TFTPRelay(("127.0.0.1", server.server_address[1]), host="127.0.0.1", port=0).start()
    try:
        client = client_for(relay)
        assert client.get("x") == PAYLOAD
        client.put("up", b"1" * 3000)
    finally:
        relay.close()
        logger.removeHandler(collector)
    assert [r.getMessage() for r in relay_records + server_records] == []
