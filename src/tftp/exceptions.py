"""Every exception the package raises on its own account, and the mapping
from OS errors to TFTP error codes.

:class:`TFTPError` is the one base. It plays three roles, told apart by the
subclass:

- what a server handler raises to refuse a request: the code and message
  become the ERROR packet the client receives (:class:`TFTPError` itself, or
  :class:`TFTPProtocolError`);
- what a client raises for an ERROR the server sent: :class:`RemoteError` and
  its leaves, one per defined error code;
- what a transfer that failed reports in ``TransferResult.error``, whichever
  of these it was, including :class:`TransferTimeoutError` and
  :class:`TransferAbortedError` for a local deadline or an ``abort()``.

Text that is not the value it was asked to become raises
:class:`TFTPValueError`, which is also a :class:`ValueError`:
:class:`TFTPDecodeError` for bytes that are not a packet,
:class:`CaptureFormatError` for a file that is not a capture and
:class:`CaptureFilterError` for a filter expression that does not parse. A
caller's own mistake, a wrong argument type or an option out of range, is
plain :class:`TypeError` or :class:`ValueError`, not one of these.

:class:`WouldBlock` is a signal that a source or sink has nothing ready, not a
failure, so it is a :class:`BlockingIOError` and not a :class:`TFTPError`.

Every class is rebuilt by ``type(*args)``, so each one copies and pickles.
"""

from __future__ import annotations

import errno
from typing import Tuple

from .packet.enums import TFTPErrorCode

__all__ = [
    "TFTPError",
    "TFTPProtocolError",
    "RemoteError",
    "FileNotFound",
    "AccessViolation",
    "DiskFull",
    "IllegalOperation",
    "UnknownTransferID",
    "FileAlreadyExists",
    "NoSuchUser",
    "OptionNegotiationError",
    "TransferTimeoutError",
    "TransferAbortedError",
    "TFTPValueError",
    "TFTPDecodeError",
    "CaptureFormatError",
    "CaptureFilterError",
    "WouldBlock",
]


class TFTPError(Exception):
    """A TFTP failure carrying the ERROR code that describes it.

    A server handler raises this to refuse a request: the code and message
    become the ERROR packet the client receives.

    :param code: an ``int`` in 0..65535 (unknown codes are kept: the wire
        carries any 16-bit value). ``TypeError`` for anything else, which
        catches ``TFTPError("message")``; ``ValueError`` outside the range.
    :param message: text; ``TypeError`` for anything else. A NUL is replaced
        and a long text cut when the ERROR is encoded.
    """

    def __init__(self, code: int = TFTPErrorCode.NOT_DEFINED, message: str = "") -> None:
        if isinstance(code, bool) or not isinstance(code, int):
            raise TypeError(
                "an error code is an int, not %s (the message is the second argument)" % type(code).__name__
            )
        if not 0 <= code <= 65535:
            raise ValueError("an error code is 0 to 65535, not %d" % code)
        if not isinstance(message, str):
            raise TypeError("an error message is text, not %s" % type(message).__name__)
        self.code = TFTPErrorCode(code)
        self.message = message or _default_message(code)
        super().__init__(*self._constructor_args())

    @classmethod
    def from_exception(cls, exc: BaseException) -> "TFTPError":
        """The ERROR to send for ``exc``, always a plain :class:`TFTPError` or ``exc`` itself.

        A :class:`TFTPError` is used as is. An ``OSError`` maps by errno
        (``ENOENT`` -> file not found, ``EACCES`` -> access violation,
        ``ENOSPC`` -> disk full, ...). Its message is the generic one for the
        code, never the OS text, which would disclose server paths.
        """
        if isinstance(exc, TFTPError):
            return exc
        if isinstance(exc, OSError):
            code = _ERRNO_CODES.get(exc.errno or 0)
            if code is None:
                if isinstance(exc, FileNotFoundError):
                    code = TFTPErrorCode.FILE_NOT_FOUND
                elif isinstance(exc, PermissionError):
                    code = TFTPErrorCode.ACCESS_VIOLATION
                elif isinstance(exc, FileExistsError):
                    code = TFTPErrorCode.FILE_EXISTS
                else:
                    code = TFTPErrorCode.NOT_DEFINED
            return TFTPError(code)
        return TFTPError(TFTPErrorCode.NOT_DEFINED)

    def _constructor_args(self) -> Tuple[object, ...]:
        """The positional arguments that rebuild this instance: ``args``.

        A subclass whose constructor takes something else returns that, so
        ``copy`` and ``pickle`` (which call ``type(*args)``) work for it.
        """
        return (self.code, self.message)

    def __str__(self) -> str:
        return "%s: %s" % (self.code.name, self.message)


class TFTPProtocolError(TFTPError):
    """The peer broke the protocol (malformed packet, bad option ack...)."""

    def __init__(self, message: str, code: int = TFTPErrorCode.ILLEGAL_OPERATION) -> None:
        super().__init__(code, message)

    def _constructor_args(self) -> Tuple[object, ...]:
        return (self.message, self.code)


class RemoteError(TFTPError):
    """The peer sent an ERROR packet.

    Raised as the subclass for its code (:class:`FileNotFound`,
    :class:`AccessViolation`, ...) so callers can catch by kind; ``code`` and
    ``message`` hold exactly what the peer sent. The leaves describe the
    server's file, so none is also a ``FileNotFoundError`` or
    ``PermissionError``: :mod:`tftp.path` translates where a path caller
    expects the builtin.
    """

    _CODE: "int | None" = None

    def __init__(self, code: "int | None" = None, message: str = "") -> None:
        super().__init__(self._CODE if code is None else code, message)

    @classmethod
    def from_code(cls, code: int, message: str = "") -> "RemoteError":
        """The :class:`RemoteError` subclass instance for ``code`` (``cls`` itself for a code with no leaf)."""
        return _REMOTE_CLASSES.get(code, cls)(code, message)


class FileNotFound(RemoteError):
    """ERROR 1."""

    _CODE = TFTPErrorCode.FILE_NOT_FOUND


class AccessViolation(RemoteError):
    """ERROR 2."""

    _CODE = TFTPErrorCode.ACCESS_VIOLATION


class DiskFull(RemoteError):
    """ERROR 3: disk full or allocation exceeded."""

    _CODE = TFTPErrorCode.DISK_FULL


class IllegalOperation(RemoteError):
    """ERROR 4."""

    _CODE = TFTPErrorCode.ILLEGAL_OPERATION


class UnknownTransferID(RemoteError):
    """ERROR 5."""

    _CODE = TFTPErrorCode.UNKNOWN_TID


class FileAlreadyExists(RemoteError):
    """ERROR 6."""

    _CODE = TFTPErrorCode.FILE_EXISTS


class NoSuchUser(RemoteError):
    """ERROR 7."""

    _CODE = TFTPErrorCode.NO_SUCH_USER


class OptionNegotiationError(RemoteError):
    """ERROR 8: the peer refused option negotiation."""

    _CODE = TFTPErrorCode.OPTION_REFUSED


_REMOTE_CLASSES = {
    cls._CODE: cls
    for cls in (
        FileNotFound,
        AccessViolation,
        DiskFull,
        IllegalOperation,
        UnknownTransferID,
        FileAlreadyExists,
        NoSuchUser,
        OptionNegotiationError,
    )
}


class TransferTimeoutError(TFTPError, TimeoutError):
    """The peer stopped answering, or the transfer ran out of time.

    Also a :class:`TimeoutError`; ``errno`` is ``None``, since no OS call
    failed.
    """

    def __init__(self, message: str = "timed out") -> None:
        super().__init__(TFTPErrorCode.NOT_DEFINED, message)

    def _constructor_args(self) -> Tuple[object, ...]:
        return (self.message,)


class TransferAbortedError(TFTPError):
    """Cancelled locally (``abort()``, a server shutting down)."""

    def __init__(self, message: str = "transfer aborted") -> None:
        super().__init__(TFTPErrorCode.NOT_DEFINED, message)

    def _constructor_args(self) -> Tuple[object, ...]:
        return (self.message,)


class TFTPValueError(TFTPError, ValueError):
    """Text that is not the value it was asked to become: a malformed
    ``tftp://`` URL, for one.

    Also a :class:`ValueError`, so ``except ValueError`` keeps catching it.
    """

    _CODE = TFTPErrorCode.NOT_DEFINED

    def __init__(self, message: str = "") -> None:
        super().__init__(self._CODE, message)

    def _constructor_args(self) -> Tuple[object, ...]:
        return (self.message,)

    def __str__(self) -> str:
        return self.message


class TFTPDecodeError(TFTPValueError):
    """The bytes do not form a valid TFTP packet.

    Carries ERROR 4 (illegal operation), the code a peer is answered with.
    """

    _CODE = TFTPErrorCode.ILLEGAL_OPERATION


class CaptureFormatError(TFTPValueError):
    """Not a pcap/pcapng capture, or a truncated one."""


class CaptureFilterError(TFTPValueError):
    """The filter expression is not valid."""


class WouldBlock(BlockingIOError):
    """Raised by a source or sink that has nothing ready yet.

    A sender whose ``read`` raises it stops after the blocks it has, and a
    receiver whose ``write`` (or ``complete``) raises it holds the block
    unacknowledged; both carry on when the driver calls ``Transfer.resume``.
    This is how a slow backend (an upstream server, an HTTP fetch) applies
    backpressure without blocking the loop that serves every other transfer.
    """


_MESSAGES = {
    TFTPErrorCode.NOT_DEFINED: "error",
    TFTPErrorCode.FILE_NOT_FOUND: "file not found",
    TFTPErrorCode.ACCESS_VIOLATION: "access violation",
    TFTPErrorCode.DISK_FULL: "disk full or allocation exceeded",
    TFTPErrorCode.ILLEGAL_OPERATION: "illegal TFTP operation",
    TFTPErrorCode.UNKNOWN_TID: "unknown transfer ID",
    TFTPErrorCode.FILE_EXISTS: "file already exists",
    TFTPErrorCode.NO_SUCH_USER: "no such user",
    TFTPErrorCode.OPTION_REFUSED: "option negotiation refused",
}


def _default_message(code: int) -> str:
    return _MESSAGES.get(code, "error")  # type: ignore[call-overload]


_ERRNO_CODES = {
    errno.ENOENT: TFTPErrorCode.FILE_NOT_FOUND,
    errno.ENOTDIR: TFTPErrorCode.FILE_NOT_FOUND,
    errno.EISDIR: TFTPErrorCode.ACCESS_VIOLATION,
    errno.EACCES: TFTPErrorCode.ACCESS_VIOLATION,
    errno.EPERM: TFTPErrorCode.ACCESS_VIOLATION,
    errno.EROFS: TFTPErrorCode.ACCESS_VIOLATION,
    errno.EEXIST: TFTPErrorCode.FILE_EXISTS,
    errno.ENOSPC: TFTPErrorCode.DISK_FULL,
    errno.EFBIG: TFTPErrorCode.DISK_FULL,
}
if hasattr(errno, "EDQUOT"):
    _ERRNO_CODES[errno.EDQUOT] = TFTPErrorCode.DISK_FULL
