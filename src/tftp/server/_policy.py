"""Resource limits for a network-facing server."""

from __future__ import annotations

from typing import Optional

from ..exceptions import TFTPError
from ..packet import TFTPErrorCode, RequestPacket

__all__ = ["TFTPServerLimits"]


class TFTPServerLimits:
    """Bounds on what one request, one client and one transfer may use.

    :param max_request_size: bytes in an RRQ/WRQ datagram. RFC 2347 keeps a
        request within 512 octets; the default allows twice that.
    :param max_filename_length: characters in a requested filename.
    :param max_options: options in one request.
    :param max_option_length: characters in one option name or value.
    :param max_sessions_per_client: concurrent transfers from one client
        address (any port); ``None`` is unlimited.
    :param max_duration: seconds one transfer may take; ``None`` is unlimited.
    :param max_idle: seconds a transfer may go with no datagram from its
        peer before it ends; a timeout the client negotiated cannot extend
        it. Time the server spends waiting on its own handler does not count.
        ``None`` is unbounded.

    A request over a limit is answered with ERROR 4 (ERROR 0 "server busy"
    for the per-client cap). Window memory is bounded separately, by
    :class:`TFTPServerOptions` ``max_window_bytes``.
    """

    __slots__ = (
        "max_request_size",
        "max_filename_length",
        "max_options",
        "max_option_length",
        "max_sessions_per_client",
        "max_duration",
        "max_idle",
    )

    def __init__(
        self,
        *,
        max_request_size: int = 1024,
        max_filename_length: int = 512,
        max_options: int = 16,
        max_option_length: int = 255,
        max_sessions_per_client: Optional[int] = None,
        max_duration: Optional[float] = None,
        max_idle: Optional[float] = 60.0,
    ) -> None:
        if max_idle is not None and max_idle <= 0:
            raise ValueError("max_idle must be positive, or None for no bound")
        self.max_request_size = max_request_size
        self.max_filename_length = max_filename_length
        self.max_options = max_options
        self.max_option_length = max_option_length
        self.max_sessions_per_client = max_sessions_per_client
        self.max_duration = max_duration
        self.max_idle = max_idle

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TFTPServerLimits):
            return NotImplemented
        return all(getattr(self, name) == getattr(other, name) for name in self.__slots__)

    #: Mutable, so not hashable.
    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return "TFTPServerLimits(%s)" % ", ".join(
            "%s=%r" % (name, getattr(self, name)) for name in self.__slots__
        )

    def check(self, request: RequestPacket) -> None:
        """Raise :class:`TFTPError` (4) if ``request`` exceeds a limit."""
        if len(request.filename) > self.max_filename_length:
            raise TFTPError(TFTPErrorCode.ILLEGAL_OPERATION, "filename too long")
        if len(request.options) > self.max_options:
            raise TFTPError(TFTPErrorCode.ILLEGAL_OPERATION, "too many options")
        for name, value in request.options.items():
            if len(name) > self.max_option_length or len(value) > self.max_option_length:
                raise TFTPError(TFTPErrorCode.ILLEGAL_OPERATION, "option too long")
