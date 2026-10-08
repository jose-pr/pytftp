"""The root parser: the seven subcommands under one program name, and the run around them."""

from __future__ import annotations

import errno as _errno
import os as _os
import sys as _sys
import typing as _ty

from duho import AUTO, Cli, DefaultsFormatter, LoggingArgs
from duho import main as _duho_main

from .._extras import PKTCAP_LINE, have_pktcap
from ._common import error
from ._missing import MissingCapture, MissingReplay
from .get import Get
from .ls import Ls
from .put import Put
from .relay import RelayCmd
from .serve import Serve

__all__ = ["Pytftp", "execute"]

#: The commands that need the ``pktcap`` extra, which stay listed without it.
_NEEDS_PKTCAP = ("capture", "replay")


def _commands() -> _ty.List[_ty.Any]:
    """The subcommands: the two that need pktcap are its commands when it is installed, else stubs."""
    commands: _ty.List[_ty.Any] = [Get, Put, Ls, Serve, RelayCmd]
    if have_pktcap():
        from .capture import CaptureCmd
        from .replay import ReplayCmd

        return commands + [CaptureCmd, ReplayCmd]
    return commands + [MissingCapture, MissingReplay]


def _command_word(argv: _ty.Sequence[str]) -> _ty.Optional[str]:
    """The first word of ``argv`` that is not an option of the root or its value."""
    skip = False
    for word in argv:
        if skip:
            skip = False
        elif word == "--loglevel":
            skip = True
        elif not word.startswith("-"):
            return word
    return None


class Pytftp(LoggingArgs, Cli):
    """TFTP client, server, relay, capture decoder and replay (RFC 1350, 2347-2349, 7440) for IPv4 and IPv6."""

    _parsername_ = "pytftp"
    _logger_name_ = "tftp"
    _version_ = AUTO
    _distribution_ = "tftp"
    # No command is designed to be a tool a program calls: serve and relay never return, and
    # get replaces files, so PYTFTP_MCP=stdio is not read.
    _mcp_ = False
    _help_formatter_ = DefaultsFormatter
    _subcommands_ = _commands()


def _is_pktcap_extra(exc: ImportError) -> bool:
    """Whether ``exc`` is pktcap's ``MissingExtraError``, the one import failure a command reports."""
    if not have_pktcap():
        return False
    import pktcap

    return isinstance(exc, pktcap.MissingExtraError)


def _silence_stdout() -> None:
    """Nobody reads stdout any more: point it at the null device so that the flush at exit does not fail again."""
    try:
        _os.dup2(_os.open(_os.devnull, _os.O_WRONLY), _sys.stdout.fileno())
    except (OSError, ValueError):
        pass


def _stdout_closed(exc: OSError) -> bool:
    """Whether ``exc`` is a write to a pipe its reader closed, which Windows reports as ``EINVAL``.

    The test is that what stdout holds cannot be flushed either.
    """
    if exc.errno not in (_errno.EPIPE, _errno.EINVAL):
        return False
    try:
        _sys.stdout.flush()
    except (OSError, ValueError):
        return True
    return False


def execute(argv: _ty.Optional[_ty.Sequence[str]]) -> int:
    """Run the command line and return its exit status.

    A ``ValueError`` out of the library is a caller error (an out-of-range
    ``--blksize``, an unknown mode) and becomes a usage error, status 2.
    """
    # The parser would answer a capture option it does not know with a usage error before the
    # command could name the extra.
    if _command_word(_sys.argv[1:] if argv is None else argv) in _NEEDS_PKTCAP and not have_pktcap():
        error(PKTCAP_LINE)
        return 1
    try:
        status = _duho_main(Pytftp, argv)
    except ValueError as exc:
        error("error: %s" % exc)
        return 2
    except BrokenPipeError:
        _silence_stdout()
        return 1
    except OSError as exc:  # a file or a socket of a capture command; a hook that failed
        if _stdout_closed(exc):
            _silence_stdout()
            return 1
        error("error: %s" % exc)
        return 1
    except ImportError as exc:  # a capture format whose extra is missing
        if not _is_pktcap_extra(exc):
            raise
        error("error: %s" % exc)
        return 1
    return 0 if status is None else int(status)
