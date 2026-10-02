"""The ``pytftp`` command line, built on the duho declarative CLI framework::

    pytftp get 192.0.2.1 pxelinux.0
    pytftp get tftp://192.0.2.1/boot/pxelinux.0 --trace
    pytftp put 192.0.2.1 firmware.bin --blksize 1428 --windowsize 16
    pytftp ls 192.0.2.1 boot
    pytftp serve /srv/tftp --port 6969 --write --listing
    pytftp serve --http https://images.example.com/pxe/ --compat pxe
    pytftp relay 10.0.0.20 --route-subnet 10.1.0.0/16=10.1.0.5 --pcap relay.pcap
    pytftp capture boot.pcapng --transfers --extract recovered/

Installed by the ``cli`` extra (``pip install tftp[cli]``). Importing this
module does not require duho: the console script is installed either way,
and :func:`run` is what reports the missing extra.
"""

from __future__ import annotations

import typing as _ty

from .common import AUTO, Args, error
from .capture import CaptureCmd
from .relay import RelayCmd
from .serve import Serve
from .transfer import Get, Ls, Put

__all__ = ["run", "Pytftp", "Get", "Put", "Ls", "Serve", "RelayCmd", "CaptureCmd"]

_NEEDS_EXTRA = "pytftp: the CLI needs the 'cli' extra -- pip install 'tftp[cli]'"


class Pytftp(Args):
    """TFTP client, server, relay and capture decoder (RFC 1350, 2347-2349, 7440) for IPv4 and IPv6."""

    _parsername_ = "pytftp"
    _version_ = AUTO
    _distribution_ = "tftp"
    _subcommands_ = [Get, Put, Ls, Serve, RelayCmd, CaptureCmd]


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
