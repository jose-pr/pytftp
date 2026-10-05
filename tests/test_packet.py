import pytest

import tftp
from tftp import ErrorCode, Opcode


def test_request_roundtrip_with_options():
    raw = tftp.encode_request(Opcode.RRQ, "boot/pxelinux.0", "octet", {"blksize": 1428, "tsize": 0})
    assert raw == b"\x00\x01boot/pxelinux.0\x00octet\x00blksize\x001428\x00tsize\x000\x00"
    packet = tftp.decode(raw)
    assert packet == tftp.Request(
        Opcode.RRQ, "boot/pxelinux.0", "octet", {"blksize": "1428", "tsize": "0"}, raw
    )
    assert packet.is_read
    assert packet.encode() == raw


def test_request_keeps_raw_bytes_and_original_options():
    raw = b"\x00\x01f\x00OcTeT\x00BlkSize\x00100\x00blksize\x00200\x00X-Vendor\x00v\x00"
    packet = tftp.decode(raw)
    assert packet.raw == raw and packet.encode() == raw
    assert packet.mode == "octet" and packet.options == {"blksize": "100", "x-vendor": "v"}
    assert packet.raw_options == [("BlkSize", "100"), ("blksize", "200"), ("X-Vendor", "v")]
    built = tftp.Request(Opcode.WRQ, "f", "octet", {"tsize": "5"})
    assert built.raw == b"" and built.raw_options == [("tsize", "5")]
    assert tftp.decode(built.encode()).options == {"tsize": "5"}


def test_request_option_names_are_case_insensitive_and_first_wins():
    raw = b"\x00\x02f\x00NetASCII\x00BLKSIZE\x00100\x00blksize\x00200\x00"
    packet = tftp.decode(raw)
    assert packet.mode == "netascii"
    assert packet.options == {"blksize": "100"}
    assert not packet.is_read


def test_request_tolerates_missing_final_nul_and_dangling_option():
    packet = tftp.decode(b"\x00\x01f\x00octet\x00blksize")
    assert packet.filename == "f" and packet.options == {}


def test_request_filename_bytes_roundtrip():
    name = b"caf\xe9-\xff".decode("utf-8", "surrogateescape")
    raw = tftp.encode_request(Opcode.WRQ, name)
    assert tftp.decode(raw).filename == name
    assert raw[2:8] == b"caf\xe9-\xff"


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\x00",
        b"\x00\x01\x00octet\x00",
        b"\x00\x01name",
        b"\x00\x03\x00",
        b"\x00\x04\x00",
        b"\x00\x09xx",
    ],
)
def test_malformed(raw):
    with pytest.raises(tftp.TFTPDecodeError):
        tftp.decode(raw)


def test_data_ack_error_oack():
    assert tftp.decode(tftp.encode_data(7, b"abc")) == tftp.Data(7, b"abc")
    assert tftp.decode(tftp.encode_ack(65535)) == tftp.Ack(65535)
    err = tftp.decode(tftp.encode_error(ErrorCode.FILE_NOT_FOUND, "nope"))
    assert err == tftp.Error(1, "nope")
    assert tftp.decode(b"\x00\x05\x00\x02") == tftp.Error(2, "")
    assert tftp.decode(tftp.encode_oack({"blksize": 9})) == tftp.OptionAck({"blksize": "9"})


def test_nul_in_strings_is_refused():
    with pytest.raises(ValueError):
        tftp.encode_request(Opcode.RRQ, "a\0b")
    with pytest.raises(ValueError):
        tftp.encode_request(Opcode.DATA, "a")


def test_errors_map_os_errors():
    from tftp.exceptions import error_for_exception

    assert error_for_exception(FileNotFoundError(2, "x")).code == ErrorCode.FILE_NOT_FOUND
    assert error_for_exception(PermissionError(13, "x")).code == ErrorCode.ACCESS_VIOLATION
    assert error_for_exception(FileExistsError(17, "x")).code == ErrorCode.FILE_EXISTS
    assert error_for_exception(OSError(28, "No space left")).code == ErrorCode.DISK_FULL
    assert error_for_exception(RuntimeError("boom")).code == ErrorCode.NOT_DEFINED
    # The OS text could leak server paths, so it never becomes the message.
    assert "secret" not in error_for_exception(FileNotFoundError(2, "/secret/path")).message
