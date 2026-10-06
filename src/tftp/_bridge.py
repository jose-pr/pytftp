"""Joining asynchronous streams to the transfer engine.

The engine never awaits: it reads and writes synchronously and treats
:class:`WouldBlock` as "come back later". A bridge owns a task that does the
awaiting -- reading from an async source into a bounded buffer, or draining a
bounded buffer into an async sink -- and calls the engine's wake-up when it
can make progress. All of it runs on the event loop thread.

A bridge's task starts with the bridge, or at :meth:`start` when it is built
with ``start=False``; its owner ends the task with :meth:`aclose`, which never
closes the stream it was given.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional, Union

from .exceptions import TFTPError
from .exceptions import WouldBlock
from ._streams import AsyncSink, AsyncSource

__all__ = ["AsyncReaderBridge", "AsyncWriterBridge"]


async def _end(task: "Optional[asyncio.Task[None]]") -> None:
    """Cancel ``task`` and wait until it is over; its outcome is not looked at."""
    if task is not None and not task.done():
        task.cancel()
        await asyncio.wait({task})


class AsyncReaderBridge:
    """The engine's (non-blocking) view of an async source.

    :param source: an object with ``async read(n)`` returning ``b""`` at the end.
    :param capacity: bytes read ahead at most.
    :param size: total size when known (answers ``tsize``).
    :param start: read from the source at once; ``False`` waits for :meth:`start`.
    """

    _CHUNK = 65536

    def __init__(
        self, source: AsyncSource, *, capacity: int = 1 << 20, size: Optional[int] = None, start: bool = True
    ) -> None:
        self.source = source
        self.capacity = capacity
        self.size = size if size is not None else getattr(source, "size", None)
        self._buffer = bytearray()
        self._eof = False
        self._error: Optional[BaseException] = None
        self._space = asyncio.Event()
        self._space.set()
        self._wakeup: Optional[Callable[[], object]] = None
        self._task: Optional["asyncio.Task[None]"] = None
        if start:
            self.start()

    def start(self) -> None:
        """Begin reading from the source (once)."""
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._pump())

    def set_wakeup(self, callback: Callable[[], object]) -> None:
        self._wakeup = callback

    def _wake(self) -> None:
        if self._wakeup is not None:
            self._wakeup()

    async def _pump(self) -> None:
        try:
            while True:
                await self._space.wait()
                chunk = await self.source.read(self._CHUNK)
                if not chunk:
                    break
                self._take(chunk)
            self._eof = True
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._error = exc if isinstance(exc, TFTPError) else TFTPError(0, "source failed")
            self._error.__cause__ = exc
        self._wake()

    def _take(self, chunk: Any) -> None:
        self._buffer += chunk
        if len(self._buffer) >= self.capacity:
            self._space.clear()
        self._wake()

    def readinto(self, view: Union[bytearray, memoryview]) -> int:
        if self._buffer:
            n = min(len(view), len(self._buffer))
            view[:n] = self._buffer[:n]
            del self._buffer[:n]
            if len(self._buffer) < self.capacity:
                self._space.set()
            return n
        if self._error is not None:
            raise self._error
        if self._eof:
            return 0
        raise WouldBlock()

    def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
        asyncio.ensure_future(self.source.close())  # type: ignore[attr-defined]  # a server's reader has close()

    async def aclose(self) -> None:
        """End the task, waiting for it; the source is left open."""
        await _end(self._task)


class AsyncWriterBridge:
    """The engine's (non-blocking) view of an async sink.

    :param sink: an object with ``async write(data)`` and, with ``close_sink``, ``async close()``.
    :param capacity: bytes buffered ahead of the sink at most.
    :param close_sink: close the sink once everything is written.
    :param start: take from the buffer at once; ``False`` waits for :meth:`start`.

    ``close()`` (what a server calls before the final ACK) raises
    :class:`WouldBlock` until everything is written, so the peer's last
    block is acknowledged only once the data is really out.
    """

    copies_writes = True

    def __init__(
        self, sink: AsyncSink, *, capacity: int = 1 << 20, close_sink: bool = True, start: bool = True
    ) -> None:
        self.sink = sink
        self.capacity = capacity
        self.close_sink = close_sink
        self._buffer = bytearray()
        self._eof = False
        self._finished = False
        self._error: Optional[BaseException] = None
        self._data = asyncio.Event()
        self._wakeup: Optional[Callable[[], object]] = None
        self._done = asyncio.get_running_loop().create_future()
        self._task: Optional["asyncio.Task[None]"] = None
        if start:
            self.start()

    def start(self) -> None:
        """Begin writing to the sink (once)."""
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._pump())

    def set_wakeup(self, callback: Callable[[], object]) -> None:
        self._wakeup = callback

    def _wake(self) -> None:
        if self._wakeup is not None:
            self._wakeup()

    async def _pump(self) -> None:
        try:
            while True:
                await self._data.wait()
                if self._buffer:
                    chunk = bytes(self._buffer)
                    self._buffer.clear()
                    self._wake()  # there is room again
                    await self.sink.write(chunk)
                    continue
                if self._eof:
                    break
                self._data.clear()
            if self.close_sink:
                await self.sink.close()  # type: ignore[attr-defined]  # close_sink is for a sink with close()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._error = exc if isinstance(exc, TFTPError) else TFTPError(3, "sink failed")
            self._error.__cause__ = exc
        self._finished = True
        if not self._done.done():
            self._done.set_result(None)
        self._wake()

    def write(self, data: Union[bytes, bytearray, memoryview]) -> int:
        if self._error is not None:
            raise self._error
        if self._buffer and len(self._buffer) + len(data) > self.capacity:
            raise WouldBlock()
        self._buffer += data
        self._data.set()
        return len(data)

    def close(self) -> None:
        self._eof = True
        self._data.set()
        if not self._finished:
            raise WouldBlock()
        if self._error is not None:
            raise self._error

    def abort(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def aclose(self) -> None:
        """End the task, waiting for it; the sink is left open unless ``close_sink`` closed it."""
        await _end(self._task)

    async def finish(self) -> None:
        """Wait until everything is written (and the sink closed, if asked); re-raise its error."""
        self.start()
        self._eof = True
        self._data.set()
        await self._done
        if self._error is not None:
            raise self._error
