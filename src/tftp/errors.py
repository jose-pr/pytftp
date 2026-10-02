"""Exceptions, and the mapping from OS errors to TFTP error codes."""

from __future__ import annotations

import errno

from .packet import ErrorCode

__all__ = [
    "TftpError",
    "RemoteError",
    "ProtocolError",
    "TransferTimeout",
    "error_for_exception",
]


class TftpError(Exception):
    """A TFTP failure carrying the ERROR code that describes it.

    A server handler raises this to refuse a request: the code and message
    become the ERROR packet the client receives.
    """

    def __init__(self, code: int = ErrorCode.NOT_DEFINED, message: str = "") -> None:
        try:
            code = ErrorCode(code)
        except ValueError:
            pass
        self.code = code
        self.message = message or _default_message(code)
        super().__init__(self.code, self.message)

    def __str__(self) -> str:
        name = self.code.name if isinstance(self.code, ErrorCode) else str(self.code)
        return "%s: %s" % (name, self.message)


class RemoteError(TftpError):
    """The peer sent an ERROR packet."""


class ProtocolError(TftpError):
    """The peer broke the protocol (malformed packet, bad option ack...)."""

    def __init__(self, message: str, code: int = ErrorCode.ILLEGAL_OPERATION) -> None:
        super().__init__(code, message)


class TransferTimeout(TftpError, TimeoutError):
    """The peer stopped answering and every retry was used up."""

    def __init__(self, message: str = "timed out") -> None:
        super().__init__(ErrorCode.NOT_DEFINED, message)


_MESSAGES = {
    ErrorCode.NOT_DEFINED: "error",
    ErrorCode.FILE_NOT_FOUND: "file not found",
    ErrorCode.ACCESS_VIOLATION: "access violation",
    ErrorCode.DISK_FULL: "disk full or allocation exceeded",
    ErrorCode.ILLEGAL_OPERATION: "illegal TFTP operation",
    ErrorCode.UNKNOWN_TID: "unknown transfer ID",
    ErrorCode.FILE_EXISTS: "file already exists",
    ErrorCode.NO_SUCH_USER: "no such user",
    ErrorCode.OPTION_REFUSED: "option negotiation refused",
}


def _default_message(code: int) -> str:
    return _MESSAGES.get(code, "error")  # type: ignore[call-overload]


_ERRNO_CODES = {
    errno.ENOENT: ErrorCode.FILE_NOT_FOUND,
    errno.ENOTDIR: ErrorCode.FILE_NOT_FOUND,
    errno.EISDIR: ErrorCode.ACCESS_VIOLATION,
    errno.EACCES: ErrorCode.ACCESS_VIOLATION,
    errno.EPERM: ErrorCode.ACCESS_VIOLATION,
    errno.EROFS: ErrorCode.ACCESS_VIOLATION,
    errno.EEXIST: ErrorCode.FILE_EXISTS,
    errno.ENOSPC: ErrorCode.DISK_FULL,
    errno.EFBIG: ErrorCode.DISK_FULL,
}
if hasattr(errno, "EDQUOT"):
    _ERRNO_CODES[errno.EDQUOT] = ErrorCode.DISK_FULL


def error_for_exception(exc: BaseException) -> TftpError:
    """The ERROR to send for ``exc``.

    A :class:`TftpError` is used as is. An ``OSError`` maps by errno
    (``ENOENT`` -> file not found, ``EACCES`` -> access violation, ``ENOSPC``
    -> disk full, ...). Its message is the generic one for the code, never the
    OS text, which would disclose server paths.
    """
    if isinstance(exc, TftpError):
        return exc
    if isinstance(exc, OSError):
        code = _ERRNO_CODES.get(exc.errno or 0)
        if code is None:
            if isinstance(exc, FileNotFoundError):
                code = ErrorCode.FILE_NOT_FOUND
            elif isinstance(exc, PermissionError):
                code = ErrorCode.ACCESS_VIOLATION
            elif isinstance(exc, FileExistsError):
                code = ErrorCode.FILE_EXISTS
            else:
                code = ErrorCode.NOT_DEFINED
        return TftpError(code)
    return TftpError(ErrorCode.NOT_DEFINED)
