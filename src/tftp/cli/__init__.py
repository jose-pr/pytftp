"""The ``pytftp`` command line, built on the duho declarative CLI framework::

    pytftp get 192.0.2.1 pxelinux.0
    pytftp put 192.0.2.1 firmware.bin --blksize 1428 --windowsize 16
    pytftp serve /srv/tftp --port 6969 --write

Installed by the ``cli`` extra (``pip install tftp[cli]``). Importing this
module does not require duho: the console script is installed either way,
and :func:`run` is what reports the missing extra.
"""

from __future__ import annotations

import typing as _ty

from .common import AUTO, Args, error
from .serve import Serve
from .transfer import Get, Put

__all__ = ["run", "Pytftp", "Get", "Put", "Serve"]

_NEEDS_EXTRA = "pytftp: the CLI needs the 'cli' extra -- pip install 'tftp[cli]'"


class Pytftp(Args):
    """TFTP client and server (RFC 1350, 2347-2349, 7440) for IPv4 and IPv6."""

    _parsername_ = "pytftp"
    _version_ = AUTO
    _distribution_ = "tftp"
    _subcommands_ = [Get, Put, Serve]


def run(argv: "_ty.Sequence[str] | None" = None) -> "int | None":
    """Console-script entry point.

    A ``ValueError`` out of the library is a caller error (an out-of-range
    ``--blksize``, an unknown mode) and becomes a usage error, exit 2.
    """
    try:
        from duho import main
    except ImportError as exc:
        raise SystemExit(_NEEDS_EXTRA) from exc

    try:
        return main(Pytftp, argv)
    except ValueError as exc:
        error("error: %s" % exc)
        return 2
