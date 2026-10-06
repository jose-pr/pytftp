"""Pure-Python TFTP client and server for IPv4 and IPv6.

Implements RFC 1350 (the protocol), RFC 2347 (option extension), RFC 2348
(``blksize``), RFC 2349 (``timeout``, ``tsize``) and RFC 7440
(``windowsize``), plus the widely deployed ``utimeout`` and ``rollover``
options, netascii mode, and unlimited file size through block-number
rollover::

    import tftp

    tftp.TFTPClient("192.0.2.1").download("pxelinux.0", "pxelinux.0")

    with tftp.TFTPServer("/srv/tftp", port=6969) as server:
        server.serve_forever()

``tftp`` exports what the common task needs: the clients and servers with their
asyncio twins, the one-shot transfer functions, the exceptions, the result and
URL types, the handler contract, the policy classes, the packet types and the
engine's ``Sender`` and ``Receiver``. Everything else lives in its role module,
which is that name's one home: :mod:`tftp.client`, :mod:`tftp.server`,
:mod:`tftp.relay`, :mod:`tftp.capture`, :mod:`tftp.options`, :mod:`tftp.packet`,
:mod:`tftp.backends`, :mod:`tftp.path`, :mod:`tftp.exceptions` and
:mod:`tftp.cli` (needs the ``cli`` extra); the engine, the ``x-list`` format and netascii
keep their own topic modules: :mod:`tftp.transfer`, :mod:`tftp.listing` and
:mod:`tftp.netascii`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, List

from .packet import (
    AckPacket,
    DataPacket,
    ErrorPacket,
    TFTPErrorCode,
    TFTPOpcode,
    OptionAckPacket,
    RequestPacket,
    decode,
)
from .exceptions import (
    AccessViolation,
    DiskFull,
    FileAlreadyExists,
    FileNotFound,
    IllegalOperation,
    NoSuchUser,
    OptionNegotiationError,
    RemoteError,
    TFTPDecodeError,
    TFTPError,
    TFTPProtocolError,
    TFTPValueError,
    TransferAbortedError,
    TransferTooLargeError,
    TransferTimeoutError,
    UnknownTransferID,
    WouldBlock,
)
from .client import TFTPClient, download, upload
from .listing import ListEntry
from .backends import FilesystemBackend
from .server import (
    AtomicWriter,
    TFTPHandler,
    TFTPRequestContext,
    TFTPServer,
    TFTPServerLimits,
)
from .netascii import NetasciiReader, NetasciiWriter
from .options import Profile, TFTPServerOptions
from ._result import TransferResult
from .transfer import Receiver, Sender
from ._uri import TFTPURL, download_url, upload_url

__all__ = [
    "__version__",
    # Client
    "TFTPClient",
    "AsyncTFTPClient",
    "ListEntry",
    "download",
    "upload",
    "TFTPURL",
    "download_url",
    "upload_url",
    # Server
    "TFTPServer",
    "AsyncTFTPServer",
    "TFTPServerOptions",
    "TFTPServerLimits",
    "TFTPHandler",
    "FilesystemBackend",
    "AtomicWriter",
    "TFTPRequestContext",
    # Results and errors
    "TransferResult",
    "TFTPError",
    "TFTPValueError",
    "TFTPDecodeError",
    "RemoteError",
    "TFTPProtocolError",
    "TransferTimeoutError",
    "TransferAbortedError",
    "TransferTooLargeError",
    "FileNotFound",
    "AccessViolation",
    "DiskFull",
    "IllegalOperation",
    "UnknownTransferID",
    "FileAlreadyExists",
    "NoSuchUser",
    "OptionNegotiationError",
    "WouldBlock",
    # Protocol
    "TFTPOpcode",
    "TFTPErrorCode",
    "RequestPacket",
    "DataPacket",
    "AckPacket",
    "ErrorPacket",
    "OptionAckPacket",
    "decode",
    # Options and profiles
    "Profile",
    # Building blocks
    "Sender",
    "Receiver",
    "NetasciiReader",
    "NetasciiWriter",
]


def _installed_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("tftp")
    except PackageNotFoundError:  # pragma: no cover - a bare source tree
        return "0.0.0+unknown"


__version__ = _installed_version()

if TYPE_CHECKING:
    from .client import AsyncTFTPClient
    from .server import AsyncTFTPServer

#: The asyncio twins, bound on first use so that importing the package does not import asyncio.
_ASYNCIO_TWINS = {"AsyncTFTPClient": ".client", "AsyncTFTPServer": ".server"}


def __getattr__(name: str) -> Any:
    home = _ASYNCIO_TWINS.get(name)
    if home is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    from importlib import import_module

    value = getattr(import_module(home, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> List[str]:
    return sorted(set(globals()) | set(__all__))
