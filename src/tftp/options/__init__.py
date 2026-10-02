"""Option negotiation (RFC 2347): handlers, registry, server policy, profiles.

Every option is an :class:`OptionHandler` in an :class:`OptionRegistry`, so
an application can add its own (vendor options, an option that selects
content) without touching the transfer code. The RFC options (``blksize``,
``timeout``, ``tsize``, ``windowsize``) are accepted by default; tftp-hpa's
extensions (``blksize2``, ``utimeout``, ``rollover``, ``cookie``) only when a
server allows them, for example through a :class:`Profile`.
"""

from __future__ import annotations

from .base import (
    DEFAULT_BLKSIZE,
    MAX_BLKSIZE,
    MAX_UTIMEOUT,
    MAX_WINDOWSIZE,
    MIN_BLKSIZE,
    MIN_UTIMEOUT,
    ClientContext,
    Negotiated,
    OptionHandler,
    ServerContext,
    parse_int,
    refuse,
)
from .builtin import (
    BUILTIN_OPTIONS,
    Blksize,
    Blksize2,
    Cookie,
    Mstfwindow,
    Rollover,
    Timeout,
    Tsize,
    Utimeout,
    Windowsize,
)
from .negotiate import accept_oack, negotiate, request_options
from .policy import EXTENSION_OPTIONS, STANDARD_OPTIONS, SUPPORTED_OPTIONS, ServerOptions
from .profiles import DEFAULT, HPA, LEGACY, PROFILES, PXE, STRICT, Profile
from .registry import DEFAULT_REGISTRY, OptionRegistry, register_option

__all__ = [
    "DEFAULT_BLKSIZE",
    "MIN_BLKSIZE",
    "MAX_BLKSIZE",
    "MAX_WINDOWSIZE",
    "MIN_UTIMEOUT",
    "MAX_UTIMEOUT",
    "Negotiated",
    "OptionHandler",
    "ServerContext",
    "ClientContext",
    "parse_int",
    "refuse",
    "Blksize",
    "Blksize2",
    "Timeout",
    "Utimeout",
    "Tsize",
    "Windowsize",
    "Rollover",
    "Cookie",
    "Mstfwindow",
    "BUILTIN_OPTIONS",
    "OptionRegistry",
    "DEFAULT_REGISTRY",
    "register_option",
    "ServerOptions",
    "STANDARD_OPTIONS",
    "EXTENSION_OPTIONS",
    "SUPPORTED_OPTIONS",
    "negotiate",
    "accept_oack",
    "request_options",
    "Profile",
    "STRICT",
    "DEFAULT",
    "PXE",
    "HPA",
    "LEGACY",
    "PROFILES",
]
