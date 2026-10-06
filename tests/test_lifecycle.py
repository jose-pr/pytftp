"""The servers' lifecycle: bind, serve, shutdown, wait_closed, close.

Every wait has a timeout and fails with a message, every thread a test starts
is joined, and a fixture checks that no ``tftp-*`` thread and no unclosed
socket is left behind.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import gc
import inspect
import io
import logging
import socket
import sys
import threading
import time
import warnings

import pytest

import tftp
from tftp import AsyncTFTPClient, AsyncTFTPServer, TFTPServer
from tftp.backends import MemoryBackend
from tftp.relay import TFTPRelay
from tftp.server import ThreadedHandler

WAIT = 5.0  # a second of margin over what any step here takes on a loaded runner


@pytest.fixture(autouse=True)
def nothing_left():
    """No ``tftp-*`` thread and no unclosed socket survives the test."""
    gc.collect()
    before = set(threading.enumerate())
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        yield
        gc.collect()
    deadline = time.monotonic() + WAIT
    while True:
        left = [t.name for t in threading.enumerate() if t not in before and t.name.startswith("tftp-")]
        if not left or time.monotonic() > deadline:
            break
        time.sleep(0.01)
    assert left == [], "threads left running: %s" % left
    unclosed = [str(w.message) for w in caught if issubclass(w.category, ResourceWarning)]
    assert unclosed == [], "unclosed: %s" % unclosed


def within(fn, what, seconds=WAIT):
    """Call ``fn`` on a thread of its own: its result, or a failure instead of a hang."""
    box = []

    def target():
        try:
            box.append((True, fn()))
        except BaseException as exc:  # noqa: BLE001 - handed to the caller
            box.append((False, exc))

    thread = threading.Thread(target=target, name="within-" + what, daemon=True)
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), "%s did not return within %s s" % (what, seconds)
    ok, value = box[0]
    if not ok:
        raise value
    return value


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# -- the blocking server and the relay ------------------------------------------------------------


def _server(port=0, **kwargs):
    kwargs.setdefault("timeout", 0.5)
    return TFTPServer(MemoryBackend({"f": b"data"}), host="127.0.0.1", port=port, **kwargs)


def _relay(port=0, **kwargs):
    return TFTPRelay(("127.0.0.1", 9), host="127.0.0.1", port=port, **kwargs)


@pytest.fixture(params=[_server, _relay], ids=["server", "relay"])
def make(request):
    """A server or a relay, closed after the test."""
    made = []

    def build(**kwargs):
        service = request.param(**kwargs)
        made.append(service)
        return service

    yield build
    for service in made:
        within(service.close, "close")


def test_the_lifecycle_names(make):
    service = make()
    for name in ("bind", "start", "serve_forever", "shutdown", "wait_closed", "close"):
        assert callable(getattr(service, name)), name
    for gone in ("stop", "wait", "listen"):
        assert not hasattr(service, gone), gone


def test_the_constructor_binds_nothing(make):
    service = make()
    assert service.server_address is None
    assert service.has_pktinfo is None


def test_bind_is_idempotent_and_reports_the_address(make):
    service = make()
    service.bind()
    address = service.server_address
    assert address[0] == "127.0.0.1" and address[1] > 0
    service.bind()
    assert service.server_address == address


def test_entering_the_context_binds_without_serving(make):
    service = make()
    with service:
        assert service.server_address is not None
        assert service.wait_closed(0)  # nothing is serving


def test_start_returns_a_server_that_answers(make):
    service = make()
    assert service.start() is service
    if isinstance(service, TFTPServer):
        port = service.server_address[1]
        assert tftp.TFTPClient("127.0.0.1", port, timeout=0.5, retries=3).get("f") == b"data"


@pytest.mark.parametrize("served_first", [False, True], ids=["never_served", "served"])
def test_start_after_close_raises(make, served_first):
    service = make()
    if served_first:
        service.start()
    within(service.close, "close")
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="closed"):
        service.start()
    assert time.monotonic() - started < 1.0, "start() after close() waited before it raised"
    with pytest.raises(RuntimeError, match="closed"):
        service.serve_forever()
    with pytest.raises(RuntimeError, match="closed"):
        service.bind()


def test_a_second_start_raises_and_close_still_returns(make):
    service = make()
    service.start()
    with pytest.raises(RuntimeError, match="already serving"):
        service.start()
    with pytest.raises(RuntimeError, match="already serving"):
        service.serve_forever()
    within(service.close, "close")


def test_start_raises_what_bind_raised_and_leaves_nothing(make):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as held:
        held.bind(("127.0.0.1", 0))
        service = make(port=held.getsockname()[1])
        with pytest.raises(OSError):
            service.start()
        assert service.server_address is None
        with pytest.raises(OSError):
            service.bind()


def test_close_is_final_and_repeatable(make):
    service = make()
    service.start()
    within(service.close, "close")
    within(service.close, "second close")


def test_shutdown_returns_at_once_and_wait_closed_sees_the_end(make):
    service = make()
    service.start()
    assert service.wait_closed(0) is False  # serving
    began = time.monotonic()
    service.shutdown()
    assert time.monotonic() - began < 1.0
    assert within(lambda: service.wait_closed(WAIT), "wait_closed") is True
    service.shutdown()  # nothing is serving: a no-op


def test_serve_forever_returns_after_shutdown_from_another_thread(make):
    service = make()
    service.bind()
    timer = threading.Timer(0.2, service.shutdown)
    timer.name = "tftp-test-timer"
    timer.start()
    try:
        within(service.serve_forever, "serve_forever")
    finally:
        timer.join(WAIT)
    assert service.wait_closed(0) is True


def test_a_service_serves_again_after_shutdown_until_it_is_closed(make):
    service = make()
    for _ in range(2):
        service.start()
        service.shutdown()
        assert within(lambda: service.wait_closed(WAIT), "wait_closed") is True


def test_wait_closed_times_out_while_serving(make):
    service = make()
    service.start()
    assert service.wait_closed(0.05) is False


class _Closer:
    """A handler that asks its own server to stop, and records what close() does there."""

    opens_fast = True

    def __init__(self):
        self.server = None
        self.close_error = None

    def open_read(self, context):
        try:
            self.server.close()
        except RuntimeError as exc:
            self.close_error = exc
        self.server.shutdown()
        return io.BytesIO(b"x")


def test_a_handler_shuts_down_with_shutdown_and_cannot_close():
    handler = _Closer()
    server = TFTPServer(handler, host="127.0.0.1", port=0, timeout=0.5)
    handler.server = server
    try:
        server.start()
        with pytest.raises(tftp.TFTPError):
            tftp.TFTPClient("127.0.0.1", server.server_address[1], timeout=0.3, retries=1).get("f")
        assert within(lambda: server.wait_closed(WAIT), "wait_closed") is True
        assert isinstance(handler.close_error, RuntimeError) and "shutdown()" in str(handler.close_error)
    finally:
        within(server.close, "close")


def test_wait_closed_from_the_serving_thread_is_refused():
    seen = []
    server = _server()

    class Waiter:
        opens_fast = True

        def open_read(self, context):
            try:
                server.wait_closed()
            except RuntimeError as exc:
                seen.append(exc)
            server.shutdown()
            return io.BytesIO(b"x")

    server.handler = Waiter()
    try:
        server.start()
        with pytest.raises(tftp.TFTPError):  # the transfer is aborted by the shutdown
            tftp.TFTPClient("127.0.0.1", server.server_address[1], timeout=0.3, retries=1).get("f")
    finally:
        within(server.close, "close")
    assert len(seen) == 1 and "itself" in str(seen[0])


# -- what a failed bind leaves ----------------------------------------------------------------------


def _fail_after_building(monkeypatch):
    """Make the second selector registration fail; keep every object built before it.

    Returns the selectors, worker pools and socket pairs made.
    """
    import selectors

    selectors_made, pools, pairs = [], [], []
    real_pool, real_pair = concurrent.futures.ThreadPoolExecutor, socket.socketpair

    class Failing(selectors.DefaultSelector):
        def __init__(self):
            super().__init__()
            selectors_made.append(self)

        def register(self, fileobj, events, data=None):
            if self.get_map():
                raise OSError("register refused")
            return super().register(fileobj, events, data)

    def pool(*args, **kwargs):
        made = real_pool(*args, **kwargs)
        pools.append(made)
        return made

    def pair(*args, **kwargs):
        made = real_pair(*args, **kwargs)
        pairs.append(made)
        return made

    monkeypatch.setattr(selectors, "DefaultSelector", Failing)
    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", pool)
    monkeypatch.setattr(socket, "socketpair", pair)
    return selectors_made, pools, pairs


def _the_port_is_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", port))


@pytest.mark.parametrize("kind", ["server", "relay"])
def test_a_failure_part_way_through_start_leaves_nothing_open(monkeypatch, kind):
    selectors_made, pools, pairs = _fail_after_building(monkeypatch)
    port = free_port()
    if kind == "server":
        service = TFTPServer(MemoryBackend({"f": b"data"}), host="127.0.0.1", port=port, open_in_thread=True)
    else:
        service = TFTPRelay(("127.0.0.1", 9), host="127.0.0.1", port=port)
    with pytest.raises(OSError, match="register refused"):
        service.start()
    (selector,) = selectors_made
    assert selector.get_map() is None, "the selector was not closed"
    assert pairs and all(sock.fileno() == -1 for pair in pairs for sock in pair), "a socket pair is open"
    for pool in pools:
        with pytest.raises(RuntimeError):
            pool.submit(int)  # a closed pool refuses work
    assert (kind == "relay") or pools, "the server made no worker pool"
    _the_port_is_free(port)
    assert service.server_address is None
    within(service.close, "close")


def test_a_listener_that_cannot_finish_closes_its_socket(monkeypatch):
    import netimps

    from tftp.server._listener import Listener  # internal: no public name

    def refuse(*args, **kwargs):
        raise OSError("endpoint refused")

    monkeypatch.setattr(netimps, "UDPEndpoint", refuse)
    port = free_port()
    with pytest.raises(OSError, match="endpoint refused"):
        Listener("127.0.0.1", port)
    _the_port_is_free(port)


# -- the asyncio server ---------------------------------------------------------------------------


def _loop_factories():
    if sys.platform == "win32":
        return [asyncio.SelectorEventLoop, asyncio.ProactorEventLoop]
    return [None]


loops = pytest.mark.parametrize(
    "loop_factory", _loop_factories(), ids=lambda f: getattr(f, "__name__", "default")
)


def run(main, loop_factory):
    """Run ``main()`` on a fresh loop of the given kind, with a limit on the whole."""

    async def bounded():
        return await asyncio.wait_for(main(), 30)

    if loop_factory is None:
        return asyncio.run(bounded())
    loop = loop_factory()
    try:
        return loop.run_until_complete(bounded())
    finally:
        loop.close()


async def waits(awaitable, what, seconds=WAIT):
    try:
        return await asyncio.wait_for(awaitable, seconds)
    except asyncio.TimeoutError:
        pytest.fail("%s did not finish within %s s" % (what, seconds))


def _async_server(**kwargs):
    kwargs.setdefault("timeout", 0.5)
    return AsyncTFTPServer(ThreadedHandler(MemoryBackend({"f": b"data"})), host="127.0.0.1", port=0, **kwargs)


def test_the_async_lifecycle_names():
    server = _async_server()
    assert not inspect.iscoroutinefunction(server.bind)
    assert not inspect.iscoroutinefunction(server.shutdown)
    for coroutine in ("start", "serve_forever", "wait_closed", "aclose"):
        assert inspect.iscoroutinefunction(getattr(server, coroutine)), coroutine
    for gone in ("close", "stop", "wait", "listen"):
        assert not hasattr(server, gone), gone


def test_the_async_constructor_binds_nothing():
    server = _async_server()
    assert server.server_address is None and server.has_pktinfo is None
    server.bind()
    address = server.server_address
    assert address[1] > 0
    server.bind()
    assert server.server_address == address

    async def finish():
        await waits(server.aclose(), "aclose")

    run(finish, None)


@loops
def test_async_start_serves_and_aclose_releases_the_port(loop_factory, caplog):
    async def main():
        results = []
        server = _async_server(on_complete=results.append)
        assert await waits(server.start(), "start") is server
        port = server.server_address[1]
        client = AsyncTFTPClient("127.0.0.1", port, timeout=0.5, retries=3)
        assert await waits(client.get("f"), "get") == b"data"
        deadline = time.monotonic() + WAIT
        while not results and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert [r.is_ok for r in results] == [True], "the server did not report the transfer"
        await waits(server.aclose(), "aclose")
        return port

    _the_port_is_free(run(main, loop_factory))
    assert [
        r.getMessage() for r in caplog.records if r.name == "tftp.server" and r.levelno >= logging.ERROR
    ] == []


@loops
@pytest.mark.parametrize("served_first", [False, True], ids=["never_served", "served"])
def test_async_start_after_aclose_raises(loop_factory, served_first):
    async def main():
        server = _async_server()
        if served_first:
            await waits(server.start(), "start")
        await waits(server.aclose(), "aclose")
        for call in (server.start, server.serve_forever):
            with pytest.raises(RuntimeError, match="closed"):
                await waits(call(), call.__name__)
        with pytest.raises(RuntimeError, match="closed"):
            server.bind()

    run(main, loop_factory)


@loops
def test_a_second_async_start_raises_and_aclose_returns(loop_factory):
    async def main():
        server = _async_server()
        await waits(server.start(), "start")
        for call in (server.start, server.serve_forever):
            with pytest.raises(RuntimeError, match="already serving"):
                await waits(call(), call.__name__)
        await waits(server.aclose(), "aclose")

    run(main, loop_factory)


@loops
def test_aclose_from_another_task_ends_serve_forever_without_an_error(loop_factory):
    async def main():
        server = _async_server()
        server.bind()

        async def closer():
            await asyncio.sleep(0.3)
            await waits(server.aclose(), "aclose")

        task = asyncio.ensure_future(closer())
        await waits(server.serve_forever(), "serve_forever")  # returns, raising nothing
        await waits(task, "the closing task")
        assert await waits(server.wait_closed(), "wait_closed") is True

    run(main, loop_factory)


@loops
def test_shutdown_ends_serve_forever_and_the_context_exits(loop_factory):
    async def main():
        async with _async_server() as server:
            asyncio.get_running_loop().call_later(0.2, server.shutdown)
            await waits(server.serve_forever(), "serve_forever")
            assert await waits(server.wait_closed(), "wait_closed") is True

    run(main, loop_factory)


@loops
def test_shutdown_from_another_thread_ends_serve_forever(loop_factory):
    async def main():
        async with _async_server() as server:
            timer = threading.Timer(0.2, server.shutdown)
            timer.name = "tftp-test-timer"
            timer.start()
            try:
                await waits(server.serve_forever(), "serve_forever")
            finally:
                timer.join(WAIT)

    run(main, loop_factory)


@loops
def test_wait_closed_times_out_while_serving_and_aclose_is_repeatable(loop_factory):
    async def main():
        server = _async_server()
        await waits(server.start(), "start")
        assert await waits(server.wait_closed(0.05), "wait_closed") is False
        await asyncio.gather(waits(server.aclose(), "first aclose"), waits(server.aclose(), "second aclose"))
        await waits(server.aclose(), "aclose again")

    run(main, loop_factory)


@loops
def test_async_start_raises_what_bind_raised_and_leaves_nothing(loop_factory):
    async def main():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as held:
            held.bind(("127.0.0.1", 0))
            server = AsyncTFTPServer(
                ThreadedHandler(MemoryBackend({})), host="127.0.0.1", port=held.getsockname()[1], timeout=0.5
            )
            with pytest.raises(OSError):
                await waits(server.start(), "start")
            assert server.server_address is None
            with pytest.raises(OSError):
                server.bind()
        await waits(server.aclose(), "aclose")

    run(main, loop_factory)


@loops
def test_a_stopped_async_server_serves_again_until_it_is_closed(loop_factory):
    async def main():
        server = _async_server()
        for _ in range(2):
            await waits(server.start(), "start")
            server.shutdown()
            assert await waits(server.wait_closed(), "wait_closed") is True
        await waits(server.aclose(), "aclose")

    run(main, loop_factory)
