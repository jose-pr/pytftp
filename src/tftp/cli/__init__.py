"""The ``pytftp`` command line, built on the duho declarative CLI framework::

    pytftp get 192.0.2.1 pxelinux.0
    pytftp get tftp://192.0.2.1/boot/pxelinux.0 --trace
    pytftp put 192.0.2.1 firmware.bin --blksize 1428 --windowsize 16
    pytftp ls 192.0.2.1 boot
    pytftp serve /srv/tftp --port 6969 --write --listing
    pytftp serve --http https://images.example.com/pxe/ --compat pxe
    pytftp relay 10.0.0.20 --route-subnet 10.1.0.0/16=10.1.0.5 --pcap relay.pcap
    pytftp capture --input boot.pcapng --transfers --extract recovered/

Installed by the ``cli`` extra (``pip install tftp[cli]``). Importing this
package does not require duho: the console script is installed either way,
and :func:`main` is what reports the missing extra.
"""

from __future__ import annotations

import typing as _ty

__all__ = ["main"]

_NEEDS_EXTRA = "pytftp: the CLI needs the 'cli' extra -- pip install 'tftp[cli]'"


def main(argv: _ty.Optional[_ty.Sequence[str]] = None) -> int:
    """Run the command line and return its exit status.

    ``0`` the command succeeded, ``1`` the transfer or the peer failed, ``2``
    the invocation was wrong. The parser itself exits with ``2`` through
    :class:`SystemExit` on a usage error.

    Without duho installed this raises :class:`SystemExit` naming the ``cli``
    extra.
    """
    try:
        import duho  # noqa: F401
    except ImportError as exc:
        raise SystemExit(_NEEDS_EXTRA) from exc

    from ._root import execute

    return execute(argv)
