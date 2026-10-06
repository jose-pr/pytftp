"""What every command shares: the base classes, the output and a bind failure."""

from __future__ import annotations

import errno as _errno
import sys as _sys
import typing as _ty

from duho import Cmd, LoggingArgs

from .._result import TransferResult
from .._text import escape
from ..capture.pcap import PcapWriter
from ..options._profiles import PROFILES
from ..server._session import PortRange

__all__ = [
    "PROFILE_NAMES",
    "Base",
    "Traced",
    "bind_failure",
    "error",
    "port_range",
    "result_json",
    "write_line",
]

#: ``--compat`` choices.
PROFILE_NAMES = tuple(PROFILES)


def error(text: str) -> None:
    """One diagnostic line on stderr; text a peer chose cannot carry a control character."""
    print(escape(text), file=_sys.stderr)


class _StdoutClosed(Exception):
    """The reader of stdout went away: the command has nothing left to say."""


def write_line(text: str) -> None:
    """One result line on stdout, flushed; :class:`_StdoutClosed` if nobody reads it any more.

    A closed pipe is ``BrokenPipeError`` on POSIX and ``OSError(EINVAL)`` on Windows.
    """
    try:
        print(text, flush=True)
    except OSError as exc:
        if isinstance(exc, BrokenPipeError) or exc.errno in (_errno.EPIPE, _errno.EINVAL):
            raise _StdoutClosed from None
        raise


def port_range(text: _ty.Optional[str]) -> _ty.Optional[PortRange]:
    """``LOW:HIGH`` (or ``LOW-HIGH``) for ``--port-range``: a ``PortRange``, or ``ValueError``."""
    if not text:
        return None
    try:
        return PortRange.parse(text)
    except ValueError:
        raise ValueError("--port-range expects LOW:HIGH within 1..65535, got %r" % text) from None


def result_json(result: TransferResult) -> dict:
    negotiated = result.negotiated
    return {
        "ok": result.is_ok,
        "operation": result.operation,
        "filename": result.filename,
        "mode": result.mode,
        "peer": list(result.peer[:2]),
        "bytes": result.bytes,
        "blocks": result.blocks,
        "retransmits": result.retransmits,
        "duration": round(result.duration, 6),
        "blksize": negotiated.blksize,
        "windowsize": negotiated.windowsize,
        "tsize": negotiated.tsize,
        "options": negotiated.options,
        "error": None if result.error is None else str(result.error),
    }


def bind_failure(exc: OSError, address: str, port: int) -> int:
    """Report a listening socket that could not be bound; exit status 1."""
    hint = None
    try:
        from netimps import bind_error_hint

        hint = bind_error_hint(exc, port)
    except ImportError:  # pragma: no cover
        pass
    error("error: cannot listen on %s port %d: %s" % (address, port, hint or exc))
    return 1


class Base(LoggingArgs, Cmd):
    """Shared options."""

    _logger_name_ = "tftp"

    json_out: bool = False
    "Emit JSON on stdout instead of human-readable text"
    ("--json",)


class Traced(Base):
    """Commands that move packets can show them."""

    trace: bool = False
    "Print every datagram sent and received on stderr"
    ("--trace",)

    pcap: _ty.Optional[str] = None
    "Also write every datagram to this pcap file (opens in Wireshark)"
    ("--pcap",)

    def _tracer(self) -> _ty.Optional[_ty.Callable[[_ty.Any], None]]:
        """The hook for ``--trace`` and ``--pcap``, or ``None``.

        The pcap file is created here, so a command calls this last: after its
        arguments are accepted and its sockets bound, never before.
        """
        hooks: _ty.List[_ty.Callable[[_ty.Any], None]] = []
        if self.trace:

            def show(event: _ty.Any) -> None:
                print(str(event), file=_sys.stderr, flush=True)

            hooks.append(show)
        if self.pcap:
            self._writer = PcapWriter(self.pcap)
            hooks.append(self._writer)
        if not hooks:
            return None
        if len(hooks) == 1:
            return hooks[0]

        def combined(event: _ty.Any) -> None:
            for hook in hooks:
                hook(event)

        return combined

    def _close_trace(self) -> None:
        writer = getattr(self, "_writer", None)
        if writer is not None:
            writer.close()
