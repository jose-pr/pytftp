"""TFTP wire format: packet types and encode/decode.

Covers every packet RFC 1350 and RFC 2347 define: RRQ, WRQ, DATA, ACK, ERROR
and OACK. The transfer engine does not go through :func:`decode` on its hot
path -- it reads the opcode and block number straight out of the receive
buffer -- so the objects here are for requests, errors, option
acknowledgements and for callers inspecting traffic.

The five packet types are immutable values that equal only their own type.
Each has ``decode(data)`` (a classmethod), ``encode()`` and ``bytes(packet)``;
the module-level ``encode_*`` functions build the same bytes without an object.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import List, Mapping, Optional, Tuple, Union

from ..exceptions import TFTPDecodeError
from .enums import TFTPErrorCode, TFTPOpcode

__all__ = [
    "RequestPacket",
    "DataPacket",
    "AckPacket",
    "ErrorPacket",
    "OptionAckPacket",
    "TFTPPacket",
    "decode",
    "encode_request",
    "encode_data",
    "encode_ack",
    "encode_error",
    "encode_oack",
    "FILENAME_ENCODING",
]

#: Filenames and option text are bytes on the wire. UTF-8 with
#: ``surrogateescape`` round-trips any byte string losslessly while still
#: giving real UTF-8 names their natural ``str``.
FILENAME_ENCODING = "utf-8"
_ERRORS = "surrogateescape"

_HDR = struct.Struct("!HH")
_OP = struct.Struct("!H")

#: Most octets a request packet may have (RFC 2347 section 2).
MAX_REQUEST_SIZE = 512
#: Most octets of message text an ERROR carries.
MAX_ERROR_TEXT = 512
#: The modes RFC 1350 defines, which a request may carry.
_MODES = frozenset({"netascii", "octet", "mail"})


def _check_block(block: object) -> None:
    if isinstance(block, bool) or not isinstance(block, int):
        raise TypeError("a block number is an int, not %s" % type(block).__name__)
    if not 0 <= block <= 65535:
        raise ValueError("a block number is 0 to 65535, not %d" % block)


def _option_pairs(options: Optional[Mapping[str, object]]) -> List[Tuple[str, str]]:
    """``(name, text)`` pairs of ``options``, checked: a name is non-empty text,
    a value is text or an ``int`` (not a ``bool``), written in decimal."""
    pairs: List[Tuple[str, str]] = []
    for name, value in (options or {}).items():
        if not isinstance(name, str):
            raise TypeError("an option name is text, not %s" % type(name).__name__)
        if not name:
            raise ValueError("an option name cannot be empty")
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise TypeError("the value of option %r is text or an int, not %s" % (name, type(value).__name__))
        pairs.append((name, str(value)))
    return pairs


def _freeze_options(options: Optional[Mapping[str, object]]) -> "Mapping[str, str]":
    """A read-only copy of ``options`` with lower-cased names and text values."""
    return MappingProxyType({name.lower(): value for name, value in _option_pairs(options)})


class _Packet:
    """What the five packet types share: ``bytes(packet)`` is ``packet.encode()``."""

    __slots__ = ()

    def __bytes__(self) -> bytes:
        return self.encode()  # type: ignore[attr-defined]


@dataclass(frozen=True, repr=False)
class RequestPacket(_Packet):
    """A read (RRQ) or write (WRQ) request.

    ``options`` is a read-only mapping with lower-cased names, as RFC 2347
    makes option names case-insensitive; values are text, kept as sent, in
    request order. ``raw`` is the datagram exactly as received (empty for a
    request built in code), so a relay can forward it untouched -- option
    spelling, order, duplicates and options this library does not know all
    survive. ``raw`` is not part of equality. ``mode`` is lower-cased and not
    checked, because a request is decoded as the peer sent it;
    :meth:`encode` refuses a mode RFC 1350 does not define.
    """

    opcode: TFTPOpcode
    filename: str
    mode: str
    options: "Mapping[str, str]" = field(default_factory=dict)
    raw: bytes = field(default=b"", compare=False, repr=False)

    def __post_init__(self) -> None:
        opcode = TFTPOpcode(self.opcode)
        if opcode not in (TFTPOpcode.RRQ, TFTPOpcode.WRQ):
            raise ValueError("a request opcode is RRQ or WRQ, not %r" % (opcode,))
        if not isinstance(self.filename, str) or not isinstance(self.mode, str):
            raise TypeError("a request's file name and mode are text")
        if not self.filename:
            raise ValueError("a request needs a file name")
        object.__setattr__(self, "opcode", opcode)
        object.__setattr__(self, "mode", self.mode.lower())
        object.__setattr__(self, "options", _freeze_options(self.options))
        object.__setattr__(self, "raw", bytes(self.raw))

    def __repr__(self) -> str:
        return "RequestPacket(TFTPOpcode.%s, %r, %r, %r)" % (
            self.opcode.name,
            self.filename,
            self.mode,
            dict(self.options),
        )

    def __hash__(self) -> int:
        return hash((self.opcode, self.filename, self.mode, frozenset(self.options.items())))

    def __reduce__(self):
        return (type(self), (self.opcode, self.filename, self.mode, dict(self.options), self.raw))

    @property
    def is_read(self) -> bool:
        return self.opcode == TFTPOpcode.RRQ

    @property
    def raw_options(self) -> List[Tuple[str, str]]:
        """Every option pair as sent: original case, order and duplicates."""
        if not self.raw:
            return list(self.options.items())
        fields = _strings(self.raw[2:])[2:]
        return [(fields[i], fields[i + 1]) for i in range(0, len(fields) - 1, 2)]

    @classmethod
    def decode(cls, data: Union[bytes, bytearray, memoryview]) -> "RequestPacket":
        """The request ``data`` holds; :class:`TFTPDecodeError` for another packet."""
        return _decode_kind(cls, data)

    def encode(self) -> bytes:
        """``raw`` when there is one, else a fresh encoding (:func:`encode_request`)."""
        return self.raw or encode_request(self.opcode, self.filename, self.mode, self.options)


@dataclass(frozen=True, repr=False)
class DataPacket(_Packet):
    """DATA: block number ``block`` (0 to 65535) carrying ``data``."""

    block: int
    data: bytes

    def __post_init__(self) -> None:
        _check_block(self.block)
        if isinstance(self.data, str):
            raise TypeError("DATA carries bytes, not text")
        object.__setattr__(self, "data", bytes(self.data))

    def __repr__(self) -> str:
        return "DataPacket(%d, %r)" % (self.block, self.data)

    @classmethod
    def decode(cls, data: Union[bytes, bytearray, memoryview]) -> "DataPacket":
        """The DATA ``data`` holds; :class:`TFTPDecodeError` for another packet."""
        return _decode_kind(cls, data)

    def encode(self) -> bytes:
        return encode_data(self.block, self.data)


@dataclass(frozen=True, repr=False)
class AckPacket(_Packet):
    """ACK of block ``block`` (0 to 65535)."""

    block: int

    def __post_init__(self) -> None:
        _check_block(self.block)

    def __repr__(self) -> str:
        return "AckPacket(%d)" % self.block

    @classmethod
    def decode(cls, data: Union[bytes, bytearray, memoryview]) -> "AckPacket":
        """The ACK ``data`` holds; :class:`TFTPDecodeError` for another packet."""
        return _decode_kind(cls, data)

    def encode(self) -> bytes:
        return encode_ack(self.block)


@dataclass(frozen=True, repr=False)
class ErrorPacket(_Packet):
    """ERROR: ``code`` is a :class:`TFTPErrorCode`, which carries a number this
    library does not define (any 16-bit value) as an unnamed member."""

    code: TFTPErrorCode
    message: str

    def __post_init__(self) -> None:
        if isinstance(self.code, bool) or not isinstance(self.code, int):
            raise TypeError("an error code is an int, not %s" % type(self.code).__name__)
        if not isinstance(self.message, str):
            raise TypeError("an error message is text, not %s" % type(self.message).__name__)
        object.__setattr__(self, "code", TFTPErrorCode(self.code))

    def __repr__(self) -> str:
        code = "TFTPErrorCode.%s" % self.code.name if self.code in _DEFINED else str(int(self.code))
        return "ErrorPacket(%s, %r)" % (code, self.message)

    @classmethod
    def decode(cls, data: Union[bytes, bytearray, memoryview]) -> "ErrorPacket":
        """The ERROR ``data`` holds; :class:`TFTPDecodeError` for another packet."""
        return _decode_kind(cls, data)

    def encode(self) -> bytes:
        """The strict encoding, :func:`encode_error`."""
        return encode_error(self.code, self.message)


@dataclass(frozen=True, repr=False)
class OptionAckPacket(_Packet):
    """OACK: the options a server accepted, a read-only mapping of lower-cased
    names to text values."""

    options: "Mapping[str, str]"

    def __post_init__(self) -> None:
        object.__setattr__(self, "options", _freeze_options(self.options))

    def __repr__(self) -> str:
        return "OptionAckPacket(%r)" % (dict(self.options),)

    def __hash__(self) -> int:
        return hash(frozenset(self.options.items()))

    def __reduce__(self):
        return (type(self), (dict(self.options),))

    @classmethod
    def decode(cls, data: Union[bytes, bytearray, memoryview]) -> "OptionAckPacket":
        """The OACK ``data`` holds; :class:`TFTPDecodeError` for another packet."""
        return _decode_kind(cls, data)

    def encode(self) -> bytes:
        return encode_oack(self.options)


_DEFINED = frozenset(TFTPErrorCode)

TFTPPacket = Union[RequestPacket, DataPacket, AckPacket, ErrorPacket, OptionAckPacket]


def _text(value: object, what: str) -> bytes:
    if not isinstance(value, str):
        raise TypeError("%s is text, not %s" % (what, type(value).__name__))
    raw = value.encode(FILENAME_ENCODING, _ERRORS)
    if b"\0" in raw:
        raise ValueError("NUL is not allowed in %s: %r" % (what, value))
    return raw


def _options_bytes(options: Optional[Mapping[str, object]]) -> bytes:
    parts = []
    for name, value in _option_pairs(options):
        parts.append(_text(name, "an option name"))
        parts.append(_text(value, "an option value"))
    return b"\0".join(parts) + b"\0" if parts else b""


def encode_request(
    opcode: int,
    filename: str,
    mode: str = "octet",
    options: Optional[Mapping[str, object]] = None,
) -> bytes:
    """Build an RRQ or WRQ.

    Option values may be text or an ``int``, sent in decimal. Raises
    ``ValueError`` for an opcode that is not RRQ or WRQ, an empty file name or
    option name, a NUL in any string, a ``mode`` other than ``octet``,
    ``netascii`` or ``mail`` (any case) and a request over the 512 octets
    RFC 2347 allows; ``TypeError`` for an argument of the wrong type.
    """
    if opcode not in (TFTPOpcode.RRQ, TFTPOpcode.WRQ):
        raise ValueError("a request opcode is RRQ or WRQ, not %r" % (opcode,))
    name = _text(filename, "a file name")
    if not name:
        raise ValueError("a request needs a file name")
    mode_text = _text(mode, "a mode")
    if mode.lower() not in _MODES:
        raise ValueError("a request mode is netascii, octet or mail, not %r" % (mode,))
    packet = _OP.pack(opcode) + name + b"\0" + mode_text + b"\0" + _options_bytes(options)
    if len(packet) > MAX_REQUEST_SIZE:
        raise ValueError("a request is at most %d octets, this one is %d" % (MAX_REQUEST_SIZE, len(packet)))
    return packet


def encode_data(block: int, data: bytes) -> bytes:
    """Build a DATA. ``ValueError`` for a block outside 0..65535, ``TypeError`` for a non-``int``."""
    _check_block(block)
    return _HDR.pack(TFTPOpcode.DATA, block) + bytes(data)


def encode_ack(block: int) -> bytes:
    """Build an ACK. ``ValueError`` for a block outside 0..65535, ``TypeError`` for a non-``int``."""
    _check_block(block)
    return _HDR.pack(TFTPOpcode.ACK, block)


def encode_error(code: int, message: str = "") -> bytes:
    """Build an ERROR.

    Raises ``ValueError`` for a ``code`` outside 0..65535, a NUL in
    ``message`` and a message over 512 octets once encoded; ``TypeError`` for
    a ``code`` that is not an ``int`` or a ``message`` that is not text.
    """
    if isinstance(code, bool) or not isinstance(code, int):
        raise TypeError("an error code is an int, not %s" % type(code).__name__)
    if not 0 <= code <= 65535:
        raise ValueError("an error code is 0 to 65535, not %d" % code)
    raw = _text(message, "an error message")
    if len(raw) > MAX_ERROR_TEXT:
        raise ValueError("an error message is at most %d octets, this one is %d" % (MAX_ERROR_TEXT, len(raw)))
    return _HDR.pack(TFTPOpcode.ERROR, code) + raw + b"\0"


def _encode_error(code: object, message: object = "") -> bytes:
    """An ERROR for the engine, the servers and the clients; it never raises.

    A code that is not an ``int`` in 0..65535 is sent as 0, a message that is
    not text as ``str(message)``, a NUL becomes ``?`` and the text is cut to
    ``MAX_ERROR_TEXT`` octets on a character boundary: a failure being
    reported must always reach the peer.
    """
    if isinstance(code, bool) or not isinstance(code, int) or not 0 <= code <= 65535:
        code = 0
    if not isinstance(message, str):
        message = str(message)
    raw = message.encode(FILENAME_ENCODING, _ERRORS).replace(b"\0", b"?")
    if len(raw) > MAX_ERROR_TEXT:
        raw = raw[:MAX_ERROR_TEXT].decode(FILENAME_ENCODING, "ignore").encode(FILENAME_ENCODING)
    return _HDR.pack(TFTPOpcode.ERROR, code) + raw + b"\0"


def encode_oack(options: Mapping[str, object]) -> bytes:
    """Build an OACK. ``ValueError`` for no options, an empty option name or a
    NUL; ``TypeError`` for a value that is neither text nor an ``int``."""
    if not options:
        raise ValueError("an OACK carries at least one option")
    return _OP.pack(TFTPOpcode.OACK) + _options_bytes(options)


def _strings(body: bytes) -> list:
    """Split NUL-terminated strings, tolerating a missing final NUL."""
    fields = body.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    return [f.decode(FILENAME_ENCODING, _ERRORS) for f in fields]


def _parse_options(fields: list) -> "dict[str, str]":
    options: "dict[str, str]" = {}
    # An odd trailing name with no value is dropped rather than fatal: the
    # request itself is still meaningful without it.
    for i in range(0, len(fields) - 1, 2):
        name = fields[i].lower()
        if name and name not in options:
            options[name] = fields[i + 1]
    return options


def decode(packet: Union[bytes, bytearray, memoryview]) -> TFTPPacket:
    """Parse one packet, whichever kind it is.

    Raises :class:`TFTPDecodeError` on invalid input. Liberal where real peers
    are: a missing final NUL, a dangling option name, an unknown mode and an
    unknown error code are accepted.
    """
    buf = bytes(packet)
    if len(buf) < 2:
        raise TFTPDecodeError("packet shorter than an opcode")
    (op,) = _OP.unpack_from(buf)
    if op in (TFTPOpcode.RRQ, TFTPOpcode.WRQ):
        fields = _strings(buf[2:])
        if len(fields) < 2 or not fields[0]:
            raise TFTPDecodeError("request without a filename and mode")
        return RequestPacket(TFTPOpcode(op), fields[0], fields[1], _parse_options(fields[2:]), buf)
    if op == TFTPOpcode.DATA:
        if len(buf) < 4:
            raise TFTPDecodeError("DATA shorter than its header")
        return DataPacket(_HDR.unpack_from(buf)[1], buf[4:])
    if op == TFTPOpcode.ACK:
        if len(buf) < 4:
            raise TFTPDecodeError("ACK shorter than its header")
        return AckPacket(_HDR.unpack_from(buf)[1])
    if op == TFTPOpcode.ERROR:
        if len(buf) < 4:
            raise TFTPDecodeError("ERROR shorter than its header")
        text = _strings(buf[4:])
        return ErrorPacket(_HDR.unpack_from(buf)[1], text[0] if text else "")
    if op == TFTPOpcode.OACK:
        return OptionAckPacket(_parse_options(_strings(buf[2:])))
    raise TFTPDecodeError("unknown opcode %d" % op)


def _decode_kind(cls: type, data: Union[bytes, bytearray, memoryview]):
    packet = decode(data)
    if not isinstance(packet, cls):
        raise TFTPDecodeError("expected %s, got %s" % (cls.__name__, type(packet).__name__))
    return packet
