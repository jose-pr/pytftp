"""Both sides of RFC 2347 negotiation, driven by the option registry."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from .base import (
    MAX_BLKSIZE,
    MAX_UTIMEOUT,
    MAX_WINDOWSIZE,
    MIN_BLKSIZE,
    MIN_UTIMEOUT,
    ClientContext,
    Negotiated,
    ServerContext,
    refuse,
)
from .policy import ServerOptions
from .registry import DEFAULT_REGISTRY, OptionRegistry

__all__ = ["negotiate", "accept_oack", "request_options"]


def negotiate(
    requested: Mapping[str, str],
    policy: ServerOptions,
    *,
    is_read: bool,
    timeout: float,
    size: Optional[int] = None,
    mtu: Optional[int] = None,
    ipv6: bool = False,
    stream: Any = None,
) -> Negotiated:
    """Server side: decide which requested options to acknowledge.

    Options the server does not know, does not allow, or that carry an
    unusable value are left out of the acknowledgement -- RFC 2347's way of
    refusing them; the transfer then runs on that option's default. A server
    never acknowledges an option that was not requested. ``size`` is the file
    size for an RRQ ``tsize``; ``mtu``/``ipv6`` feed ``fit_mtu``; ``stream``
    is the RRQ's opened source, for options that describe it.
    """
    result = Negotiated(timeout=timeout)
    ctx = ServerContext(result, requested, policy, is_read, size, mtu, ipv6, stream)
    for handler in policy.registry:
        name = handler.name
        if name in requested and policy.accepts(name):
            answer = handler.negotiate(requested[name], ctx)
            if answer is not None:
                result.options[name] = answer
    return result


def accept_oack(
    requested: Mapping[str, str],
    oack: Mapping[str, str],
    *,
    is_read: bool,
    timeout: float,
    registry: Optional[OptionRegistry] = None,
) -> Negotiated:
    """Client side: validate a server's OACK against what was requested.

    Raises :class:`TFTPProtocolError` with code 8 when the server acknowledged an
    option that was not requested, or answered outside what the option's RFC
    allows (a larger ``blksize`` or ``windowsize`` than asked for, ...). The
    caller sends that ERROR to the server and abandons the transfer. An
    option the server left out runs on its default.
    """
    registry = registry or DEFAULT_REGISTRY
    result = Negotiated(timeout=timeout, options=dict(oack))
    ctx = ClientContext(result, requested, is_read)
    for name, value in oack.items():
        if name not in requested:
            raise refuse("server acknowledged unrequested option %r" % name)
        handler = registry.get(name)
        if handler is None:
            result.extra[name] = value  # a custom option the caller asked for
        else:
            handler.accept(requested[name], value, ctx)
    return result


def request_options(
    *,
    blksize: Optional[int] = None,
    windowsize: Optional[int] = None,
    timeout: Optional[float] = None,
    tsize: Optional[int] = None,
    rollover: Optional[int] = None,
    utimeout: bool = False,
    extra: Optional[Mapping[str, object]] = None,
) -> Dict[str, str]:
    """Client side: the options to put in a request.

    ``timeout`` is sent as ``timeout`` when it is a whole number of seconds in
    1..255; a fractional one is sent as ``utimeout`` only with
    ``utimeout=True`` (an extension) and otherwise not at all. ``tsize`` is
    ``0`` for a read and the upload size for a write; ``None`` leaves it out.
    ``extra`` adds options verbatim (custom or extension options), after the
    built-in ones.
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
        elif utimeout:
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
    for name, value in (extra or {}).items():
        key = str(name).lower()
        if key in options:
            raise ValueError("option %r given twice" % key)
        options[key] = str(value)
    return options
