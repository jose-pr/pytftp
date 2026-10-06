"""The clients: :class:`TFTPClient` (blocking) and :class:`AsyncTFTPClient` (asyncio).

Siblings over one private base: the same arguments, the same options and the
same results, one waiting on sockets and one on the event loop. The one-shot
functions :func:`download` and :func:`upload` use the blocking client.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, List

from .._streams import AsyncSink, AsyncSource
from ._core import MODES, ProgressFunction, RemoteStat, SinkLike, SourceLike
from ._sync import TFTPClient, download, upload

if TYPE_CHECKING:
    from ._asyncio import AsyncTFTPClient

__all__ = [
    "AsyncSink",
    "AsyncSource",
    "AsyncTFTPClient",
    "MODES",
    "ProgressFunction",
    "RemoteStat",
    "SinkLike",
    "SourceLike",
    "TFTPClient",
    "download",
    "upload",
]


def __getattr__(name: str) -> Any:
    """Bind :class:`AsyncTFTPClient` on first use, so importing the package does not import asyncio."""
    if name == "AsyncTFTPClient":
        from ._asyncio import AsyncTFTPClient

        globals()[name] = AsyncTFTPClient
        return AsyncTFTPClient
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def __dir__() -> List[str]:
    return sorted(set(globals()) | set(__all__))
