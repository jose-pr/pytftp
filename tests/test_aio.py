"""asyncio client and server."""

from __future__ import annotations

import asyncio
import io
import os
import sys

import pytest

import tftp
from conftest import client_for
from tftp.aio import AsyncClient, AsyncServer
from tftp.backends import HttpHandler, MemoryHandler


def run(coro, loop_factory=None):
    if loop_factory is None:
        return asyncio.run(asyncio.wait_for(coro, 30))
    if sys.version_info >= (3, 12):
        return asyncio.run(asyncio.wait_for(coro, 30), loop_factory=loop_factory)
    loop = loop_factory()
    try:
        return loop.run_until_complete(asyncio.wait_for(coro, 30))
    finally:
        loop.close()


def async_client(server, **kwargs):
    kwargs.setdefault("timeout", 0.5)
    kwargs.setdefault("retries", 3)
    address = server.server_address
    host = address[0] if address[0] not in ("::", "0.0.0.0") else "127.0.0.1"
    return AsyncClient(host, address[1], **kwargs)


# -- AsyncClient against the threaded server -------------------------------------------------------


@pytest.mark.parametrize("shape", [{}, {"blksize": None}, {"blksize": 8192, "windowsize": 8}])
def test_async_client_download_upload(root, make_server, shape):
    server = make_server(root, writable=True)
    expected = (root / "big.bin").read_bytes()

    async def main():
        client = async_client(server, **shape)
        assert await client.get("big.bin") == expected
        result = await client.upload("aio-up.bin", expected[:70_000])
        assert result.bytes == 70_000
        assert await client.get("empty.bin") == b""
        with pytest.raises(tftp.FileNotFound):
            await client.get("missing")

    run(main())
    assert (root / "aio-up.bin").read_bytes() == expected[:70_000]


def test_async_client_concurrency(root, make_server):
    server = make_server(root)
    expected = (root / "big.bin").read_bytes()

    async def main():
        client = async_client(server, windowsize=4)
        results = await asyncio.gather(*(client.get("big.bin") for _ in range(20)))
        assert all(r == expected for r in results)

    run(main())


class SlowSink:
    """An async writer that takes its time: backpressure must hold."""

    def __init__(self):
        self.data = bytearray()

    async def write(self, chunk):
        await asyncio.sleep(0.001)
        self.data += chunk


class AsyncSource:
    def __init__(self, data, chunk=1000):
        self.data = io.BytesIO(data)
        self.chunk = chunk

    async def read(self, n):
        await asyncio.sleep(0)
        return self.data.read(min(n, self.chunk))


def test_async_sink_source_and_stream(root, make_server):
    server = make_server(root, writable=True)
    expected = (root / "big.bin").read_bytes()

    async def gen():
        for i in range(0, 50_000, 7_000):
            await asyncio.sleep(0)
            yield expected[i : min(i + 7_000, 50_000)]

    async def main():
        client = async_client(server, windowsize=4)
        sink = SlowSink()
        await client.download("big.bin", sink)
        assert bytes(sink.data) == expected
        await client.upload("from-async.bin", AsyncSource(expected[:30_000]))
        await client.upload("from-gen.bin", gen())
        chunks = [c async for c in client.stream("big.bin", buffer=8192)]
        assert b"".join(chunks) == expected

    run(main())
    assert (root / "from-async.bin").read_bytes() == expected[:30_000]
    assert (root / "from-gen.bin").read_bytes() == expected[:50_000]


def test_async_client_timeout_and_cancel(root, make_server):
    import socket

    async def main():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as silent:
            silent.bind(("127.0.0.1", 0))
            client = AsyncClient("127.0.0.1", silent.getsockname()[1], timeout=0.05, retries=2)
            with pytest.raises(tftp.TransferTimeout):
                await client.get("x")
        server = make_server(root, timeout=2)

        class Stuck:
            async def write(self, chunk):
                await asyncio.Event().wait()  # never completes

        # The download cannot finish while its sink is stuck, so the cancel lands.
        task = asyncio.ensure_future(async_client(server, blksize=None).download("big.bin", Stuck()))
        await asyncio.sleep(0.05)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(main())


def test_async_client_trace_and_fallback(root, make_server):
    from test_protocol import FakeServer

    from tftp import encode_data, encode_error

    def script(packet):
        if isinstance(packet, tftp.Request):
            return [encode_error(8)] if packet.options else [encode_data(1, b"plain")]
        return []

    fake = FakeServer(script)
    events = []

    async def main():
        client = AsyncClient(*fake.address, timeout=0.5, trace=events.append)
        assert await client.get("f") == b"plain"

    try:
        run(main())
    finally:
        fake.close()
    assert [e.opcode_name for e in events][:2] == ["RRQ", "ERROR"]
    assert {e.role for e in events} == {"client"}


# -- AsyncServer ---------------------------------------------------------------------------------------


def serve(handler, coro_factory, loop_factory=None, **kwargs):
    """Run an AsyncServer and the test coroutine on one loop."""

    async def main():
        kwargs.setdefault("timeout", 0.5)
        async with AsyncServer(handler, "127.0.0.1", 0, **kwargs) as server:
            await server.start()
            await coro_factory(server)

    run(main(), loop_factory)


def test_async_server_with_sync_and_async_clients(root):
    expected = (root / "big.bin").read_bytes()

    async def scenario(server):
        loop = asyncio.get_running_loop()
        sync = client_for(server, windowsize=8)
        assert await loop.run_in_executor(None, sync.get, "big.bin") == expected
        assert await async_client(server).get("513.bin") == (root / "513.bin").read_bytes()
        await async_client(server).put("aio-server-up.bin", b"z" * 5000)
        with pytest.raises(tftp.FileNotFound):
            await async_client(server).get("missing")

    serve(str(root), scenario, writable=True)
    assert (root / "aio-server-up.bin").read_bytes() == b"z" * 5000


def test_async_handler_and_async_streams():
    class AsyncHandler:
        def __init__(self):
            self.uploads = {}

        async def open_read(self, context):
            await asyncio.sleep(0.01)  # e.g. an async database lookup
            if context.filename == "gone":
                raise tftp.TftpError(tftp.ErrorCode.FILE_NOT_FOUND, "async says no")
            return AsyncSource(b"generated:" + context.filename.encode() * 1000)

        async def open_write(self, context, size):
            sink = SlowSink()
            self.uploads[context.filename] = sink
            return sink

    handler = AsyncHandler()

    async def scenario(server):
        client = async_client(server, windowsize=4)
        assert await client.get("x") == b"generated:" + b"x" * 1000
        with pytest.raises(tftp.FileNotFound) as info:
            await client.get("gone")
        assert info.value.message == "async says no"
        await client.put("up", os.urandom(20_000))

    serve(handler, scenario)
    assert len(handler.uploads["up"].data) == 20_000


def test_async_server_runs_blocking_handlers_in_the_executor(root):
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = b"from-http"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:

        async def scenario(server):
            # The Pipe's thread-side wake-ups must reach the loop thread.
            assert await async_client(server).get("anything") == b"from-http"

        serve(HttpHandler("http://127.0.0.1:%d" % httpd.server_address[1]), scenario)
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_async_server_memory_handler_trace_and_results():
    results, events = [], []

    async def scenario(server):
        client = async_client(server)
        await client.put("m.bin", b"memory")
        assert await client.get("m.bin") == b"memory"
        await asyncio.sleep(0.05)

    serve(MemoryHandler(writable=True), scenario, on_complete=results.append, trace=events.append)
    assert [r.operation for r in results] == ["write", "read"] and all(r.ok for r in results)
    assert {e.role for e in events} == {"server"} and len({e.session for e in events}) == 2


@pytest.mark.skipif(sys.platform != "win32", reason="Windows event loop types")
@pytest.mark.parametrize("loop_type", ["SelectorEventLoop", "ProactorEventLoop"])
def test_both_windows_loops(root, loop_type):
    async def scenario(server):
        assert server.supports_pktinfo  # kept on Proactor through the reader thread
        assert await async_client(server).get("513.bin") == (root / "513.bin").read_bytes()

    serve(str(root), scenario, loop_factory=getattr(asyncio, loop_type))


def test_async_server_shutdown_aborts_transfers(root):
    import socket

    async def scenario(server):
        loop = asyncio.get_running_loop()
        raw = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        raw.settimeout(2)
        raw.bind(("127.0.0.1", 0))
        try:
            request = tftp.encode_request(tftp.Opcode.RRQ, "big.bin")
            raw.sendto(request, server.server_address)
            await loop.run_in_executor(None, raw.recvfrom, 2048)  # DATA 1 / OACK
            server.shutdown()
            data, _ = await loop.run_in_executor(None, raw.recvfrom, 2048)
            assert tftp.decode(data).message == "server shutting down"
        finally:
            raw.close()

    serve(str(root), scenario, timeout=5)


def test_async_size(root, make_server):
    server = make_server(root)

    async def main():
        assert await async_client(server).size("big.bin") == 300_001

    run(main())


# -- an unencodable ERROR releases the session ---------------------------------------------------


def test_async_server_releases_a_session_whose_error_could_not_be_encoded():
    import io

    def broken():
        error = tftp.TftpError(1, "x")
        error.code = 70000
        return error

    class Handler:
        _tftp_fast_open_ = True

        def open_read(self, context):
            if context.filename == "ok":
                return io.BytesIO(b"fine")
            raise broken()

    async def scenario(server):
        client = async_client(server, retries=1)
        with pytest.raises(tftp.RemoteError):
            await client.get("bad")
        assert await client.get("ok") == b"fine"
        for _ in range(100):
            if not server.active_sessions:
                break
            await asyncio.sleep(0.02)
        assert server.active_sessions == 0

    serve(Handler(), scenario)


# -- link-local servers and send failures ---------------------------------------------------------


def _loop_factories():
    if sys.platform == "win32":
        return [asyncio.SelectorEventLoop, asyncio.ProactorEventLoop]
    return [None]


@pytest.mark.parametrize("loop_factory", _loop_factories(), ids=lambda f: getattr(f, "__name__", "default"))
@pytest.mark.parametrize("strict", [True, False])
def test_async_client_hears_a_server_named_by_a_link_local_address_with_its_zone(
    link_local, loop_factory, strict
):
    async def main():
        async with AsyncServer(MemoryHandler({"f": b"link-local"}), link_local, 0, timeout=0.5) as server:
            await server.start()
            client = AsyncClient(
                link_local, server.server_address[1], timeout=0.5, retries=2, strict_source=strict
            )
            assert await client.get("f") == b"link-local"

    run(main(), loop_factory)


def test_a_send_the_host_refuses_ends_the_request_and_an_icmp_report_does_not():
    """Transport errors are loss only when they are the peer's ICMP report."""
    import errno

    from tftp.aio.client import _Protocol, _Transfer

    async def main():
        loop = asyncio.get_running_loop()
        driver = _Transfer(AsyncClient("127.0.0.1", 9), loop)
        protocol = _Protocol(driver)
        protocol.error_received(ConnectionRefusedError(errno.ECONNREFUSED, "port closed"))
        protocol.error_received(ConnectionResetError(10054, "ICMP port unreachable"))
        assert not driver.first.done()
        protocol.error_received(OSError(errno.EMSGSIZE, "message too long"))
        with pytest.raises(OSError) as info:
            await driver.first
        assert info.value.errno == errno.EMSGSIZE and not isinstance(info.value, tftp.TftpError)

    run(main())
