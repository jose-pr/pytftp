"""Opcodes and error codes (RFC 1350, RFC 2347)."""

from __future__ import annotations

import enum

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
    """ERROR packet codes (RFC 1350 section 5, plus RFC 2347's code 8)."""

    NOT_DEFINED = 0
    FILE_NOT_FOUND = 1
    ACCESS_VIOLATION = 2
    DISK_FULL = 3
    ILLEGAL_OPERATION = 4
    UNKNOWN_TID = 5
    FILE_EXISTS = 6
    NO_SUCH_USER = 7
    OPTION_REFUSED = 8
