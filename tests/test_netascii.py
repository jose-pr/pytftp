import io
import random

import pytest

from tftp import NetasciiReader, NetasciiWriter
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
