import io
import random

import pytest

from tftp import NetasciiReader, NetasciiWriter, WouldBlock
from tftp.netascii import decode, encode, encoded_size

SAMPLES = [
    b"",
    b"plain",
    b"a\nb\n",
    b"dos\r\nline\r\n",
    b"bare\rcr",
    b"\r",
    b"\n",
    b"\r\r\n\n\r",
    b"\0\r\0\n\0",
]


@pytest.mark.parametrize("data", SAMPLES)
def test_roundtrip(data):
    assert decode(encode(data)) == data


def test_encoding_rules():
    assert encode(b"a\nb") == b"a\r\nb"
    assert encode(b"a\rb") == b"a\r\0b"
    assert encode(b"a\r\nb") == b"a\r\0\r\nb"


@pytest.mark.parametrize("data", SAMPLES + [bytes(random.Random(1).getrandbits(8) for _ in range(5000))])
def test_reader_fills_exact_blocks(data):
    reader = NetasciiReader(io.BytesIO(data))
    out = bytearray()
    buf = bytearray(7)
    while True:
        n = reader.readinto(buf)
        out += buf[:n]
        if n < len(buf):
            break
    assert bytes(out) == encode(data)


@pytest.mark.parametrize("split", range(1, 12))
def test_writer_handles_cr_on_any_boundary(split):
    data = b"x\ry\r\nz\r\r\n\rw\r"
    wire = encode(data)
    sink = io.BytesIO()
    writer = NetasciiWriter(sink)
    for i in range(0, len(wire), split):
        writer.write(memoryview(wire)[i : i + split])
    writer.flush()
    assert sink.getvalue() == data


def test_encoded_size_keeps_position():
    data = b"a\nb\rc\r\n" * 1000
    stream = io.BytesIO(data)
    stream.seek(3)
    assert encoded_size(stream) == len(encode(data[3:]))
    assert stream.tell() == 3
    assert encoded_size(object()) is None


SEEDS = range(40)


@pytest.mark.parametrize("seed", SEEDS)
def test_any_octets_survive_encode_decode_the_reader_and_the_writer_at_any_split(seed):
    rng = random.Random(seed)
    alphabet = [b"\r", b"\n", b"\0", b"a", b"\r\n", b"\r\0", b"\xff"]
    data = b"".join(rng.choice(alphabet) for _ in range(rng.randint(0, 300)))
    where = "seed %d" % seed
    assert decode(encode(data)) == data, where
    reader = NetasciiReader(io.BytesIO(data))
    block = bytearray(rng.randint(1, 40))
    wire = bytearray()
    while True:
        n = reader.readinto(block)
        wire += block[:n]
        if n < len(block):
            break
    assert bytes(wire) == encode(data), where
    sink = io.BytesIO()
    writer = NetasciiWriter(sink)
    position = 0
    while position < len(wire):
        step = rng.randint(1, 40)
        writer.write(memoryview(wire)[position : position + step])
        position += step
    writer.flush()
    assert sink.getvalue() == data, where


class _Raw(io.RawIOBase):
    """A raw stream that takes ``take`` octets a call, and has nothing ready
    for the calls whose numbers are in ``busy``."""

    def __init__(self, take, busy=()):
        self.taken = bytearray()
        self._take = take
        self._busy = set(busy)
        self._calls = 0

    def writable(self):
        return True

    def write(self, data):
        self._calls += 1
        if self._calls in self._busy:
            return None
        part = bytes(data)[: self._take]
        self.taken += part
        return len(part)


@pytest.mark.parametrize("take", [1, 7, 100])
def test_the_writer_completes_a_short_write_of_a_raw_stream(take):
    data = b"line one\nline two\r\nbare\rend\r" * 9
    wire = encode(data)
    raw = _Raw(take)
    writer = NetasciiWriter(raw)
    for start in range(0, len(wire), 50):
        assert writer.write(wire[start : start + 50]) == len(wire[start : start + 50])
    writer.flush()
    assert bytes(raw.taken) == data


def test_a_raw_stream_that_takes_nothing_fails_the_write():
    writer = NetasciiWriter(_Raw(0))
    with pytest.raises(OSError, match="took none"):
        writer.write(b"abc")


def test_a_raw_stream_with_nothing_ready_holds_the_block_and_a_retry_is_exact():
    raw = _Raw(100, busy={2})
    writer = NetasciiWriter(raw)
    writer.write(b"a\r")
    with pytest.raises(WouldBlock):
        writer.write(b"\nb\r")
    writer.write(b"\nb\r")
    writer.flush()
    assert bytes(raw.taken) == b"a\nb\r"
