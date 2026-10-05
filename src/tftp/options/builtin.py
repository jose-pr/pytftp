"""The options this library knows: RFC 2348, 2349, 7440, tftp-hpa's and Microsoft's extensions.

=============  ===========  ====================================================
option         defined by   meaning
=============  ===========  ====================================================
``blksize``    RFC 2348     DATA payload size, 8..65464 (default 512)
``timeout``    RFC 2349     retransmission timeout, whole seconds 1..255
``tsize``      RFC 2349     transfer size: asked with 0 on RRQ, announced on WRQ
``windowsize`` RFC 7440     DATA packets per ACK, 1..65535 (default 1)
``blksize2``   tftp-hpa     like blksize, answered with a power of two
``utimeout``   tftp-hpa     retransmission timeout in microseconds
``rollover``   tftp-hpa     block number after 65535: 0 (default) or 1
``cookie``     tftp-hpa     opaque value, echoed back unchanged
``mstfwindow`` Microsoft    variable window (bootmgr/WDS): offer 31416, answer
                            27182, window 4
``x-list``     pytftp       RRQ of a directory answered with a listing
``x-mtime``    pytftp       RRQ: the file's modification time, in the OACK
=============  ===========  ====================================================
"""

from __future__ import annotations

import os
from typing import Any, Optional

from .base import (
    MAX_UTIMEOUT,
    MAX_WINDOWSIZE,
    MIN_BLKSIZE,
    MIN_UTIMEOUT,
    ClientOptionContext,
    OptionHandler,
    ServerOptionContext,
    parse_int,
    refuse,
)

__all__ = [
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
    "stream_mtime",
    "BUILTIN_OPTIONS",
]


def _number(name: str, text: str) -> int:
    value = parse_int(text)
    if value is None:
        raise refuse("server sent non-numeric %s=%r" % (name, text))
    return value


class BlksizeOption(OptionHandler):
    name = "blksize"
    standard = True

    def negotiate(self, value: str, ctx: ServerOptionContext) -> Optional[str]:
        size = parse_int(value)
        if size is None or size < MIN_BLKSIZE:
            return None
        # RFC 2348: the server may answer with a smaller value.
        ctx.result.blksize = min(size, ctx.max_blksize)
        return str(ctx.result.blksize)

    def accept(self, requested: str, acked: str, ctx: ClientOptionContext) -> None:
        size = _number(self.name, acked)
        if not MIN_BLKSIZE <= size <= int(requested):
            raise refuse("server blksize %d outside %d..%s" % (size, MIN_BLKSIZE, requested))
        ctx.result.blksize = size


class Blksize2Option(OptionHandler):
    """``blksize`` restricted to powers of two, for firmware that needs them."""

    name = "blksize2"

    def negotiate(self, value: str, ctx: ServerOptionContext) -> Optional[str]:
        if "blksize" in ctx.acked:
            return None  # one block size per transfer; blksize wins
        size = parse_int(value)
        if size is None or size < MIN_BLKSIZE:
            return None
        limit = min(size, ctx.max_blksize)
        power = 1 << (limit.bit_length() - 1)
        if power < MIN_BLKSIZE:
            return None
        ctx.result.blksize = power
        return str(power)

    def accept(self, requested: str, acked: str, ctx: ClientOptionContext) -> None:
        size = _number(self.name, acked)
        if size < MIN_BLKSIZE or size > int(requested) or size & (size - 1):
            raise refuse("server blksize2 %d is not a power of two within %s" % (size, requested))
        ctx.result.blksize = size


class TimeoutOption(OptionHandler):
    name = "timeout"
    standard = True

    def negotiate(self, value: str, ctx: ServerOptionContext) -> Optional[str]:
        seconds = parse_int(value)
        if seconds is None or not 1 <= seconds <= 255:
            return None
        ctx.result.timeout = float(seconds)
        return value.strip()  # RFC 2349: echo exactly what was asked

    def accept(self, requested: str, acked: str, ctx: ClientOptionContext) -> None:
        seconds = _number(self.name, acked)
        if not 1 <= seconds <= 255:
            raise refuse("server timeout %d outside 1..255" % seconds)
        ctx.result.timeout = float(seconds)


class UtimeoutOption(OptionHandler):
    name = "utimeout"

    def negotiate(self, value: str, ctx: ServerOptionContext) -> Optional[str]:
        micro = parse_int(value)
        if micro is None or not MIN_UTIMEOUT <= micro <= MAX_UTIMEOUT:
            return None
        ctx.result.timeout = micro / 1e6
        return str(micro)

    def accept(self, requested: str, acked: str, ctx: ClientOptionContext) -> None:
        micro = _number(self.name, acked)
        if micro <= 0:
            raise refuse("server utimeout %d is not positive" % micro)
        ctx.result.timeout = micro / 1e6


class TsizeOption(OptionHandler):
    name = "tsize"
    standard = True

    def negotiate(self, value: str, ctx: ServerOptionContext) -> Optional[str]:
        size = parse_int(value)
        if size is None or size < 0:
            return None
        if not ctx.is_read:
            ctx.result.tsize = size
            return str(size)
        # Never a guess: an unknown size is left out. Nor 0 for an empty
        # file: curl rejects "tsize 0" in an OACK, and the transfer shows it.
        if ctx.size:
            ctx.result.tsize = ctx.size
            return str(ctx.size)
        return None

    def accept(self, requested: str, acked: str, ctx: ClientOptionContext) -> None:
        size = _number(self.name, acked)
        if size < 0:
            raise refuse("server tsize %d is negative" % size)
        ctx.result.tsize = size


class WindowsizeOption(OptionHandler):
    name = "windowsize"
    standard = True

    def negotiate(self, value: str, ctx: ServerOptionContext) -> Optional[str]:
        window = parse_int(value)
        if window is None or window < 1:
            return None
        policy = ctx.policy
        # A sender keeps the whole window in memory: bound it in bytes too.
        by_bytes = max(1, policy.max_window_bytes // ctx.result.blksize)
        ctx.result.windowsize = min(window, policy.max_windowsize, by_bytes, MAX_WINDOWSIZE)
        return str(ctx.result.windowsize)

    def accept(self, requested: str, acked: str, ctx: ClientOptionContext) -> None:
        window = _number(self.name, acked)
        if not 1 <= window <= int(requested):
            raise refuse("server windowsize %d outside 1..%s" % (window, requested))
        ctx.result.windowsize = window


class RolloverOption(OptionHandler):
    name = "rollover"

    def negotiate(self, value: str, ctx: ServerOptionContext) -> Optional[str]:
        target = parse_int(value)
        if target not in (0, 1):
            return None
        ctx.result.rollover = target  # type: ignore[assignment]
        return str(target)

    def accept(self, requested: str, acked: str, ctx: ClientOptionContext) -> None:
        target = _number(self.name, acked)
        if target not in (0, 1):
            raise refuse("server rollover %d is not 0 or 1" % target)
        ctx.result.rollover = target


class CookieOption(OptionHandler):
    """Opaque; echoed back unchanged. Its length is bounded by TFTPServerLimits."""

    name = "cookie"

    def negotiate(self, value: str, ctx: ServerOptionContext) -> Optional[str]:
        ctx.result.extra[self.name] = value
        return value

    def accept(self, requested: str, acked: str, ctx: ClientOptionContext) -> None:
        if acked != requested:
            raise refuse("server changed the cookie")
        ctx.result.extra[self.name] = acked


class MstfwindowOption(OptionHandler):
    """Microsoft's variable-window extension (Windows 8+ ``bootmgr``, WDS).

    The client offers ``mstfwindow=31416``; a server that speaks it answers
    ``27182`` and both start with a window of 4 blocks. The client may then
    resize the window through its ACKs, in a format Microsoft has not
    published; this library keeps the window at 4 (bytes after an ACK's
    block number are ignored). A ``windowsize`` negotiated alongside wins.
    """

    name = "mstfwindow"
    OFFER = "31416"
    ANSWER = "27182"
    WINDOW = 4

    def negotiate(self, value: str, ctx: ServerOptionContext) -> Optional[str]:
        if value.strip() != self.OFFER:
            return None
        if "windowsize" not in ctx.acked:
            ctx.result.windowsize = min(self.WINDOW, ctx.policy.max_windowsize)
        ctx.result.extra[self.name] = True
        return self.ANSWER

    def accept(self, requested: str, acked: str, ctx: ClientOptionContext) -> None:
        if acked.strip() != self.ANSWER:
            raise refuse("server answered mstfwindow=%r, not %s" % (acked, self.ANSWER))
        if "windowsize" not in ctx.result.options:
            ctx.result.windowsize = self.WINDOW
        ctx.result.extra[self.name] = True


class XListOption(OptionHandler):
    """pytftp's directory listing (see :mod:`tftp.listing`): ``x-list=1``.

    Acknowledged only when the handler answered the RRQ with a listing
    (a stream marked ``_tftp_listing_``); a client seeing it left out of the
    OACK knows it is receiving a file.
    """

    name = "x-list"
    VERSION = "1"

    def negotiate(self, value: str, ctx: ServerOptionContext) -> Optional[str]:
        if not ctx.is_read or value.strip() != self.VERSION:
            return None
        if not getattr(ctx.stream, "_tftp_listing_", False):
            return None
        ctx.result.extra[self.name] = True
        return self.VERSION

    def accept(self, requested: str, acked: str, ctx: ClientOptionContext) -> None:
        if acked.strip() != self.VERSION:
            raise refuse("server answered x-list=%r" % acked)
        ctx.result.extra[self.name] = True


def stream_mtime(stream: Any) -> Optional[int]:
    """Modification time of an opened source: its ``mtime`` attribute, or ``fstat``."""
    mtime = getattr(stream, "mtime", None)
    if isinstance(mtime, (int, float)) and not isinstance(mtime, bool):
        return int(mtime)
    try:
        return int(os.fstat(stream.fileno()).st_mtime)
    except (AttributeError, OSError, ValueError):
        return None


class XMtimeOption(OptionHandler):
    """pytftp's ``x-mtime``: an RRQ's OACK carries the file's modification
    time (whole seconds since the epoch), when the source knows it."""

    name = "x-mtime"

    def negotiate(self, value: str, ctx: ServerOptionContext) -> Optional[str]:
        if not ctx.is_read:
            return None
        mtime = stream_mtime(ctx.stream)
        if mtime is None:
            return None
        ctx.result.extra[self.name] = mtime
        return str(mtime)

    def accept(self, requested: str, acked: str, ctx: ClientOptionContext) -> None:
        ctx.result.extra[self.name] = _number(self.name, acked)


#: In negotiation order: block size first, since the window bound depends on it.
BUILTIN_OPTIONS = (
    BlksizeOption(),
    Blksize2Option(),
    TimeoutOption(),
    UtimeoutOption(),
    TsizeOption(),
    WindowsizeOption(),
    RolloverOption(),
    CookieOption(),
    MstfwindowOption(),
    XListOption(),
    XMtimeOption(),
)
