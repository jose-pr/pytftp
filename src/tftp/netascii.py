"""Streaming netascii translation (RFC 764 as used by RFC 1350).

On the wire, a line ends in CR LF and a bare CR is sent as CR NUL. Locally,
this module uses LF for a line end and leaves every other byte alone, so the
translation is lossless both ways: a local ``\\r\\n`` travels as
``\\r\\0\\r\\n`` and comes back as ``\\r\\n``.

Both directions are incremental, so a CR that falls on a block boundary is
handled correctly.
"""

from __future__ import annotations

from typing import BinaryIO, Optional

__all__ = ["NetasciiReader", "NetasciiWriter", "encode", "decode", "encoded_size"]

_CHUNK = 65536


def encode(data: bytes) -> bytes:
    """Translate local bytes to netascii in one go."""
    # CR first: the CRs that LF translation introduces must not gain a NUL.
    return data.replace(b"\r", b"\r\0").replace(b"\n", b"\r\n")


def decode(data: bytes) -> bytes:
    """Translate a complete netascii byte string back to local bytes."""
    # On a well-formed stream every CR is followed by LF or NUL, so a CR LF
    # pair can only be an encoded line end.
    return data.replace(b"\r\n", b"\n").replace(b"\r\0", b"\r")


def encoded_size(fileobj: BinaryIO) -> Optional[int]:
    """Netascii size of a seekable binary file, leaving its position alone.

    Every CR and LF grows by one byte. Returns ``None`` when the file cannot
    be scanned (not seekable).
    """
    try:
        start = fileobj.tell()
    except (AttributeError, OSError, ValueError):
        return None
    total = 0
    try:
        while True:
            chunk = fileobj.read(_CHUNK)
            if not chunk:
                break
            total += len(chunk) + chunk.count(b"\r") + chunk.count(b"\n")
    finally:
        fileobj.seek(start)
    return total


class NetasciiReader:
    """A binary reader whose bytes come out netascii-encoded.

    Only ``readinto`` and ``close`` are provided -- what a sender needs.
    """

    def __init__(self, raw: BinaryIO) -> None:
        self._raw = raw
        self._pending = b""
        self._eof = False

    def readinto(self, buffer) -> int:
        view = memoryview(buffer)
        want = len(view)
        filled = 0
        while filled < want:
            if not self._pending:
                if self._eof:
                    break
                chunk = self._raw.read(max(_CHUNK, want))
                if not chunk:
                    self._eof = True
                    break
                self._pending = encode(chunk)
            take = min(want - filled, len(self._pending))
            view[filled : filled + take] = self._pending[:take]
            self._pending = self._pending[take:]
            filled += take
        return filled

    def close(self) -> None:
        self._raw.close()


class NetasciiWriter:
    """A binary writer that decodes netascii before writing it through.

    A trailing CR is held back until the next write shows what follows it;
    :meth:`close` flushes it as a plain CR.
    """

    def __init__(self, raw: BinaryIO) -> None:
        self._raw = raw
        self._held_cr = False

    def write(self, data) -> int:
        size = len(data)
        chunk = bytes(data)
        if self._held_cr:
            chunk = b"\r" + chunk
            self._held_cr = False
        if chunk.endswith(b"\r"):
            chunk = chunk[:-1]
            self._held_cr = True
        if chunk:
            self._raw.write(decode(chunk))
        return size

    def flush(self) -> None:
        if self._held_cr:
            self._raw.write(b"\r")
            self._held_cr = False
        flush = getattr(self._raw, "flush", None)
        if flush is not None:
            flush()

    def close(self) -> None:
        self.flush()
        self._raw.close()

    def abort(self) -> None:
        abort = getattr(self._raw, "abort", None)
        if abort is not None:
            abort()
        else:
            self._raw.close()
