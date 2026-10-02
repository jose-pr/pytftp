"""Negotiation building blocks: limits, the result type, and the option interface."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from ..errors import ProtocolError
from ..packet import ErrorCode

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
    "refuse",
    "parse_int",
]

DEFAULT_BLKSIZE = 512
MIN_BLKSIZE = 8
#: RFC 2348's ceiling: the largest payload a 65535-byte IPv4 datagram carries.
MAX_BLKSIZE = 65464
MAX_WINDOWSIZE = 65535
MIN_UTIMEOUT = 10_000
MAX_UTIMEOUT = 255_000_000


class Negotiated:
    """The parameters a transfer actually runs with, after negotiation.

    ``options`` is what was acknowledged (the OACK contents), empty when the
    transfer runs on RFC 1350 defaults. ``extra`` holds what custom option
    handlers decided, keyed by option name.
    """

    __slots__ = ("blksize", "windowsize", "timeout", "tsize", "rollover", "options", "extra")

    def __init__(
        self,
        blksize: int = DEFAULT_BLKSIZE,
        windowsize: int = 1,
        timeout: float = 1.0,
        tsize: Optional[int] = None,
        rollover: int = 0,
        options: Optional[Dict[str, str]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.blksize = blksize
        self.windowsize = windowsize
        self.timeout = timeout
        self.tsize = tsize
        self.rollover = rollover
        self.options = options if options is not None else {}
        self.extra = extra if extra is not None else {}

    def __repr__(self) -> str:
        return "Negotiated(blksize=%d, windowsize=%d, timeout=%g, tsize=%r, rollover=%d)" % (
            self.blksize,
            self.windowsize,
            self.timeout,
            self.tsize,
            self.rollover,
        )


def parse_int(text: str) -> Optional[int]:
    """A decimal option value, or ``None`` when it is not one."""
    try:
        return int(text.strip())
    except (ValueError, AttributeError):
        return None


def refuse(message: str) -> ProtocolError:
    """The ERROR 8 a client sends for an OACK it cannot accept."""
    return ProtocolError(message, ErrorCode.OPTION_REFUSED)


class ServerContext:
    """What a server-side option handler sees while negotiating one request.

    :ivar result: the :class:`Negotiated` being built; handlers set fields on it.
    :ivar requested: every requested option (lower-case names).
    :ivar acked: what has been acknowledged so far, in registry order.
    :ivar policy: the server's :class:`ServerOptions`.
    :ivar is_read: RRQ (``True``) or WRQ.
    :ivar size: the file size for an RRQ (``tsize``), or ``None``.
    :ivar mtu: link MTU of the interface the request arrived on, when known.
    :ivar ipv6: the transfer runs over IPv6 (header size for MTU arithmetic).
    """

    __slots__ = ("result", "requested", "acked", "policy", "is_read", "size", "mtu", "ipv6")

    def __init__(
        self,
        result: Negotiated,
        requested: Mapping[str, str],
        policy: Any,
        is_read: bool,
        size: Optional[int] = None,
        mtu: Optional[int] = None,
        ipv6: bool = False,
    ) -> None:
        self.result = result
        self.requested = requested
        self.acked = result.options
        self.policy = policy
        self.is_read = is_read
        self.size = size
        self.mtu = mtu
        self.ipv6 = ipv6

    @property
    def max_blksize(self) -> int:
        """The policy's ``max_blksize``, lowered to fit the MTU when asked to."""
        limit = self.policy.max_blksize
        if self.policy.fit_mtu and self.mtu:
            # IP header, UDP header (8), TFTP DATA header (4).
            fits = self.mtu - (40 if self.ipv6 else 20) - 8 - 4
            if fits >= MIN_BLKSIZE:
                limit = min(limit, fits)
        return limit


class ClientContext:
    """What a client-side option handler sees while checking an OACK."""

    __slots__ = ("result", "requested", "is_read")

    def __init__(self, result: Negotiated, requested: Mapping[str, str], is_read: bool) -> None:
        self.result = result
        self.requested = requested
        self.is_read = is_read


class OptionHandler:
    """One option's rules, for both ends of the negotiation.

    Subclass and register an instance (see :class:`OptionRegistry`) to add
    an option. ``name`` is lower-case; ``standard`` marks the RFC-defined
    options a server accepts by default.
    """

    name: str = ""
    standard: bool = False

    def negotiate(self, value: str, ctx: ServerContext) -> Optional[str]:
        """Server side: the value to acknowledge, or ``None`` to leave it out.

        Leaving an option out is how RFC 2347 refuses it; the transfer then
        runs as if it had not been requested. Set fields on ``ctx.result``.
        """
        return None

    def accept(self, requested: str, acked: str, ctx: ClientContext) -> None:
        """Client side: check the server's answer and apply it.

        Raise :func:`refuse` for a value the RFC does not allow; the client
        then sends ERROR 8 and abandons the transfer.
        """
        ctx.result.extra[self.name] = acked

    def __repr__(self) -> str:
        return "<%s %r>" % (type(self).__name__, self.name)
