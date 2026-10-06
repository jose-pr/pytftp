"""What every command shares: the base classes, the output and a bind failure."""

from __future__ import annotations

import contextlib as _contextlib
import errno as _errno
import sys as _sys
import typing as _ty

from duho import Cmd, LoggingArgs

from .._text import escape
from ..capture._hook import combine_hooks
from ..capture.pcap import PcapWriter
from ..options._profiles import PROFILES

__all__ = [
    "PROFILE_NAMES",
    "Base",
    "Traced",
    "bind_failure",
    "error",
    "flag",
    "write_line",
]

#: ``--compat`` choices.
PROFILE_NAMES = tuple(PROFILES)


def error(text: str) -> None:
    """One diagnostic line on stderr; text a peer chose cannot carry a control character."""
    print(escape(text), file=_sys.stderr)


def write_line(text: str) -> None:
    """One result line on stdout, flushed; ``BrokenPipeError`` if nobody reads it any more.

    A closed pipe is ``BrokenPipeError`` on POSIX and ``OSError(EINVAL)`` on Windows.
    """
    try:
        print(text, flush=True)
    except OSError as exc:
        if isinstance(exc, BrokenPipeError) or exc.errno in (_errno.EPIPE, _errno.EINVAL):
            raise BrokenPipeError(_errno.EPIPE, "stdout is closed") from None
        raise


@_contextlib.contextmanager
def flag(name: str) -> _ty.Iterator[None]:
    """Name ``name`` in the usage error a value of that flag raises (``ValueError``, exit 2)."""
    try:
        yield
    except ValueError as exc:
        raise ValueError("%s: %s" % (name, exc)) from None


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


def _show(event: _ty.Any) -> None:
    print(str(event), file=_sys.stderr, flush=True)


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
        show = _show if self.trace else None
        if self.pcap:
            self._writer = PcapWriter(self.pcap)
        return combine_hooks(show, getattr(self, "_writer", None))

    def _close_trace(self) -> None:
        writer = getattr(self, "_writer", None)
        if writer is not None:
            writer.close()
