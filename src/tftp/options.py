"""Option negotiation: RFC 2347 framing with RFC 2348/2349/7440 options.

Supported options:

=============  ==========  ====================================================
option         RFC         meaning
=============  ==========  ====================================================
``blksize``    2348        DATA payload size, 8..65464 (default 512)
``timeout``    2349        retransmission timeout, whole seconds 1..255
``tsize``      2349        transfer size: asked with 0 on RRQ, announced on WRQ
``windowsize`` 7440        DATA packets per ACK, 1..65535 (default 1)
``utimeout``   (tftp-hpa)  retransmission timeout in microseconds
``rollover``   (common)    block number after 65535: 0 (default) or 1
=============  ==========  ====================================================
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Mapping, Optional

from .errors import ProtocolError
from .packet import ErrorCode

__all__ = [
    "DEFAULT_BLKSIZE",
    "MIN_BLKSIZE",
    "MAX_BLKSIZE",
    "MAX_WINDOWSIZE",
    "SUPPORTED_OPTIONS",
    "Negotiated",
    "ServerOptions",
    "negotiate",
    "accept_oack",
    "request_options",
]

DEFAULT_BLKSIZE = 512
MIN_BLKSIZE = 8
#: RFC 2348's ceiling: the largest payload a 65535-byte IPv4 datagram carries.
MAX_BLKSIZE = 65464
MAX_WINDOWSIZE = 65535
MIN_UTIMEOUT = 10_000
MAX_UTIMEOUT = 255_000_000

SUPPORTED_OPTIONS: FrozenSet[str] = frozenset(
    {"blksize", "timeout", "tsize", "windowsize", "utimeout", "rollover"}
)


class Negotiated:
    """The parameters a transfer actually runs with, after negotiation.

    ``options`` is what was acknowledged (the OACK contents), empty when the
    transfer runs on RFC 1350 defaults.
    """

    __slots__ = ("blksize", "windowsize", "timeout", "tsize", "rollover", "options")

    def __init__(
        self,
        blksize: int = DEFAULT_BLKSIZE,
        windowsize: int = 1,
        timeout: float = 1.0,
        tsize: Optional[int] = None,
        rollover: int = 0,
        options: Optional[Dict[str, str]] = None,
    ) -> None:
        self.blksize = blksize
        self.windowsize = windowsize
        self.timeout = timeout
        self.tsize = tsize
        self.rollover = rollover
        self.options = options if options is not None else {}

    def __repr__(self) -> str:
        return "Negotiated(blksize=%d, windowsize=%d, timeout=%g, tsize=%r, rollover=%d)" % (
            self.blksize,
            self.windowsize,
            self.timeout,
            self.tsize,
            self.rollover,
        )


class ServerOptions:
    """What a server is willing to negotiate.

    :param max_blksize: largest ``blksize`` granted; a larger request is
        answered with this value, which RFC 2348 allows.
    :param max_windowsize: largest ``windowsize`` granted. A sender keeps a
        whole window in memory, so this bounds per-transfer memory at about
        ``max_windowsize * max_blksize``.
    :param allowed: option names the server will acknowledge at all.
    """

    __slots__ = ("max_blksize", "max_windowsize", "allowed")

    def __init__(
        self,
        max_blksize: int = MAX_BLKSIZE,
        max_windowsize: int = 64,
        allowed: FrozenSet[str] = SUPPORTED_OPTIONS,
    ) -> None:
        if not MIN_BLKSIZE <= max_blksize <= MAX_BLKSIZE:
            raise ValueError("max_blksize must be in %d..%d" % (MIN_BLKSIZE, MAX_BLKSIZE))
        if not 1 <= max_windowsize <= MAX_WINDOWSIZE:
            raise ValueError("max_windowsize must be in 1..%d" % MAX_WINDOWSIZE)
        unknown = set(allowed) - SUPPORTED_OPTIONS
        if unknown:
            raise ValueError("unsupported options: %s" % ", ".join(sorted(unknown)))
        self.max_blksize = max_blksize
        self.max_windowsize = max_windowsize
        self.allowed = frozenset(allowed)


def _int(text: str) -> Optional[int]:
    try:
        value = int(text.strip())
    except (ValueError, AttributeError):
        return None
    return value


def negotiate(
    requested: Mapping[str, str],
    policy: ServerOptions,
    *,
    is_read: bool,
    timeout: float,
    size: Optional[int] = None,
) -> Negotiated:
    """Server side: decide which requested options to acknowledge.

    Options the server does not understand, does not allow, or that carry an
    unusable value are left out of the acknowledgement, which RFC 2347 defines
    as refusing them -- the transfer then runs on the default for that option.
    ``size`` is the file size for an RRQ ``tsize``; when unknown or zero,
    ``tsize`` is not acknowledged.
    """
    result = Negotiated(timeout=timeout)
    acked = result.options
    allowed = policy.allowed

    if "blksize" in requested and "blksize" in allowed:
        value = _int(requested["blksize"])
        if value is not None and value >= MIN_BLKSIZE:
            result.blksize = min(value, policy.max_blksize)
            acked["blksize"] = str(result.blksize)

    if "timeout" in requested and "timeout" in allowed:
        value = _int(requested["timeout"])
        if value is not None and 1 <= value <= 255:
            result.timeout = float(value)
            acked["timeout"] = requested["timeout"].strip()

    if "utimeout" in requested and "utimeout" in allowed:
        value = _int(requested["utimeout"])
        if value is not None and MIN_UTIMEOUT <= value <= MAX_UTIMEOUT:
            result.timeout = value / 1e6
            acked["utimeout"] = str(value)

    if "tsize" in requested and "tsize" in allowed:
        value = _int(requested["tsize"])
        if value is not None and value >= 0:
            if not is_read:
                result.tsize = value
                acked["tsize"] = str(value)
            elif size:
                # Not for an empty file: curl rejects "tsize 0" in an OACK
                # (RFC 2349 allows it), and the transfer shows the size anyway.
                result.tsize = size
                acked["tsize"] = str(size)

    if "windowsize" in requested and "windowsize" in allowed:
        value = _int(requested["windowsize"])
        if value is not None and value >= 1:
            result.windowsize = min(value, policy.max_windowsize, MAX_WINDOWSIZE)
            acked["windowsize"] = str(result.windowsize)

    if "rollover" in requested and "rollover" in allowed:
        value = _int(requested["rollover"])
        if value in (0, 1):
            result.rollover = value  # type: ignore[assignment]
            acked["rollover"] = str(value)

    return result


def request_options(
    *,
    blksize: Optional[int] = None,
    windowsize: Optional[int] = None,
    timeout: Optional[float] = None,
    tsize: Optional[int] = None,
    rollover: Optional[int] = None,
) -> Dict[str, str]:
    """Client side: the options to put in a request.

    ``timeout`` is sent as ``timeout`` when it is a whole number of seconds in
    1..255 and as ``utimeout`` otherwise. ``tsize`` is ``0`` for a read and
    the upload size for a write; ``None`` leaves it out.
    """
    options: Dict[str, str] = {}
    if blksize is not None:
        if not MIN_BLKSIZE <= blksize <= MAX_BLKSIZE:
            raise ValueError("blksize must be in %d..%d" % (MIN_BLKSIZE, MAX_BLKSIZE))
        options["blksize"] = str(blksize)
    if tsize is not None:
        if tsize < 0:
            raise ValueError("tsize cannot be negative")
        options["tsize"] = str(tsize)
    if timeout is not None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if float(timeout).is_integer() and 1 <= timeout <= 255:
            options["timeout"] = str(int(timeout))
        else:
            micro = int(round(timeout * 1e6))
            options["utimeout"] = str(min(max(micro, MIN_UTIMEOUT), MAX_UTIMEOUT))
    if windowsize is not None:
        if not 1 <= windowsize <= MAX_WINDOWSIZE:
            raise ValueError("windowsize must be in 1..%d" % MAX_WINDOWSIZE)
        options["windowsize"] = str(windowsize)
    if rollover is not None:
        if rollover not in (0, 1):
            raise ValueError("rollover must be 0 or 1")
        options["rollover"] = str(rollover)
    return options


def _refuse(message: str) -> ProtocolError:
    return ProtocolError(message, ErrorCode.OPTION_REFUSED)


def accept_oack(
    requested: Mapping[str, str],
    oack: Mapping[str, str],
    *,
    is_read: bool,
    timeout: float,
) -> Negotiated:
    """Client side: validate a server's OACK against what was requested.

    Raises :class:`ProtocolError` with code 8 when the server acknowledged an
    option that was not requested or answered outside what RFC 2348/2349/7440
    allow (a larger ``blksize`` or ``windowsize`` than asked for, ...). The
    caller sends that ERROR to the server and abandons the transfer.
    """
    result = Negotiated(timeout=timeout, options=dict(oack))
    for name, text in oack.items():
        if name not in requested:
            raise _refuse("server acknowledged unrequested option %r" % name)
        value = _int(text)
        if value is None:
            raise _refuse("server sent non-numeric %s=%r" % (name, text))
        if name == "blksize":
            if not MIN_BLKSIZE <= value <= int(requested[name]):
                raise _refuse("server blksize %d outside %d..%s" % (value, MIN_BLKSIZE, requested[name]))
            result.blksize = value
        elif name == "windowsize":
            if not 1 <= value <= int(requested[name]):
                raise _refuse("server windowsize %d outside 1..%s" % (value, requested[name]))
            result.windowsize = value
        elif name == "timeout":
            if not 1 <= value <= 255:
                raise _refuse("server timeout %d outside 1..255" % value)
            result.timeout = float(value)
        elif name == "utimeout":
            if value <= 0:
                raise _refuse("server utimeout %d is not positive" % value)
            result.timeout = value / 1e6
        elif name == "tsize":
            if value < 0:
                raise _refuse("server tsize %d is negative" % value)
            result.tsize = value
        elif name == "rollover":
            if value not in (0, 1):
                raise _refuse("server rollover %d is not 0 or 1" % value)
            result.rollover = value
    return result
