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
    The source is read with ``readinto`` when it has one, so a non-blocking
    source's ``WouldBlock`` passes through (after any bytes already
    produced) instead of a blocking ``read`` stalling the caller.
    """

    def __init__(self, raw: BinaryIO) -> None:
        self._raw = raw
        self._pending = b""
        self._eof = False
        self._readinto = getattr(raw, "readinto", None)

    def _read_raw(self, n: int) -> bytes:
        if self._readinto is None:
            return self._raw.read(n)
        chunk = bytearray(n)
        got = self._readinto(chunk) or 0
        return bytes(chunk[:got])

    def readinto(self, buffer) -> int:
        view = memoryview(buffer)
        want = len(view)
        filled = 0
        while filled < want:
            if not self._pending:
                if self._eof:
                    break
                try:
                    chunk = self._read_raw(max(_CHUNK, want))
                except BlockingIOError:
                    if filled:
                        return filled  # deliver what we have; the next call re-raises
                    raise
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

    copies_writes = True  # write() copies what it is given, so the engine hands it a reused buffer

    def __init__(self, raw: BinaryIO) -> None:
        self._raw = raw
        self._held_cr = False

    def write(self, data) -> int:
        size = len(data)
        chunk = bytes(data)
        if self._held_cr:
            chunk = b"\r" + chunk
        held = chunk.endswith(b"\r")
        if held:
            chunk = chunk[:-1]
        if chunk:
            # May raise (WouldBlock from a full sink): the state below is only
            # updated once the write went through, so a retry is exact.
            self._raw.write(decode(chunk))
        self._held_cr = held
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
