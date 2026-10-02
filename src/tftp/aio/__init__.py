"""asyncio front ends: :class:`AsyncClient` and :class:`AsyncServer`.

Same engine, options and behaviour as :class:`tftp.Client` and
:class:`tftp.Server`; transfers run on the event loop instead of a thread,
handlers may be ``async def``, and asynchronous streams are bridged to the
engine with backpressure.
"""

from __future__ import annotations

from .bridge import AsyncReaderBridge, AsyncWriterBridge, is_async_reader, is_async_writer
from .client import AsyncClient
from .server import AsyncServer

__all__ = [
    "AsyncClient",
    "AsyncServer",
    "AsyncReaderBridge",
    "AsyncWriterBridge",
    "is_async_reader",
    "is_async_writer",
]
