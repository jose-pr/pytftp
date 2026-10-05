"""TFTP wire format: packet types and encode/decode.

Covers every packet RFC 1350 and RFC 2347 define: RRQ, WRQ, DATA, ACK, ERROR
and OACK. The transfer engine does not go through :func:`decode` on its hot
path -- it reads the opcode and block number straight out of the receive
buffer -- so the objects here are for requests, errors, option
acknowledgements and for callers inspecting traffic.
"""

from __future__ import annotations

import struct
from typing import Dict, List, Mapping, NamedTuple, Optional, Tuple, Union

from .enums import Opcode

__all__ = [
    "Request",
    "Data",
    "Ack",
    "Error",
    "OptionAck",
    "Packet",
    "MalformedPacket",
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


class MalformedPacket(ValueError):
    """The bytes do not form a valid TFTP packet."""


class Request(NamedTuple):
    """A read (RRQ) or write (WRQ) request.

    ``options`` keys are lower-cased, as RFC 2347 makes option names
    case-insensitive; values are kept as sent, in request order. ``raw`` is
    the datagram exactly as received (empty for a request built in code), so
    a relay can forward it untouched -- option spelling, order, duplicates and
    options this library does not know all survive.
    """

    opcode: Opcode
    filename: str
    mode: str
    options: Dict[str, str]
    raw: bytes = b""

    @property
    def is_read(self) -> bool:
        return self.opcode == Opcode.RRQ

    @property
    def raw_options(self) -> List[Tuple[str, str]]:
        """Every option pair as sent: original case, order and duplicates."""
        if not self.raw:
            return list(self.options.items())
        fields = _strings(self.raw[2:])[2:]
        return [(fields[i], fields[i + 1]) for i in range(0, len(fields) - 1, 2)]

    def encode(self) -> bytes:
        """``raw`` when there is one, else a fresh encoding."""
        return self.raw or encode_request(self.opcode, self.filename, self.mode, self.options)


class Data(NamedTuple):
    block: int
    data: bytes


class Ack(NamedTuple):
    block: int


class Error(NamedTuple):
    code: int
    message: str


class OptionAck(NamedTuple):
    options: Dict[str, str]


Packet = Union[Request, Data, Ack, Error, OptionAck]


def _text(value: str) -> bytes:
    raw = value.encode(FILENAME_ENCODING, _ERRORS)
    if b"\0" in raw:
        raise ValueError("NUL is not allowed in TFTP strings: %r" % value)
    return raw


def _options_bytes(options: Optional[Mapping[str, object]]) -> bytes:
    if not options:
        return b""
    parts = []
    for name, value in options.items():
        parts.append(_text(str(name)))
        parts.append(_text(str(value)))
    return b"\0".join(parts) + b"\0"


def encode_request(
    opcode: int,
    filename: str,
    mode: str = "octet",
    options: Optional[Mapping[str, object]] = None,
) -> bytes:
    """Build an RRQ or WRQ. Option values are sent as ``str(value)``."""
    if opcode not in (Opcode.RRQ, Opcode.WRQ):
        raise ValueError("a request opcode is RRQ or WRQ, not %r" % (opcode,))
    return _OP.pack(opcode) + _text(filename) + b"\0" + _text(mode) + b"\0" + _options_bytes(options)


def encode_data(block: int, data: bytes) -> bytes:
    return _HDR.pack(Opcode.DATA, block) + bytes(data)


def encode_ack(block: int) -> bytes:
    return _HDR.pack(Opcode.ACK, block)


#: Most octets of message text an ERROR carries.
MAX_ERROR_TEXT = 512


def encode_error(code: int, message: str = "") -> bytes:
    """Build an ERROR; it never raises, whatever it is given.

    Message text is UTF-8 encoded, a NUL in it becomes ``?`` and it is cut to
    ``MAX_ERROR_TEXT`` octets on a character boundary. A code that is not an
    ``int`` in 0..65535 is sent as 0.
    """
    if isinstance(code, bool) or not isinstance(code, int) or not 0 <= code <= 65535:
        code = 0
    if not isinstance(message, str):
        message = str(message)
    raw = message.encode(FILENAME_ENCODING, _ERRORS).replace(b"\0", b"?")
    if len(raw) > MAX_ERROR_TEXT:
        raw = raw[:MAX_ERROR_TEXT].decode(FILENAME_ENCODING, "ignore").encode(FILENAME_ENCODING)
    return _HDR.pack(Opcode.ERROR, code) + raw + b"\0"


def encode_oack(options: Mapping[str, object]) -> bytes:
    return _OP.pack(Opcode.OACK) + _options_bytes(options)


def _strings(body: bytes) -> list:
    """Split NUL-terminated strings, tolerating a missing final NUL."""
    fields = body.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    return [f.decode(FILENAME_ENCODING, _ERRORS) for f in fields]


def _parse_options(fields: list) -> Dict[str, str]:
    options: Dict[str, str] = {}
    # An odd trailing name with no value is dropped rather than fatal: the
    # request itself is still meaningful without it.
    for i in range(0, len(fields) - 1, 2):
        name = fields[i].lower()
        if name and name not in options:
            options[name] = fields[i + 1]
    return options


def decode(packet: Union[bytes, bytearray, memoryview]) -> Packet:
    """Parse one packet. Raises :class:`MalformedPacket` on invalid input."""
    buf = bytes(packet)
    if len(buf) < 2:
        raise MalformedPacket("packet shorter than an opcode")
    (op,) = _OP.unpack_from(buf)
    if op in (Opcode.RRQ, Opcode.WRQ):
        fields = _strings(buf[2:])
        if len(fields) < 2 or not fields[0]:
            raise MalformedPacket("request without a filename and mode")
        return Request(Opcode(op), fields[0], fields[1].lower(), _parse_options(fields[2:]), buf)
    if op == Opcode.DATA:
        if len(buf) < 4:
            raise MalformedPacket("DATA shorter than its header")
        return Data(_HDR.unpack_from(buf)[1], buf[4:])
    if op == Opcode.ACK:
        if len(buf) < 4:
            raise MalformedPacket("ACK shorter than its header")
        return Ack(_HDR.unpack_from(buf)[1])
    if op == Opcode.ERROR:
        if len(buf) < 4:
            raise MalformedPacket("ERROR shorter than its header")
        code = _HDR.unpack_from(buf)[1]
        text = _strings(buf[4:])
        return Error(code, text[0] if text else "")
    if op == Opcode.OACK:
        return OptionAck(_parse_options(_strings(buf[2:])))
    raise MalformedPacket("unknown opcode %d" % op)
