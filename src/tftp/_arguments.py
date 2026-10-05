"""Checks a constructor makes on its arguments, before any socket is opened.

A wrong type raises ``TypeError`` and a wrong value ``ValueError``. A ``bool``
is never accepted for a number: ``port=True`` is a mistake, not port 1.
"""

from __future__ import annotations

import ipaddress
import math
import socket
from numbers import Real
from typing import Any, Optional, Tuple

__all__ = ["check_int", "check_seconds", "check_family", "check_source"]

_FAMILIES = (socket.AF_UNSPEC, socket.AF_INET, socket.AF_INET6)


def _is_number(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool)


def check_int(name: str, value: Any, low: int, high: Optional[int] = None) -> int:
    """``value`` as an ``int`` in ``low..high`` (``high`` open when ``None``)."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("%s must be an int, not %r" % (name, value))
    if value < low or (high is not None and value > high):
        raise ValueError(
            "%s must be %s, got %d"
            % (name, "%d..%d" % (low, high) if high is not None else "at least %d" % low, value)
        )
    return value


def check_seconds(name: str, value: Any, *, finite: bool = True, minimum: float = 0.0) -> float:
    """``value`` as a duration in seconds, at least ``minimum``; ``0`` is refused when ``minimum`` is ``0``.

    ``finite=False`` lets ``inf`` through (a limit that never comes).
    """
    if not _is_number(value):
        raise TypeError("%s must be a number of seconds, not %r" % (name, value))
    if math.isnan(value) or (finite and math.isinf(value)):
        raise ValueError("%s must be a finite number of seconds, got %r" % (name, value))
    if value < minimum or (minimum == 0.0 and value == 0):
        raise ValueError(
            "%s must be %s, got %r" % (name, "positive" if minimum == 0.0 else "at least %g" % minimum, value)
        )
    return value


def check_family(value: Any) -> int:
    """``value`` as ``AF_UNSPEC`` (0), ``AF_INET`` or ``AF_INET6``."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("family must be an int, not %r" % (value,))
    if value not in _FAMILIES:
        raise ValueError("family must be 0, AF_INET or AF_INET6, got %d" % value)
    return value


def _is_host_like(value: Any) -> bool:
    if isinstance(value, (str, ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return True
    if isinstance(value, (ipaddress.IPv4Interface, ipaddress.IPv6Interface)):
        return True
    return type(value).__module__.partition(".")[0] == "netimps"  # Host, FQDN


def check_source(value: Any) -> Optional[Tuple[Any, int]]:
    """``value`` as ``None`` or an ``(address, port)`` pair to send from; port ``0`` lets the system choose."""
    if value is None:
        return None
    if not isinstance(value, (tuple, list)):
        raise TypeError("src must be an (address, port) pair, not %r" % (value,))
    if len(value) != 2:
        raise ValueError("src must be an (address, port) pair, got %d items" % len(value))
    address, port = value
    if not _is_host_like(address):
        raise TypeError("src address must be text, an address or a netimps.Host, not %r" % (address,))
    return address, check_int("src port", port, 0, 65535)
