"""Transparent TFTP relay with pluggable routing.

See :class:`TFTPRelay` and its asyncio twin :class:`AsyncTFTPRelay`. For a proxy that
terminates both sessions (each side with its own options) use
:class:`tftp.backends.UpstreamBackend` with a ``TFTPServer`` instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, List

from ._sync import TFTPRelay
from ._routing import (
    AsyncRouteFunction,
    RouteFunction,
    RouteTable,
    Upstream,
    UpstreamLike,
    by_interface,
    by_prefix,
    by_subnet,
)
from ._session import RelaySummary

if TYPE_CHECKING:
    from ._asyncio import AsyncTFTPRelay

__all__ = [
    "AsyncTFTPRelay",
    "TFTPRelay",
    "RelaySummary",
    "Upstream",
    "UpstreamLike",
    "RouteFunction",
    "AsyncRouteFunction",
    "RouteTable",
    "by_subnet",
    "by_prefix",
    "by_interface",
]


def __getattr__(name: str) -> Any:
    """Bind :class:`AsyncTFTPRelay` on first use, so importing the package does not import asyncio."""
    if name == "AsyncTFTPRelay":
        from ._asyncio import AsyncTFTPRelay

        globals()[name] = AsyncTFTPRelay
        return AsyncTFTPRelay
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def __dir__() -> List[str]:
    return sorted(set(globals()) | set(__all__))
