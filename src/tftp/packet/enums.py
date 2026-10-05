"""Opcodes and error codes (RFC 1350, RFC 2347)."""

from __future__ import annotations

import enum
from typing import Optional

__all__ = ["TFTPOpcode", "TFTPErrorCode"]


class TFTPOpcode(enum.IntEnum):
    """Packet type, the first two bytes of every packet."""

    RRQ = 1
    WRQ = 2
    DATA = 3
    ACK = 4
    ERROR = 5
    OACK = 6


class TFTPErrorCode(enum.IntEnum):
    """ERROR packet codes (RFC 1350 section 5, plus RFC 2347's code 8).

    ``TFTPErrorCode(n)`` for any ``n`` in 0..65535 the RFCs do not define is
    an unnamed member (``name`` is ``CODE_<n>``) that carries the number, so a
    peer's code is forwarded as it came; ``ValueError`` outside that range.
    """

    NOT_DEFINED = 0
    FILE_NOT_FOUND = 1
    ACCESS_VIOLATION = 2
    DISK_FULL = 3
    ILLEGAL_OPERATION = 4
    UNKNOWN_TID = 5
    FILE_EXISTS = 6
    NO_SUCH_USER = 7
    OPTION_REFUSED = 8

    @classmethod
    def _missing_(cls, value: object) -> "Optional[TFTPErrorCode]":
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 65535:
            member = int.__new__(cls, value)
            member._name_ = "CODE_%d" % value
            member._value_ = value
            return member
        return None
