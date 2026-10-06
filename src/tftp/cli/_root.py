"""The root parser: the six subcommands under one program name, and the run around them."""

from __future__ import annotations

import os as _os
import sys as _sys
import typing as _ty

from duho import AUTO, Cli, LoggingArgs
from duho import main as _duho_main

from ._common import _StdoutClosed, error
from .capture import CaptureCmd
from .get import Get
from .ls import Ls
from .put import Put
from .relay import RelayCmd
from .serve import Serve

__all__ = ["Pytftp", "execute"]


class Pytftp(LoggingArgs, Cli):
    """TFTP client, server, relay and capture decoder (RFC 1350, 2347-2349, 7440) for IPv4 and IPv6."""

    _parsername_ = "pytftp"
    _logger_name_ = "tftp"
    _version_ = AUTO
    _distribution_ = "tftp"
    # No command is designed to be a tool a program calls: serve and relay never return, and
    # get replaces files, so PYTFTP_MCP=stdio is not read.
    _mcp_ = False
    _subcommands_ = [Get, Put, Ls, Serve, RelayCmd, CaptureCmd]


def execute(argv: _ty.Optional[_ty.Sequence[str]]) -> int:
    """Run the command line and return its exit status.

    A ``ValueError`` out of the library is a caller error (an out-of-range
    ``--blksize``, an unknown mode) and becomes a usage error, status 2.
    """
    try:
        status = _duho_main(Pytftp, argv)
    except ValueError as exc:
        error("error: %s" % exc)
        return 2
    except _StdoutClosed:
        # Nobody reads stdout any more. Point it at the null device so that the flush at exit
        # does not fail again, and end without a word.
        try:
            _os.dup2(_os.open(_os.devnull, _os.O_WRONLY), _sys.stdout.fileno())
        except (OSError, ValueError):
            pass
        return 1
    return 0 if status is None else int(status)
