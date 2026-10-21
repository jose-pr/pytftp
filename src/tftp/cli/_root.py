"""The root parser: the seven subcommands under one program name, and the run around them."""

from __future__ import annotations

import sys as _sys
import typing as _ty

from duho import AUTO, Cli, ConfigArgs, DefaultsFormatter, LoggingArgs, run
from duho.config import user_path
from duho.exceptions import ConfigError

from .._extras import PKTCAP_LINE, have_pktcap
from ._common import error
from ._missing import MissingCapture, MissingReplay
from ._settings import command_word, read_settings
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


def _errors() -> _ty.Dict[type, int]:
    """The exceptions a command ends with, and the status of each.

    A ``ValueError`` out of the library is a caller error (an out-of-range ``--blksize``, an unknown mode, a
    settings file that cannot be used): a usage error, status 2. An ``OSError`` is a file or a socket of a
    command, or a hook that failed. Without pktcap, a capture format that needs the extra says so.
    """
    errors: _ty.Dict[type, int] = {ValueError: 2, OSError: 1}
    if have_pktcap():
        import pktcap

        errors[pktcap.MissingExtraError] = 1
    return errors


def _report_error(message: str, code: int, exc: BaseException) -> None:
    """One ``error: ...`` line on stderr; duho's own check of the settings file keeps the program name.

    The check is the ``ConfigError`` duho raises itself, which names no file of its own: the readers' name the
    file they were reading.
    """
    prefix = "%s: " % Pytftp._parsername_ if type(exc) is ConfigError and exc.path is None else ""
    error("%serror: %s" % (prefix, message))


class Pytftp(ConfigArgs, LoggingArgs, Cli):
    """TFTP client, server, relay, capture decoder and replay (RFC 1350, 2347-2349, 7440) for IPv4 and IPv6."""

    _parsername_ = "pytftp"
    _logger_name_ = "tftp"
    _config_env_ = "PYTFTP_CONFIG"
    _config_ = user_path("pytftp", "pytftp.ini")
    _config_loader_ = staticmethod(read_settings)
    _config_validate_ = "strict"
    _version_ = AUTO
    _distribution_ = "tftp"
    # No command is designed to be a tool a program calls: serve and relay never return, and
    # get replaces files, so PYTFTP_MCP=stdio is not read.
    _mcp_ = False
    _help_formatter_ = DefaultsFormatter
    _subcommands_ = _commands()
    _errors_ = _errors()
    _report_error_ = staticmethod(_report_error)


def execute(argv: _ty.Optional[_ty.Sequence[str]]) -> int:
    """Run the command line and return its exit status.

    The parser ends with a ``SystemExit`` for ``--help``, ``--version`` and a usage error; everything else is
    returned, a closed stdout and Ctrl-C included.
    """
    tokens = list(_sys.argv[1:] if argv is None else argv)
    # The parser would answer a capture option it does not know with a usage error before the
    # command could name the extra.
    if command_word(tokens) in _NEEDS_PKTCAP and not have_pktcap():
        error(PKTCAP_LINE)
        return 1
    return run(Pytftp, argv, exit="parse")
