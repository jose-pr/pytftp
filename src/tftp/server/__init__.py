"""The TFTP servers (blocking and asyncio), their request handlers, and the per-transfer session."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, List

from ._sync import TFTPServer
from .handler import (
    AsyncTFTPHandler,
    AsyncTFTPReader,
    AsyncTFTPWriter,
    AtomicWriter,
    TFTPChunkReader,
    TFTPHandler,
    TFTPReader,
    TFTPRequestContext,
    TFTPWriter,
    ThreadedHandler,
)
from .policy import TFTPServerLimits
from .session import PortRange, PortRangeLike
from .stats import TFTPStats

if TYPE_CHECKING:
    from ._asyncio import AsyncTFTPServer

__all__ = [
    "AsyncTFTPServer",
    "TFTPServer",
    "TFTPServerLimits",
    "PortRange",
    "PortRangeLike",
    "TFTPStats",
    "TFTPHandler",
    "TFTPReader",
    "TFTPChunkReader",
    "TFTPWriter",
    "AsyncTFTPHandler",
    "AsyncTFTPReader",
    "AsyncTFTPWriter",
    "ThreadedHandler",
    "AtomicWriter",
    "TFTPRequestContext",
]


def __getattr__(name: str) -> Any:
    """Bind :class:`AsyncTFTPServer` on first use, so importing the package does not import asyncio."""
    if name == "AsyncTFTPServer":
        from ._asyncio import AsyncTFTPServer

        globals()[name] = AsyncTFTPServer
        return AsyncTFTPServer
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def __dir__() -> List[str]:
    return sorted(set(globals()) | set(__all__))
