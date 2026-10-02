"""Pure-Python TFTP client and server for IPv4 and IPv6.

Implements RFC 1350 (the protocol), RFC 2347 (option extension), RFC 2348
(``blksize``), RFC 2349 (``timeout``, ``tsize``) and RFC 7440
(``windowsize``), plus the widely deployed ``utimeout`` and ``rollover``
options, netascii mode, and unlimited file size through block-number
rollover::

    import tftp

    tftp.Client("192.0.2.1").download("pxelinux.0", "pxelinux.0")

    with tftp.Server("/srv/tftp", port=6969) as server:
        server.serve_forever()

Everything public is importable from ``tftp`` directly. The subpackages
group it for reading: :mod:`tftp.packet` (wire format), :mod:`tftp.options`
(negotiation), :mod:`tftp.transfer` (the I/O-free engine),
:mod:`tftp.client`, :mod:`tftp.server`, :mod:`tftp.netascii`,
:mod:`tftp.uri` (``tftp://`` URLs), and
:mod:`tftp.cli` (needs the ``cli`` extra).
"""

from __future__ import annotations

from .client import MODES, Client, download, upload
from .errors import (
    AccessViolation,
    DiskFull,
    FileAlreadyExists,
    FileNotFound,
    IllegalOperation,
    NoSuchUser,
    OptionNegotiationError,
    ProtocolError,
    RemoteError,
    TftpError,
    TransferAborted,
    TransferTimeout,
    UnknownTransferId,
)
from .server import AtomicWriter, FileSystemHandler, Handler, PortRange, RequestContext, Server, ServerLimits
from .netascii import NetasciiReader, NetasciiWriter
from .options import (
    DEFAULT,
    DEFAULT_BLKSIZE,
    DEFAULT_REGISTRY,
    EXTENSION_OPTIONS,
    HPA,
    LEGACY,
    MAX_BLKSIZE,
    MAX_WINDOWSIZE,
    MIN_BLKSIZE,
    PROFILES,
    PXE,
    STANDARD_OPTIONS,
    STRICT,
    SUPPORTED_OPTIONS,
    Negotiated,
    OptionHandler,
    OptionRegistry,
    Profile,
    ServerOptions,
    register_option,
)
from .packet import (
    Ack,
    Data,
    Error,
    ErrorCode,
    MalformedPacket,
    Opcode,
    OptionAck,
    Request,
    decode,
    encode_ack,
    encode_data,
    encode_error,
    encode_oack,
    encode_request,
)
from .result import TransferResult
from .transfer import Receiver, Sender, WouldBlock
from .uri import TftpURL, download_url, format_url, parse_url, upload_url

__all__ = [
    "__version__",
    # Client
    "Client",
    "download",
    "upload",
    "MODES",
    "TftpURL",
    "parse_url",
    "format_url",
    "download_url",
    "upload_url",
    # Server
    "Server",
    "ServerOptions",
    "ServerLimits",
    "PortRange",
    "Handler",
    "FileSystemHandler",
    "AtomicWriter",
    "RequestContext",
    # Results and errors
    "TransferResult",
    "Negotiated",
    "TftpError",
    "RemoteError",
    "ProtocolError",
    "TransferTimeout",
    "TransferAborted",
    "FileNotFound",
    "AccessViolation",
    "DiskFull",
    "IllegalOperation",
    "UnknownTransferId",
    "FileAlreadyExists",
    "NoSuchUser",
    "OptionNegotiationError",
    # Protocol
    "Opcode",
    "ErrorCode",
    "Request",
    "Data",
    "Ack",
    "Error",
    "OptionAck",
    "MalformedPacket",
    "decode",
    "encode_request",
    "encode_data",
    "encode_ack",
    "encode_error",
    "encode_oack",
    "DEFAULT_BLKSIZE",
    "MIN_BLKSIZE",
    "MAX_BLKSIZE",
    "MAX_WINDOWSIZE",
    "SUPPORTED_OPTIONS",
    "STANDARD_OPTIONS",
    "EXTENSION_OPTIONS",
    # Options and profiles
    "OptionHandler",
    "OptionRegistry",
    "DEFAULT_REGISTRY",
    "register_option",
    "Profile",
    "PROFILES",
    "STRICT",
    "DEFAULT",
    "PXE",
    "HPA",
    "LEGACY",
    # Building blocks
    "Sender",
    "Receiver",
    "WouldBlock",
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
