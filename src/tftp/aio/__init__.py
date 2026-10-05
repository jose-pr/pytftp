"""asyncio front ends: :class:`AsyncTFTPClient` and :class:`AsyncTFTPServer`.

Same engine, options and behaviour as :class:`tftp.TFTPClient` and
:class:`tftp.TFTPServer`; transfers run on the event loop instead of a thread,
handlers may be ``async def``, and asynchronous streams are bridged to the
engine with backpressure.
"""

from __future__ import annotations

from .bridge import AsyncReaderBridge, AsyncWriterBridge, is_async_reader, is_async_writer
from .client import AsyncTFTPClient
from .server import AsyncTFTPServer

__all__ = [
    "AsyncTFTPClient",
    "AsyncTFTPServer",
    "AsyncReaderBridge",
    "AsyncWriterBridge",
    "is_async_reader",
    "is_async_writer",
]
