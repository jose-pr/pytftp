"""asyncio client and server."""

from __future__ import annotations

import asyncio
import io
import os
import sys
import time

import pytest

import tftp
from conftest import BAD_FIRST_ANSWERS, LISTING, FakePeer, client_for
from tftp import AsyncTFTPClient, AsyncTFTPServer
from tftp.backends import HTTPBackend, MemoryBackend
from tftp.server import ThreadedHandler


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
    return AsyncTFTPClient(host, address[1], **kwargs)


# -- AsyncTFTPClient against the threaded server -------------------------------------------------------


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

    async def close(self):
        pass


class AsyncSource:
    def __init__(self, data, chunk=1000):
        self.data = io.BytesIO(data)
        self.chunk = chunk

    async def read(self, n):
        await asyncio.sleep(0)
        return self.data.read(min(n, self.chunk))

    async def close(self):
        pass


def test_async_sink_source_and_stream(root, make_server):
    server = make_server(root, writable=True)
    expected = (root / "big.bin").read_bytes()

    async def main():
        client = async_client(server, windowsize=4)
        sink = SlowSink()
        await client.download("big.bin", sink)
        assert bytes(sink.data) == expected
        await client.upload("from-async.bin", AsyncSource(expected[:30_000]))
        chunks = [c async for c in client.stream("big.bin", buffer=8192)]
        assert b"".join(chunks) == expected

    run(main())
    assert (root / "from-async.bin").read_bytes() == expected[:30_000]


def test_async_client_timeout_and_cancel(root, make_server):
    import socket

    async def main():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as silent:
            silent.bind(("127.0.0.1", 0))
            client = AsyncTFTPClient("127.0.0.1", silent.getsockname()[1], timeout=0.05, retries=2)
            with pytest.raises(tftp.TransferTimeoutError):
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


def test_async_client_trace_and_fallback(root, make_server, fake_server):
    from tftp.packet import encode_data, encode_error

    def script(packet):
        if isinstance(packet, tftp.RequestPacket):
            return [encode_error(8)] if packet.options else [encode_data(1, b"plain")]
        return []

    fake = fake_server(script)
    events = []

    async def main():
        client = AsyncTFTPClient(*fake.address, timeout=0.5, trace=events.append)
        assert await client.get("f") == b"plain"

    try:
        run(main())
    finally:
        fake.close()
    assert [e.opcode_name for e in events][:2] == ["RRQ", "ERROR"]
    assert {e.role for e in events} == {"client"}


def test_async_client_repeats_a_request_without_options_after_error_4(root, make_server, fake_server):
    """RFC 2347: after "an error for a request which carries an option", the client "may attempt to repeat the request without appending any options"."""
    from tftp.packet import encode_data, encode_error

    def script(packet):
        if isinstance(packet, tftp.RequestPacket):
            return [encode_error(4, "no")] if packet.options else [encode_data(1, b"plain")]
        return []

    fake = fake_server(script)

    async def main():
        assert await AsyncTFTPClient(*fake.address, timeout=0.5).get("f") == b"plain"

    try:
        run(main())
    finally:
        fake.close()
    assert [bool(r.options) for r in fake.requests if isinstance(r, tftp.RequestPacket)] == [True, False]


# -- AsyncTFTPServer ---------------------------------------------------------------------------------------


def serve(handler, coro_factory, loop_factory=None, **kwargs):
    """Run an AsyncTFTPServer and the test coroutine on one loop."""

    async def main():
        kwargs.setdefault("timeout", 0.5)
        async with AsyncTFTPServer(handler, host="127.0.0.1", port=0, **kwargs) as server:
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
                raise tftp.TFTPError(tftp.TFTPErrorCode.FILE_NOT_FOUND, "async says no")
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

        serve(ThreadedHandler(HTTPBackend("http://127.0.0.1:%d" % httpd.server_address[1])), scenario)
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

    serve(
        ThreadedHandler(MemoryBackend(writable=True)),
        scenario,
        on_complete=results.append,
        trace=events.append,
    )
    assert [r.operation for r in results] == ["write", "read"] and all(r.is_ok for r in results)
    assert {e.role for e in events} == {"server"} and len({e.session for e in events}) == 2


@pytest.mark.skipif(sys.platform != "win32", reason="Windows event loop types")
@pytest.mark.parametrize("loop_type", ["SelectorEventLoop", "ProactorEventLoop"])
def test_both_windows_loops(root, loop_type):
    async def scenario(server):
        assert server.has_pktinfo  # kept on Proactor through the reader thread
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
            request = tftp.packet.encode_request(tftp.TFTPOpcode.RRQ, "big.bin")
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
        error = tftp.TFTPError(1, "x")
        error.code = 70000
        return error

    class Handler:
        opens_fast = True

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

    serve(ThreadedHandler(Handler()), scenario)


# -- link-local servers and send failures ---------------------------------------------------------


@pytest.mark.parametrize("strict", [True, False])
def test_async_client_hears_a_server_named_by_a_link_local_address_with_its_zone(
    link_local, loop_factory, strict
):
    async def main():
        async with AsyncTFTPServer(
            ThreadedHandler(MemoryBackend({"f": b"link-local"})), host=link_local, port=0, timeout=0.5
        ) as server:
            await server.start()
            client = AsyncTFTPClient(
                link_local, server.server_address[1], timeout=0.5, retries=2, strict_source=strict
            )
            assert await client.get("f") == b"link-local"

    run(main(), loop_factory)


def test_a_send_the_host_refuses_ends_the_request_and_an_icmp_report_does_not():
    """Transport errors are loss only when they are the peer's ICMP report."""
    import errno

    from tftp.client._asyncio import (
        _Protocol,
        _Transfer,
    )  # the ICMP error callback cannot be provoked on every host

    async def main():
        loop = asyncio.get_running_loop()
        driver = _Transfer(AsyncTFTPClient("127.0.0.1", 9), loop)
        protocol = _Protocol(driver)
        protocol.error_received(ConnectionRefusedError(errno.ECONNREFUSED, "port closed"))
        protocol.error_received(ConnectionResetError(10054, "ICMP port unreachable"))
        assert not driver.first.done()
        protocol.error_received(OSError(errno.EMSGSIZE, "message too long"))
        with pytest.raises(OSError) as info:
            await driver.first
        assert info.value.errno == errno.EMSGSIZE and not isinstance(info.value, tftp.TFTPError)

    run(main())


def test_async_client_deadline_ends_a_transfer_before_its_retries_do():
    import socket
    import time

    async def main():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as silent:
            silent.bind(("127.0.0.1", 0))
            client = AsyncTFTPClient(
                "127.0.0.1", silent.getsockname()[1], timeout=0.05, retries=100, deadline=0.3
            )
            started = time.monotonic()
            with pytest.raises(tftp.TransferTimeoutError):
                await client.get("x")
            # 100 retries at the backed-off 0.4 s would take some forty seconds.
            assert time.monotonic() - started < 10

    run(main())


# -- the first answer and the time limit ----------------------------------------------------------


@pytest.mark.parametrize("is_read, answer", [pytest.param(r, a, id=i) for i, r, a in BAD_FIRST_ANSWERS])
def test_a_first_answer_a_server_may_not_send_is_a_protocol_error(loop_factory, is_read, answer):
    class Sink:
        async def write(self, data):
            pass

    async def main():
        with FakePeer(lambda data: [answer]) as peer:
            client = AsyncTFTPClient("127.0.0.1", peer.port, timeout=1.0, retries=1)
            call = client.download("f", Sink()) if is_read else client.upload("f", AsyncSource(b"payload"))
            try:
                await asyncio.wait_for(call, 5)
            except asyncio.TimeoutError:
                pytest.fail("the client waited for a transfer that cannot start")
            except tftp.TFTPProtocolError:
                pass
            else:
                pytest.fail("a first answer no server may send was accepted")
            sent = [tftp.decode(data) for _, data in peer.seen]
        assert not any(isinstance(p, tftp.DataPacket) for p in sent)

    run(main(), loop_factory)


@pytest.mark.parametrize("call", ["get", "size", "stat"])
def test_the_time_limit_bounds_every_call_that_waits_for_a_server(loop_factory, call):
    import time

    async def main():
        with FakePeer() as peer:
            client = AsyncTFTPClient("127.0.0.1", peer.port, timeout=3.0, retries=3, deadline=0.5)
            started = time.monotonic()
            with pytest.raises(tftp.TransferTimeoutError):
                await getattr(client, call)("f")
            return time.monotonic() - started

    elapsed = run(main(), loop_factory)
    # A second of margin over the 0.5 s limit; the wait unbounded by it is 3 s a request.
    assert 0.4 <= elapsed < 1.5


def test_the_time_limit_is_one_start_across_the_fallback_to_a_request_without_options(loop_factory):
    import time

    from tftp.packet import encode_error

    def script(data):
        request = tftp.decode(data)
        if isinstance(request, tftp.RequestPacket) and request.options:
            return [(1.5, encode_error(tftp.TFTPErrorCode.OPTION_REFUSED))]
        return []

    async def main():
        with FakePeer(script) as peer:
            client = AsyncTFTPClient("127.0.0.1", peer.port, timeout=5.0, retries=3, deadline=2.0)
            started = time.monotonic()
            with pytest.raises(tftp.TransferTimeoutError):
                await client.get("f")
            return time.monotonic() - started

    elapsed = run(main(), loop_factory)
    # 1.5 s in the first request leave 0.5 s for the second; a fresh limit would end it near 3.5 s.
    assert 1.9 <= elapsed < 3.0


def test_an_engine_with_nothing_outstanding_is_woken_after_one_timeout(loop_factory):
    from tftp.client._asyncio import (
        _Transfer,
    )  # drives an engine that is idle, which a real peer never leaves

    class Idle:
        deadline = None
        is_done = False
        is_stalled = False
        woken = None

        def on_timeout(self, now):
            Idle.woken = now
            self.is_done = True

    async def main():
        driver = _Transfer(AsyncTFTPClient("127.0.0.1", 9, timeout=0.05), asyncio.get_running_loop())
        driver.engine = Idle()
        driver.schedule()
        assert driver.timer is not None, "no timer was armed for an engine with no deadline"
        await asyncio.wait_for(asyncio.sleep(0.5), 5)
        assert Idle.woken is not None, "the engine was never told that a timeout passed"

    run(main(), loop_factory)


# -- the destination file, local failures and sizes ------------------------------------------------

OLD = b"the previous good copy"


def _beside(directory):
    return sorted(p.name for p in directory.iterdir())


@pytest.fixture
def dest(tmp_path):
    directory = tmp_path / "out"
    directory.mkdir()
    path = directory / "important.cfg"
    path.write_bytes(OLD)
    return path


@pytest.mark.parametrize("host", ["127.0.0.1", None, "h:+70"], ids=["not-found", "host-is-none", "bad-host"])
def test_a_failed_download_to_a_path_leaves_the_file_that_was_there(loop_factory, dest, host):
    from tftp.packet import encode_error

    async def main():
        with FakePeer(lambda data: [encode_error(tftp.TFTPErrorCode.FILE_NOT_FOUND)]) as peer:
            client = AsyncTFTPClient(host, peer.port, timeout=0.5, retries=1)
            with pytest.raises(Exception):
                await client.download("f", dest)

    run(main(), loop_factory)
    assert dest.read_bytes() == OLD
    assert _beside(dest.parent) == ["important.cfg"]


def test_a_download_to_a_path_replaces_the_file_on_success(loop_factory, root, make_server, dest):
    server = make_server(root)

    async def main():
        return await async_client(server).download("big.bin", dest)

    result = run(main(), loop_factory)
    assert dest.read_bytes() == (root / "big.bin").read_bytes() and result.bytes == 300_001
    assert _beside(dest.parent) == ["important.cfg"]


def test_a_download_that_fails_while_data_arrives_leaves_the_file_and_no_temporary(loop_factory, dest):
    async def main():
        with FakePeer(_data_for_ever) as peer:
            client = AsyncTFTPClient(
                "127.0.0.1", peer.port, timeout=0.5, retries=1, blksize=None, deadline=0.3
            )
            with pytest.raises(tftp.TransferTimeoutError):
                await client.download("f", dest)
            assert len(peer.seen) > 2, "no data had arrived when the transfer failed"

    run(main(), loop_factory)
    assert dest.read_bytes() == OLD
    assert _beside(dest.parent) == ["important.cfg"]


def test_a_download_into_a_directory_that_does_not_exist_sends_nothing(loop_factory, tmp_path):
    async def main():
        with FakePeer() as peer:
            client = AsyncTFTPClient("127.0.0.1", peer.port, timeout=0.2, retries=1)
            with pytest.raises(FileNotFoundError):
                await client.download("f", tmp_path / "missing" / "x.bin")
            assert peer.seen == []

    run(main(), loop_factory)


def _data_for_ever(data):
    from tftp.packet import encode_data

    packet = tftp.decode(data)
    if isinstance(packet, tftp.RequestPacket):
        return [encode_data(1, b"x" * 512)]
    if isinstance(packet, tftp.AckPacket):
        return [encode_data(packet.block + 1, b"x" * 512)]
    return []


def test_a_failing_sink_is_raised_as_itself_and_the_server_is_told(loop_factory):
    import errno

    class DiskFull:
        async def write(self, data):
            raise OSError(errno.ENOSPC, "No space left on device")

    async def main():
        with FakePeer(_data_for_ever) as peer:
            client = AsyncTFTPClient("127.0.0.1", peer.port, timeout=0.5, retries=1, blksize=None)
            with pytest.raises(OSError) as info:
                await client.download("f", DiskFull())
            assert info.value.errno == errno.ENOSPC and not isinstance(info.value, tftp.TFTPError)
            for _ in range(100):
                if any(isinstance(tftp.decode(d), tftp.ErrorPacket) for _, d in peer.seen):
                    break
                await asyncio.sleep(0.05)
            return [tftp.decode(d) for _, d in peer.seen]

    seen = run(main(), loop_factory)
    assert any(isinstance(p, tftp.ErrorPacket) and p.code == tftp.TFTPErrorCode.DISK_FULL for p in seen)


def test_max_size_ends_a_download_the_server_does_not_end(loop_factory, tmp_path):
    class Sink:
        def __init__(self):
            self.data = bytearray()

        async def write(self, data):
            self.data += data

    sink = Sink()

    async def main():
        with FakePeer(_data_for_ever) as peer:
            client = AsyncTFTPClient(
                "127.0.0.1", peer.port, timeout=0.5, retries=1, blksize=None, max_size=700
            )
            with pytest.raises(tftp.TransferTooLargeError):
                await client.download("f", sink)
            with pytest.raises(tftp.TransferTooLargeError):
                await client.get("f", max_size=1000)
            with pytest.raises(tftp.TransferTooLargeError):
                await client.download("f", tmp_path / "x.bin")
            for _ in range(100):
                if any(tftp.decode(d).__class__ is tftp.ErrorPacket for _, d in peer.seen):
                    break
                await asyncio.sleep(0.05)
            return [tftp.decode(d) for _, d in peer.seen]

    seen = run(main(), loop_factory)
    assert any(isinstance(p, tftp.ErrorPacket) and p.code == tftp.TFTPErrorCode.DISK_FULL for p in seen)
    assert len(sink.data) <= 700
    assert not (tmp_path / "x.bin").exists()


def test_a_listing_is_bounded_and_the_server_is_told(loop_factory, root, make_server):
    server = make_server(root, options=LISTING)

    async def main():
        client = async_client(server)
        with pytest.raises(tftp.TransferTooLargeError):
            await client.listdir(max_size=10)
        assert await client.listdir()

    run(main(), loop_factory)
    for _ in range(100):
        if server.stats_snapshot()["failed"] >= 1:
            break
        time.sleep(0.05)
    assert server.stats_snapshot()["failed"] >= 1


# -- who owns an asyncio transfer's tasks and socket ----------------------------------------------


@pytest.fixture
def sockets(monkeypatch):
    """The sockets the clients bind, as they are made: each must be closed when its transfer is over."""
    import netimps

    made = []
    real = netimps.bind

    def recording(*args, **kwargs):
        sock = real(*args, **kwargs)
        if sys._getframe(1).f_globals["__name__"].startswith("tftp.client"):  # not the servers'
            made.append(sock)
        return sock

    monkeypatch.setattr(netimps, "bind", recording)
    return made


def _tasks():
    """The tasks other than the test's own (``run`` wraps it in ``wait_for``, itself a task before 3.12)."""
    me = asyncio.current_task()
    return {t for t in asyncio.all_tasks() if t is not me and t.get_coro().__qualname__ != "wait_for"}


async def _settled(what):
    """The tasks this test did not start, once the loop has had a turn; named in the failure."""
    await asyncio.sleep(0)
    left = _tasks()
    assert not left, "%s: tasks still pending: %s" % (what, sorted(t.get_coro().__qualname__ for t in left))


def _all_closed(sockets, what):
    assert sockets, "%s: the client bound no socket" % what
    assert [s for s in sockets if s.fileno() != -1] == [], "%s: a socket is still open" % what


class _Stuck:
    """A sink and a source that never complete a call."""

    async def write(self, data):
        await asyncio.Event().wait()

    async def read(self, n):
        await asyncio.Event().wait()


class _Reads:
    def __init__(self, data):
        self.data = io.BytesIO(data)
        self.taken = 0

    async def read(self, n):
        chunk = self.data.read(n)
        self.taken += len(chunk)
        return chunk


def test_a_download_into_a_stream_the_server_refuses_leaves_no_task_and_no_socket(
    loop_factory, root, make_server, sockets
):
    server = make_server(root)

    async def main():
        class Sink:
            async def write(self, data):
                pass

        with pytest.raises(tftp.FileNotFound):
            await async_client(server).download("missing", Sink())
        await _settled("after the refusal")

    run(main(), loop_factory)
    _all_closed(sockets, "after the refusal")


def test_an_upload_the_server_refuses_takes_nothing_from_its_source_and_leaves_no_task(
    loop_factory, root, make_server, sockets
):
    server = make_server(root)  # read-only: the request is refused
    source = _Reads(b"x" * 3_000_000)

    async def main():
        with pytest.raises(tftp.AccessViolation):
            await async_client(server).upload("up.bin", source)
        await _settled("after the refusal")

    run(main(), loop_factory)
    assert source.taken == 0, (
        "%d octets were taken from the source of an upload that never started" % source.taken
    )
    _all_closed(sockets, "after the refusal")


@pytest.mark.parametrize("direction", ["download", "upload"])
def test_a_cancelled_transfer_leaves_no_task_and_no_socket(
    loop_factory, root, make_server, sockets, direction
):
    server = make_server(root, writable=True, timeout=5)

    async def main():
        client = async_client(server, blksize=None, timeout=5)
        if direction == "download":
            call = client.download("big.bin", _Stuck())
        else:
            call = client.upload("up.bin", _Stuck())
        task = asyncio.ensure_future(call)
        await asyncio.sleep(0.3)
        assert not task.done(), "the transfer ended before it could be cancelled"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        await _settled("after the cancel")

    run(main(), loop_factory)
    _all_closed(sockets, "after the cancel")


@pytest.mark.parametrize("direction", ["download", "upload"])
def test_a_transfer_that_times_out_leaves_no_task_and_no_socket(loop_factory, sockets, direction):
    async def main():
        class Sink:
            async def write(self, data):
                pass

        with FakePeer() as peer:
            client = AsyncTFTPClient("127.0.0.1", peer.port, timeout=0.1, retries=1)
            with pytest.raises(tftp.TransferTimeoutError):
                if direction == "download":
                    await client.download("f", Sink())
                else:
                    await client.upload("f", AsyncSource(b"x" * 100_000))
        await _settled("after the timeout")

    run(main(), loop_factory)
    _all_closed(sockets, "after the timeout")


def test_a_transfer_that_succeeds_leaves_no_task_and_no_socket(loop_factory, root, make_server, sockets):
    server = make_server(root, writable=True)

    async def main():
        client = async_client(server)

        class Sink:
            async def write(self, data):
                pass

        await client.download("big.bin", Sink())
        await client.upload("up.bin", AsyncSource(b"y" * 5000))
        assert (
            b"".join([c async for c in client.stream("big.bin", buffer=8192)])
            == (root / "big.bin").read_bytes()
        )
        await _settled("after the transfers")

    run(main(), loop_factory)
    _all_closed(sockets, "after the transfers")


def test_stream_holds_about_buffer_octets_and_leaves_nothing_when_closed_early(
    loop_factory, make_server, sockets
):
    class Counting(io.BytesIO):
        taken = 0

        def readinto(self, view):
            n = super().readinto(view)
            Counting.taken += n
            return n

    class Handler:
        opens_fast = True

        def open_read(self, context):
            return Counting(bytes(6_000_000))

    Counting.taken = 0
    server = make_server(Handler(), timeout=5)

    async def main():
        client = AsyncTFTPClient("127.0.0.1", server.server_address[1], timeout=5, retries=1)
        stream = client.stream("big", buffer=8192)
        first = await stream.__anext__()
        await asyncio.sleep(1.0)  # the consumer is busy: nothing more is taken
        ahead = Counting.taken - len(first)
        await stream.aclose()
        await _settled("after aclose()")
        return ahead

    ahead = run(main(), loop_factory)
    # buffer octets here, and the blocks in flight to the server's next ACK: nothing like the
    # 1 MiB a second queue held.
    assert ahead < 16 * 1024, "%d octets were read ahead of a consumer that stopped" % ahead
    _all_closed(sockets, "after aclose()")


def test_abandoning_a_stream_tells_the_server(loop_factory):
    async def main():
        with FakePeer(_data_for_ever) as peer:
            client = AsyncTFTPClient("127.0.0.1", peer.port, timeout=5, retries=1, blksize=None)
            stream = client.stream("f", buffer=2048)
            await stream.__anext__()
            await stream.aclose()
            for _ in range(100):
                if any(isinstance(tftp.decode(d), tftp.ErrorPacket) for _, d in peer.seen):
                    break
                await asyncio.sleep(0.05)
            return [tftp.decode(d) for _, d in peer.seen]

    seen = run(main(), loop_factory)
    assert any(isinstance(p, tftp.ErrorPacket) for p in seen), "the server was never told"


def test_a_transfer_is_closed_once_and_asyncio_logs_nothing(loop_factory, root, make_server):
    """On some CPython 3.9 and 3.10 releases the Proactor loop logs a traceback for a socket closed twice."""
    import logging

    records = []

    class Catch(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Catch(level=logging.DEBUG)
    logger = logging.getLogger("asyncio")
    logger.addHandler(handler)
    server = make_server(root, writable=True)

    async def main():
        client = async_client(server)

        class Sink:
            async def write(self, data):
                pass

        await client.download("big.bin", Sink())
        await client.upload("up.bin", AsyncSource(b"y" * 5000))
        with pytest.raises(tftp.FileNotFound):
            await client.download("missing", Sink())
        await asyncio.sleep(0.1)  # the loop's own close callbacks run

    try:
        run(main(), loop_factory)
    finally:
        logger.removeHandler(handler)
    assert [r.getMessage()[:200] for r in records if r.levelno >= logging.WARNING] == []


def test_async_transfers_in_flight_at_shutdown_are_reported(root):
    import socket

    results, stats = [], []

    async def scenario(server):
        loop = asyncio.get_running_loop()
        peers = []
        try:
            for operation, name in (
                (tftp.TFTPOpcode.RRQ, "big.bin"),
                (tftp.TFTPOpcode.WRQ, "unfinished.bin"),
            ):
                raw = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                peers.append(raw)
                raw.settimeout(2)
                raw.bind(("127.0.0.1", 0))
                raw.sendto(tftp.packet.encode_request(operation, name), server.server_address)
                await loop.run_in_executor(None, raw.recvfrom, 2048)
            server.shutdown()
            assert await server.wait_closed(5)
            for raw in peers:
                data, _ = await loop.run_in_executor(None, raw.recvfrom, 2048)
                assert tftp.decode(data).message == "server shutting down"
            stats.append(server.stats_snapshot())
        finally:
            for raw in peers:
                raw.close()

    serve(str(root), scenario, timeout=5, writable=True, on_complete=results.append)
    assert sorted(r.operation for r in results) == ["read", "write"]
    assert all(isinstance(r.error, tftp.TransferAbortedError) for r in results)
    assert (stats[0]["failed"], stats[0]["completed"], stats[0]["active"]) == (2, 0, 0)
    assert not (root / "unfinished.bin").exists()


@pytest.mark.parametrize("last", ["final ACK", "ERROR"])
def test_async_server_last_datagram_reaches_the_peer_when_the_transfer_is_released_at_once(
    root, loop_factory, last
):
    """The transfer's transport is aborted right after its last send; the peer must still get it.

    ``dally=False`` ends an upload at its final ACK, and a datagram that is not
    TFTP ends it with an ERROR: both leave nothing to wait for.
    """
    import socket

    async def scenario(server):
        loop = asyncio.get_running_loop()
        raw = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        raw.settimeout(5)
        raw.bind(("127.0.0.1", 0))
        try:
            raw.sendto(tftp.packet.encode_request(tftp.TFTPOpcode.WRQ, "last.bin"), server.server_address)
            data, tid = await loop.run_in_executor(None, raw.recvfrom, 2048)
            assert tftp.decode(data) == tftp.AckPacket(0)
            if last == "final ACK":
                raw.sendto(tftp.packet.encode_data(1, b"short"), tid)
                data, _ = await loop.run_in_executor(None, raw.recvfrom, 2048)
                assert tftp.decode(data) == tftp.AckPacket(1)
            else:
                raw.sendto(b"\x00\x09garbage", tid)  # an opcode that does not exist
                data, _ = await loop.run_in_executor(None, raw.recvfrom, 2048)
                assert isinstance(tftp.decode(data), tftp.ErrorPacket)
            deadline = time.monotonic() + 5
            while server.active_sessions and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert server.active_sessions == 0
        finally:
            raw.close()

    serve(str(root), scenario, loop_factory, writable=True, dally=False)
    assert (root / "last.bin").exists() == (last == "final ACK")


def test_the_largest_block_size_arrives_in_full_between_the_asyncio_peers(root, tmp_path, loop_factory):
    payload = os.urandom(2 * 65464 + 17)
    (root / "largest.bin").write_bytes(payload)

    async def scenario(server):
        client = async_client(server, blksize=65464, timeout=2)
        down = await client.download("largest.bin", tmp_path / "down.bin")
        assert down.negotiated.blksize == 65464 and down.retransmits == 0
        up = await client.upload("largest-up.bin", payload)
        assert up.negotiated.blksize == 65464 and up.retransmits == 0

    serve(str(root), scenario, loop_factory, writable=True, overwrite=True, timeout=2)
    assert (tmp_path / "down.bin").read_bytes() == payload
    assert (root / "largest-up.bin").read_bytes() == payload


@pytest.mark.parametrize("strict, taken", [(True, b"real"), (False, b"rival")])
def test_strict_source_decides_whether_an_answer_from_another_address_is_taken(
    rivals, loop_factory, strict, taken
):
    pair = rivals()

    async def main():
        client = AsyncTFTPClient("127.0.0.1", pair.port, timeout=1, retries=2, strict_source=strict)
        assert await client.get("x") == taken

    run(main(), loop_factory)
