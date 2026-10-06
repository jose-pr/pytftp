"""Joining asynchronous streams to the transfer engine.

The engine never awaits: it reads and writes synchronously and treats
:class:`WouldBlock` as "come back later". A bridge owns a task that does the
awaiting -- reading from an async source into a bounded buffer, or draining a
bounded buffer into an async sink -- and calls the engine's wake-up when it
can make progress. All of it runs on the event loop thread.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable, Optional

from .exceptions import TFTPError
from .exceptions import WouldBlock

__all__ = ["AsyncReaderBridge", "AsyncWriterBridge", "is_async_reader", "is_async_writer"]


def is_async_reader(obj: Any) -> bool:
    """An object with ``async read(n)``, or an async iterable of bytes."""
    return inspect.iscoroutinefunction(getattr(obj, "read", None)) or hasattr(obj, "__aiter__")


def is_async_writer(obj: Any) -> bool:
    """An object with ``async write(data)``, or ``write`` + ``async drain()`` (StreamWriter)."""
    return inspect.iscoroutinefunction(getattr(obj, "write", None)) or (
        callable(getattr(obj, "write", None)) and inspect.iscoroutinefunction(getattr(obj, "drain", None))
    )


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class AsyncReaderBridge:
    """The engine's (non-blocking) view of an async source.

    :param source: ``async read(n)`` object or async iterable of bytes.
    :param capacity: bytes read ahead at most.
    :param size: total size when known (answers ``tsize``).
    """

    _CHUNK = 65536

    def __init__(self, source: Any, *, capacity: int = 1 << 20, size: Optional[int] = None) -> None:
        self.source = source
        self.capacity = capacity
        self.size = size if size is not None else getattr(source, "size", None)
        self._buffer = bytearray()
        self._eof = False
        self._error: Optional[BaseException] = None
        self._space = asyncio.Event()
        self._space.set()
        self._wakeup: Optional[Callable[[], None]] = None
        self._task = asyncio.get_running_loop().create_task(self._pump())

    def set_wakeup(self, callback: Callable[[], None]) -> None:
        self._wakeup = callback

    def _wake(self) -> None:
        if self._wakeup is not None:
            self._wakeup()

    async def _pump(self) -> None:
        try:
            if hasattr(self.source, "__aiter__") and not inspect.iscoroutinefunction(
                getattr(self.source, "read", None)
            ):
                async for chunk in self.source:
                    await self._space.wait()
                    self._take(chunk)
            else:
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

    def readinto(self, view) -> int:
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
        self._task.cancel()
        closer = getattr(self.source, "aclose", None) or getattr(self.source, "close", None)
        if closer is not None:
            result = closer()
            if inspect.isawaitable(result):
                asyncio.ensure_future(result)


class AsyncWriterBridge:
    """The engine's (non-blocking) view of an async sink.

    :param sink: ``async write(data)``, or ``write`` + ``async drain()``.
    :param capacity: bytes buffered ahead of the sink at most.
    :param close_sink: close the sink once everything is written.

    ``close()`` (what a server calls before the final ACK) raises
    :class:`WouldBlock` until everything is written, so the peer's last
    block is acknowledged only once the data is really out.
    """

    _tftp_copies_ = True

    def __init__(self, sink: Any, *, capacity: int = 1 << 20, close_sink: bool = True) -> None:
        self.sink = sink
        self.capacity = capacity
        self.close_sink = close_sink
        self._buffer = bytearray()
        self._eof = False
        self._finished = False
        self._error: Optional[BaseException] = None
        self._data = asyncio.Event()
        self._wakeup: Optional[Callable[[], None]] = None
        self._done = asyncio.get_running_loop().create_future()
        self._task = asyncio.get_running_loop().create_task(self._pump())

    def set_wakeup(self, callback: Callable[[], None]) -> None:
        self._wakeup = callback

    def _wake(self) -> None:
        if self._wakeup is not None:
            self._wakeup()

    async def _pump(self) -> None:
        drain = getattr(self.sink, "drain", None)
        try:
            while True:
                await self._data.wait()
                if self._buffer:
                    chunk = bytes(self._buffer)
                    self._buffer.clear()
                    self._wake()  # there is room again
                    await _maybe_await(self.sink.write(chunk))
                    if drain is not None and inspect.iscoroutinefunction(drain):
                        await drain()
                    continue
                if self._eof:
                    break
                self._data.clear()
            if self.close_sink:
                closer = getattr(self.sink, "aclose", None) or getattr(self.sink, "close", None)
                if closer is not None:
                    await _maybe_await(closer())
                    waiter = getattr(self.sink, "wait_closed", None)
                    if waiter is not None:
                        await waiter()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._error = exc if isinstance(exc, TFTPError) else TFTPError(3, "sink failed")
            self._error.__cause__ = exc
        self._finished = True
        if not self._done.done():
            self._done.set_result(None)
        self._wake()

    def write(self, data) -> int:
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
        self._task.cancel()

    async def finish(self) -> None:
        """Wait until everything is written (and the sink closed, if asked); re-raise its error."""
        self._eof = True
        self._data.set()
        await self._done
        if self._error is not None:
            raise self._error
