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
    ClientOptionContext,
    Negotiated,
    OptionHandler,
    ServerOptionContext,
    parse_int,
    refuse,
)
from .builtin import (
    BUILTIN_OPTIONS,
    BlksizeOption,
    Blksize2Option,
    CookieOption,
    MstfwindowOption,
    XListOption,
    XMtimeOption,
    RolloverOption,
    TimeoutOption,
    TsizeOption,
    UtimeoutOption,
    WindowsizeOption,
)
from .negotiate import accept_oack, negotiate, request_options
from .policy import EXTENSION_OPTIONS, LISTING_OPTIONS, STANDARD_OPTIONS, SUPPORTED_OPTIONS, TFTPServerOptions
from .profiles import PROFILES, Profile
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
    "ServerOptionContext",
    "ClientOptionContext",
    "parse_int",
    "refuse",
    "BlksizeOption",
    "Blksize2Option",
    "TimeoutOption",
    "UtimeoutOption",
    "TsizeOption",
    "WindowsizeOption",
    "RolloverOption",
    "CookieOption",
    "MstfwindowOption",
    "XListOption",
    "XMtimeOption",
    "BUILTIN_OPTIONS",
    "OptionRegistry",
    "DEFAULT_REGISTRY",
    "register_option",
    "TFTPServerOptions",
    "STANDARD_OPTIONS",
    "EXTENSION_OPTIONS",
    "LISTING_OPTIONS",
    "SUPPORTED_OPTIONS",
    "negotiate",
    "accept_oack",
    "request_options",
    "Profile",
    "PROFILES",
]
