"""The shapes of the streams a client, a server or a transfer is handed."""

from __future__ import annotations

from typing import Any, Optional, Protocol

__all__ = ["AsyncSink", "AsyncSource", "SupportsRead", "SupportsReadinto", "SupportsWrite"]


class SupportsWrite(Protocol):
    """A sink with ``write(data)``: a binary file, or any object that takes bytes."""

    def write(self, data: Any, /) -> Optional[int]: ...


class SupportsReadinto(Protocol):
    """A source with ``readinto(buffer)``, filling the buffer and returning the count (``None``: not ready)."""

    def readinto(self, buffer: memoryview, /) -> Optional[int]: ...


class SupportsRead(Protocol):
    """A source with ``read(size)`` returning bytes (``None``: not ready)."""

    def read(self, size: int, /) -> Optional[bytes]: ...


class AsyncSource(Protocol):
    """Where an upload reads from: ``await read(n)`` returns up to ``n`` bytes, ``b""`` at the end."""

    async def read(self, size: int, /) -> bytes: ...


class AsyncSink(Protocol):
    """Where a download writes to: ``await write(data)`` takes one chunk."""

    async def write(self, data: bytes, /) -> object: ...
