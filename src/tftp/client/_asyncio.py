"""The asyncio client: the synchronous client's options and rules, without a thread per transfer."""

from __future__ import annotations

import asyncio
import errno
import io
import os
import socket
import time
from typing import Any, AsyncIterator, List, Optional, Tuple

from ..capture.events import PacketEvent, new_session_id
from .. import listing
from ..listing import ListEntry
from ..exceptions import RemoteError, TransferTimeoutError
from ..netascii import NetasciiReader, NetasciiWriter, encoded_size
from ..options import DEFAULT_BLKSIZE
from ..packet import TFTPErrorCode, TFTPOpcode, encode_ack, encode_request
from ..packet.codec import _encode_error
from ..result import TransferResult
from ..transfer import Receiver, Sender, Transfer, as_readinto, as_write
from .._bridge import AsyncReaderBridge, AsyncWriterBridge
from .._sockets import fit_window, same_host
from ._core import (
    Progress,
    RemoteStat,
    _ClientBase,
    _mode,
    _NotListing,
    _repeats_without_options,
    _source_size,
)

__all__ = ["AsyncTFTPClient"]


class _Protocol(asyncio.DatagramProtocol):
    """Feeds datagrams to the request phase, then to the transfer engine."""

    def __init__(self, driver: "_Transfer") -> None:
        self.driver = driver

    def datagram_received(self, data: bytes, addr: Tuple[Any, ...]) -> None:
        self.driver.received(data, addr)

    def error_received(self, exc: Exception) -> None:
        self.driver.failed(exc)


class _Transfer:
    """One client transfer on the event loop."""

    def __init__(self, client: "AsyncTFTPClient", loop: asyncio.AbstractEventLoop) -> None:
        self.client = client
        self.loop = loop
        self.transport: Optional[asyncio.DatagramTransport] = None
        self.server: Tuple[Any, ...] = ()
        self.peer: Optional[Tuple[Any, ...]] = None
        self.first: "asyncio.Future[Tuple[bytes, Tuple[Any, ...]]]" = loop.create_future()
        self.done: "asyncio.Future[None]" = loop.create_future()
        self.engine: Optional[Transfer] = None
        self.timer: Optional[asyncio.TimerHandle] = None
        self.timer_at: Optional[float] = None
        self.progress: Optional[Progress] = None
        self.total: Optional[int] = None
        self.reported = -1
        self.trace = client.trace
        self.session_id = new_session_id("c") if self.trace is not None else None
        self.local: Tuple[Any, ...] = ()

    # -- I/O ---------------------------------------------------------------------

    def emit(self, data: bytes, direction: str, remote: Tuple[Any, ...]) -> None:
        try:
            self.trace(  # type: ignore[misc]
                PacketEvent(
                    time.time(), direction, self.local, remote, bytes(data), "client", self.session_id
                )
            )
        except Exception:
            pass

    def sendto(self, data, addr: Tuple[Any, ...]) -> None:
        assert self.transport is not None
        self.transport.sendto(bytes(data), addr)
        if self.trace is not None:
            self.emit(data, "out", addr)

    def send(self, packet) -> None:
        self.sendto(packet, self.peer)  # type: ignore[arg-type]

    def received(self, data: bytes, addr: Tuple[Any, ...]) -> None:
        if self.trace is not None:
            self.emit(data, "in", addr)
        if self.engine is None:
            if self.peer is None and not self.first.done():
                if len(data) >= 2 and (not self.client.strict_source or same_host(addr, self.server)):
                    self.peer = addr
                    self.first.set_result((data, addr))
            return
        peer = self.peer
        if addr[1] != peer[1] or addr[0] != peer[0]:  # type: ignore[index]
            self.sendto(_encode_error(TFTPErrorCode.UNKNOWN_TID), addr)
            return
        engine = self.engine
        engine.handle(memoryview(data), len(data), self.loop.time())
        self.after()

    def failed(self, exc: Exception) -> None:
        """The transport reported an error.

        An ICMP report (the peer's port is closed) is loss, which the
        retransmission timer deals with. Anything else is the host refusing
        a send: it ends the transfer with that error rather than leaving the
        caller to wait out every retry for an answer that was never asked for.
        """
        if isinstance(exc, (ConnectionRefusedError, ConnectionResetError)):
            return
        future = self.first if self.engine is None else self.done
        if not future.done():
            future.set_exception(exc)

    # -- engine bookkeeping --------------------------------------------------------

    def after(self) -> None:
        engine = self.engine
        assert engine is not None
        if self.progress is not None and engine.bytes != self.reported:
            self.reported = engine.bytes
            self.progress(self.reported, self.total)
        if engine.is_done:
            if self.timer is not None:
                self.timer.cancel()
            if not self.done.done():
                self.done.set_result(None)
            return
        self.schedule()

    def schedule(self) -> None:
        engine = self.engine
        if engine is None or engine.is_done:
            return
        due = engine.deadline
        if due is None:
            if engine.is_stalled:
                return  # the bridge wakes the engine when its source or sink is ready
            # Nothing outstanding: wait for a datagram as long as the blocking
            # client does, counted from now.
            if self.timer is not None:
                self.timer.cancel()
            self.timer_at = self.loop.time() + self.client.timeout
            self.timer = self.loop.call_at(self.timer_at, self.fire)
            return
        if self.timer_at is None or due < self.timer_at:
            if self.timer is not None:
                self.timer.cancel()
            self.timer_at = due
            self.timer = self.loop.call_at(due, self.fire)

    def fire(self) -> None:
        self.timer = None
        self.timer_at = None
        engine = self.engine
        if engine is None or engine.is_done:
            return
        now = self.loop.time()
        if engine.deadline is not None and engine.deadline > now:
            self.schedule()  # moved later since this was scheduled
            return
        engine.on_timeout(now)
        self.after()

    def resume(self) -> None:
        engine = self.engine
        if engine is not None and not engine.is_done:
            engine.resume(self.loop.time())
            self.after()


class AsyncTFTPClient(_ClientBase):
    """:class:`tftp.TFTPClient` for asyncio: the same arguments, coroutine methods.

    A source or destination is a path (or ``bytes``, to upload) -- local file
    I/O happens on the loop, which is fine for files and wrong for anything
    slow -- or an asynchronous stream: an object with ``async read(n)`` to
    upload from, an object with ``async write(data)`` to download into. Those
    are driven with backpressure: a slow sink slows the transfer instead of
    filling memory. The client never closes a stream it was given.
    """

    async def download(
        self,
        filename: str,
        dst: Any,
        *,
        mode: str = "octet",
        progress: Optional[Progress] = None,
    ) -> TransferResult:
        """Fetch ``filename`` into ``dst``: a path, or an object with ``async write(data)``."""
        mode = _mode(mode)
        if isinstance(dst, (str, os.PathLike)):
            path = os.fspath(dst)
            fileobj = open(path, "wb")
            try:
                result = await self._download(filename, fileobj, mode, progress, bridged=False)
            except BaseException:
                fileobj.close()
                try:
                    os.unlink(path)
                except OSError:
                    pass
                raise
            fileobj.close()
            return result
        return await self._download(filename, dst, mode, progress, bridged=True)

    async def get(self, filename: str, *, mode: str = "octet") -> bytes:
        buffer = io.BytesIO()
        await self._download(filename, buffer, mode, None, bridged=False)
        return buffer.getvalue()

    async def upload(
        self,
        filename: str,
        src: Any,
        *,
        mode: str = "octet",
        progress: Optional[Progress] = None,
    ) -> TransferResult:
        """Send ``src``: a path, bytes, or an object with ``async read(n)``."""
        mode = _mode(mode)
        if isinstance(src, (str, os.PathLike)):
            with open(os.fspath(src), "rb") as fileobj:
                return await self._upload(filename, fileobj, mode, progress, bridged=False)
        if isinstance(src, (bytes, bytearray, memoryview)):
            return await self._upload(filename, io.BytesIO(bytes(src)), mode, progress, bridged=False)
        return await self._upload(filename, src, mode, progress, bridged=True)

    async def put(self, filename: str, data: bytes, *, mode: str = "octet") -> TransferResult:
        return await self.upload(filename, data, mode=mode)

    async def size(self, filename: str, *, mode: str = "octet") -> Optional[int]:
        """:meth:`tftp.TFTPClient.size`, without blocking the loop (it runs in the executor)."""
        loop = asyncio.get_running_loop()
        expires = self._expires(time.monotonic())
        return await loop.run_in_executor(None, lambda: self._size(filename, mode, expires))

    async def stat(self, filename: str, *, mode: str = "octet") -> RemoteStat:
        """:meth:`tftp.TFTPClient.stat`, without blocking the loop (it runs in the executor)."""
        loop = asyncio.get_running_loop()
        expires = self._expires(time.monotonic())
        return await loop.run_in_executor(None, lambda: self._stat(filename, mode, expires))

    async def listdir(self, dirname: str = "") -> List[ListEntry]:
        """:meth:`tftp.TFTPClient.listdir`, as a coroutine."""
        lister = self._lister()
        sink = io.BytesIO()
        try:
            await lister._download(dirname or ".", sink, "octet", None, bridged=False)
        except _NotListing:
            raise NotADirectoryError(errno.ENOTDIR, "not a directory", dirname) from None
        return listing.loads(sink.getvalue())

    async def stream(
        self, filename: str, *, mode: str = "octet", buffer: int = 1 << 20
    ) -> AsyncIterator[bytes]:
        """Yield ``filename``'s contents as it arrives, with backpressure.

        Only ``buffer`` bytes are held at a time; when the consumer is slow,
        acknowledgements are held back and the server waits.
        """
        queue: "asyncio.Queue[Optional[bytes]]" = asyncio.Queue()
        held = [0]
        room = asyncio.Event()
        room.set()

        class _Sink:
            async def write(self, data: bytes) -> None:
                held[0] += len(data)
                if held[0] >= buffer:
                    room.clear()
                await queue.put(data)
                await room.wait()

        task = asyncio.ensure_future(self.download(filename, _Sink(), mode=mode))

        def finished(_: Any) -> None:
            queue.put_nowait(None)

        task.add_done_callback(finished)
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                held[0] -= len(chunk)
                if held[0] < buffer:
                    room.set()
                yield chunk
            await task  # re-raise a transfer error
        finally:
            if not task.done():
                task.cancel()

    # -- internals ---------------------------------------------------------------

    async def _download(
        self, filename: str, sink: Any, mode: str, progress: Optional[Progress], *, bridged: bool
    ) -> TransferResult:
        bridge = None
        if bridged:
            bridge = AsyncWriterBridge(sink, close_sink=False)
            target: Any = bridge
        else:
            target = sink
        writer: Any = NetasciiWriter(target) if mode == "netascii" else target
        result = await self._run(
            TFTPOpcode.RRQ, filename, mode, None, as_write(writer), None, progress, bridge
        )
        if mode == "netascii":
            writer.flush()
        if bridge is not None:
            await bridge.finish()
        return result

    async def _upload(
        self, filename: str, source: Any, mode: str, progress: Optional[Progress], *, bridged: bool
    ) -> TransferResult:
        bridge = None
        if bridged:
            bridge = AsyncReaderBridge(source)
            source = bridge
        size: Optional[int]
        if mode == "netascii":
            size = encoded_size(source) if bridge is None else None
            reader: Any = NetasciiReader(source)
        else:
            size = _source_size(source) if bridge is None else bridge.size
            reader = source
        return await self._run(
            TFTPOpcode.WRQ, filename, mode, size, None, as_readinto(reader), progress, bridge
        )

    async def _run(self, opcode, filename, mode, size, write, read, progress, bridge) -> TransferResult:
        from netimps import bind

        loop = asyncio.get_running_loop()
        family, server, address = await loop.run_in_executor(None, self._endpoint)  # DNS off the loop
        options = self._options(opcode == TFTPOpcode.RRQ, size, address)
        local_host, local_port = self.src or (("::" if family == socket.AF_INET6 else "0.0.0.0"), 0)
        started = time.monotonic()
        attempts = [options, {}] if options and self.fallback else [options]
        for attempt, attempt_options in enumerate(attempts):
            # Each attempt gets its own socket (the transport closes it).
            sock = bind(local_host, local_port, family=family)
            sock.setblocking(False)
            try:
                return await self._exchange_async(
                    loop,
                    sock,
                    server,
                    opcode,
                    filename,
                    mode,
                    attempt_options,
                    write,
                    read,
                    progress,
                    started,
                    bridge,
                )
            except RemoteError as exc:
                # Retry without options only when the request itself was
                # refused for them: nothing has been read or written yet.
                if not (_repeats_without_options(exc) and attempt + 1 < len(attempts)):
                    raise
            finally:
                sock.close()
        raise AssertionError("unreachable")  # pragma: no cover

    async def _exchange_async(
        self, loop, sock, server, opcode, filename, mode, options, write, read, progress, started, bridge
    ) -> TransferResult:
        is_read = opcode == TFTPOpcode.RRQ
        request = encode_request(opcode, filename, mode=mode, options=options)
        fit_window(sock, int(options.get("blksize", DEFAULT_BLKSIZE)), int(options.get("windowsize", 1)))
        driver = _Transfer(self, loop)
        driver.server = server
        driver.local = sock.getsockname()
        driver.progress = progress
        transport, _ = await loop.create_datagram_endpoint(lambda: _Protocol(driver), sock=sock)
        driver.transport = transport
        try:
            budget = self._expires(started)
            expires = None if budget is None else loop.time() + (budget - time.monotonic())
            engine_kwargs = {"backoff": self.backoff, "max_timeout": self.max_timeout, "expires": expires}
            # Request phase, with the same backoff as retransmissions.
            from netimps import Backoff

            timer = Backoff(self.timeout, multiplier=self.backoff, max_delay=self.max_timeout)
            while expires is None or loop.time() < expires:
                driver.sendto(request, server)
                wait = timer.delay if expires is None else min(timer.delay, expires - loop.time())
                try:
                    await asyncio.wait_for(asyncio.shield(driver.first), wait)
                    break
                except asyncio.TimeoutError:
                    if timer.attempt >= self.retries:
                        break
                    timer.advance()
            if not driver.first.done():
                raise TransferTimeoutError("no response from %s:%s" % server[:2])
            data, peer = driver.first.result()
            view = memoryview(data)
            negotiated, first_data = self._first_response(view, len(data), options, is_read, driver.send)
            self._negotiated(negotiated, peer, driver.send)
            driver.total = negotiated.tsize
            now = loop.time()
            if is_read:
                reply = None if first_data else encode_ack(0)
                driver.engine = Receiver(
                    driver.send, write, negotiated, self.retries, now, reply=reply, **engine_kwargs
                )
                if first_data:
                    driver.engine.handle(view, len(data), now)
            else:
                driver.engine = Sender(driver.send, read, negotiated, self.retries, now, **engine_kwargs)
            if bridge is not None:
                bridge.set_wakeup(lambda: loop.call_soon(driver.resume))
            driver.after()
            await driver.done
            engine = driver.engine
            if engine.error is not None:
                raise engine.error
            if is_read and self.dally:
                await asyncio.sleep(negotiated.timeout)  # the protocol keeps re-ACKing meanwhile
            return TransferResult(
                filename,
                "read" if is_read else "write",
                mode,
                peer,
                driver.local,
                engine.bytes,
                engine.blocks,
                engine.retransmits,
                time.monotonic() - started,
                negotiated,
            )
        finally:
            if driver.timer is not None:
                driver.timer.cancel()
            if driver.engine is not None and not driver.engine.is_done:
                driver.engine.abort("cancelled")
            transport.abort()
