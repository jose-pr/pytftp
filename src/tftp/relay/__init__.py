"""Transparent TFTP relay with pluggable routing.

See :class:`TFTPRelay`. For a proxy that terminates both sessions (each side
with its own options) use :class:`tftp.backends.UpstreamBackend` with a
``TFTPServer`` instead.
"""

from __future__ import annotations

from ._core import TFTPRelay
from ._routing import RouteFunction, RouteTable, Upstream, by_interface, by_prefix, by_subnet
from ._session import RelaySummary

__all__ = [
    "TFTPRelay",
    "RelaySummary",
    "Upstream",
    "RouteFunction",
    "RouteTable",
    "by_subnet",
    "by_prefix",
    "by_interface",
]
