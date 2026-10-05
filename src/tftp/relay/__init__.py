"""Transparent TFTP relay with pluggable routing.

See :class:`TFTPRelay`. For a proxy that terminates both sessions (each side
with its own options) use :class:`tftp.backends.UpstreamHandler` with a
``TFTPServer`` instead.
"""

from __future__ import annotations

from .core import TFTPRelay
from .routing import Route, RouteTable, Upstream, by_interface, by_prefix, by_subnet, upstream
from .session import RelaySummary

__all__ = [
    "TFTPRelay",
    "RelaySummary",
    "Upstream",
    "upstream",
    "Route",
    "RouteTable",
    "by_subnet",
    "by_prefix",
    "by_interface",
]
