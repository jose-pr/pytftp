"""The asyncio client: :class:`Client`'s options and rules, without a thread per transfer."""

from __future__ import annotations

import asyncio
import errno
import io
import os
import socket
import time
from typing import Any, AsyncIterator, List, Optional, Tuple

from ..capture.events import PacketEvent, new_session_id
from ..client import Client, Progress, RemoteStat, _NotListing, _mode, _source_size
from ..listing import ListEntry, parse_listing
from ..errors import RemoteError, TransferTimeout
from ..netascii import NetasciiReader, NetasciiWriter, encoded_size
from ..options import DEFAULT_BLKSIZE
from ..packet import ErrorCode, Opcode, encode_ack, encode_error, encode_request
from ..result import TransferResult
from ..transfer import Receiver, Sender, Transfer, as_readinto, as_write
from .._sockets import fit_window
from .bridge import AsyncReaderBridge, AsyncWriterBridge, is_async_reader, is_async_writer

__all__ = ["AsyncClient"]


class _Protocol(asyncio.DatagramProtocol):
    """Feeds datagrams to the request phase, then to the transfer engine."""

    def __init__(self, driver: "_Transfer") -> None:
        self.driver = driver

    def datagram_received(self, data: bytes, addr: Tuple[Any, ...]) -> None:
        self.driver.received(data, addr)

    def error_received(self, exc: Exception) -> None:  # ICMP errors: loss
        pass


class _Transfer:
    """One client transfer on the event loop."""

    def __init__(self, client: "AsyncClient", loop: asyncio.AbstractEventLoop) -> None:
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
                if len(data) >= 2 and (not self.client.strict_source or addr[0] == self.server[0]):
                    self.peer = addr
                    self.first.set_result((data, addr))
            return
        peer = self.peer
        if addr[1] != peer[1] or addr[0] != peer[0]:  # type: ignore[index]
            self.sendto(encode_error(ErrorCode.UNKNOWN_TID), addr)
            return
        engine = self.engine
        engine.handle(memoryview(data), len(data), self.loop.time())
        self.after()

    # -- engine bookkeeping --------------------------------------------------------

    def after(self) -> None:
        engine = self.engine
        assert engine is not None
        if self.progress is not None and engine.bytes != self.reported:
            self.reported = engine.bytes
            self.progress(self.reported, self.total)
        if engine.done:
            if self.timer is not None:
                self.timer.cancel()
            if not self.done.done():
                self.done.set_result(None)
            return
        self.schedule()

    def schedule(self) -> None:
        engine = self.engine
        due = engine.deadline if engine is not None else None
        if due is None:
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
        if engine is None or engine.done:
            return
        now = self.loop.time()
        if engine.deadline is not None and engine.deadline > now:
            self.schedule()  # moved later since this was scheduled
            return
        engine.on_timeout(now)
        self.after()

    def resume(self) -> None:
        engine = self.engine
        if engine is not None and not engine.done:
            engine.resume(self.loop.time())
            self.after()


class AsyncClient(Client):
    """:class:`Client` for asyncio: the same arguments, coroutine methods.

    Sources and destinations may be what :class:`Client` takes (paths,
    binary files, bytes -- local file I/O happens on the loop, which is fine
    for files and wrong for anything slow) or asynchronous: an object with
    ``async read(n)`` / an async iterable of bytes to upload, an object with
    ``async write(data)`` (or ``write`` plus ``async drain()``, such as
    :class:`asyncio.StreamWriter`) to download into. Those are driven with
    backpressure: a slow sink slows the transfer instead of filling memory.
    """

    async def download(
        self,
        filename: str,
        dest: Any,
        *,
        mode: str = "octet",
        progress: Optional[Progress] = None,
    ) -> TransferResult:
        """Fetch ``filename`` into ``dest``: a path, a binary file or an async writer."""
        mode = _mode(mode)
        if isinstance(dest, (str, os.PathLike)):
            path = os.fspath(dest)
            fileobj = open(path, "wb")
            try:
                result = await self._download(filename, fileobj, mode, progress)
            except BaseException:
                fileobj.close()
                try:
                    os.unlink(path)
                except OSError:
                    pass
                raise
            fileobj.close()
            return result
        return await self._download(filename, dest, mode, progress)

    async def get(self, filename: str, *, mode: str = "octet") -> bytes:  # type: ignore[override]
        buffer = io.BytesIO()
        await self.download(filename, buffer, mode=mode)
        return buffer.getvalue()

    async def upload(  # type: ignore[override]
        self,
        filename: str,
        source: Any,
        *,
        mode: str = "octet",
        progress: Optional[Progress] = None,
    ) -> TransferResult:
        """Send ``source``: a path, bytes, a binary file, or an async reader/iterable."""
        mode = _mode(mode)
        if isinstance(source, (str, os.PathLike)):
            with open(os.fspath(source), "rb") as fileobj:
                return await self._upload(filename, fileobj, mode, progress)
        if isinstance(source, (bytes, bytearray, memoryview)):
            return await self._upload(filename, io.BytesIO(bytes(source)), mode, progress)
        return await self._upload(filename, source, mode, progress)

    async def put(self, filename: str, data: bytes, *, mode: str = "octet") -> TransferResult:  # type: ignore[override]
        return await self.upload(filename, data, mode=mode)

    async def size(self, filename: str, *, mode: str = "octet") -> Optional[int]:  # type: ignore[override]
        """:meth:`Client.size`, without blocking the loop (it runs in the executor)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: Client.size(self, filename, mode=mode))

    async def stat(self, filename: str, *, mode: str = "octet") -> RemoteStat:  # type: ignore[override]
        """:meth:`Client.stat`, without blocking the loop (it runs in the executor)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: Client.stat(self, filename, mode=mode))

    async def listdir(self, dirname: str = "") -> List[ListEntry]:  # type: ignore[override]
        """:meth:`Client.listdir`, as a coroutine."""
        lister = self._lister()
        sink = io.BytesIO()
        try:
            await lister.download(dirname or ".", sink)
        except _NotListing:
            raise NotADirectoryError(errno.ENOTDIR, "not a directory", dirname) from None
        return parse_listing(sink.getvalue())

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
        self, filename: str, sink: Any, mode: str, progress: Optional[Progress]
    ) -> TransferResult:
        bridge = None
        if is_async_writer(sink):
            bridge = AsyncWriterBridge(sink, close_sink=False)
            target: Any = bridge
        else:
            target = sink
        writer: Any = NetasciiWriter(target) if mode == "netascii" else target
        result = await self._run(Opcode.RRQ, filename, mode, None, as_write(writer), None, progress, bridge)
        if mode == "netascii":
            writer.flush()
        if bridge is not None:
            await bridge.finish()
        return result

    async def _upload(
        self, filename: str, source: Any, mode: str, progress: Optional[Progress]
    ) -> TransferResult:
        bridge = None
        if is_async_reader(source):
            bridge = AsyncReaderBridge(source)
            source = bridge
        size: Optional[int]
        if mode == "netascii":
            size = encoded_size(source) if bridge is None else None
            reader: Any = NetasciiReader(source)
        else:
            size = _source_size(source) if bridge is None else bridge.size
            reader = source
        return await self._run(Opcode.WRQ, filename, mode, size, None, as_readinto(reader), progress, bridge)

    async def _run(self, opcode, filename, mode, size, write, read, progress, bridge) -> TransferResult:
        from netimps import bind, get_ip, normalize_host

        loop = asyncio.get_running_loop()
        host, port = normalize_host(self.host, self.port)
        ipv6 = {socket.AF_INET6: True, socket.AF_INET: False}.get(self.family)
        address = await loop.run_in_executor(None, get_ip, host, ipv6)  # DNS off the loop
        if address is None:
            raise socket.gaierror("cannot resolve %r" % host)
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        server = (str(address), port)
        options = self._options(opcode == Opcode.RRQ, size, address)
        local_host, local_port = self.local_address or (("::" if family == socket.AF_INET6 else "0.0.0.0"), 0)
        started = time.monotonic()
        attempts = [options, {}] if options and self.fallback else [options]
        for attempt, attempt_options in enumerate(attempts):
            # Each attempt gets its own socket (the transport closes it).
            sock = bind(local_host, local_port, family=family, connreset=False)
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
                if not (
                    exc.code == ErrorCode.OPTION_REFUSED
                    and getattr(exc, "_in_request", False)
                    and attempt + 1 < len(attempts)
                ):
                    raise
            finally:
                sock.close()
        raise AssertionError("unreachable")  # pragma: no cover

    async def _exchange_async(
        self, loop, sock, server, opcode, filename, mode, options, write, read, progress, started, bridge
    ) -> TransferResult:
        is_read = opcode == Opcode.RRQ
        request = encode_request(opcode, filename, mode, options)
        fit_window(sock, int(options.get("blksize", DEFAULT_BLKSIZE)), int(options.get("windowsize", 1)))
        driver = _Transfer(self, loop)
        driver.server = server
        driver.local = sock.getsockname()
        driver.progress = progress
        transport, _ = await loop.create_datagram_endpoint(lambda: _Protocol(driver), sock=sock)
        driver.transport = transport
        try:
            expires = None if self.max_duration is None else loop.time() + self.max_duration
            engine_kwargs = {"backoff": self.backoff, "max_timeout": self.max_timeout, "expires": expires}
            # Request phase, with the same backoff as retransmissions.
            wait = self.timeout
            for attempt in range(self.retries + 1):
                driver.sendto(request, server)
                try:
                    data, peer = await asyncio.wait_for(asyncio.shield(driver.first), wait)
                    break
                except asyncio.TimeoutError:
                    if expires is not None and loop.time() >= expires:
                        break
                    wait = min(wait * self.backoff, self.max_timeout)
            else:
                data = None  # type: ignore[assignment]
            if not driver.first.done():
                raise TransferTimeout("no response from %s:%s" % server[:2])
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
            if driver.engine is not None and not driver.engine.done:
                driver.engine.abort("cancelled")
            transport.abort()
