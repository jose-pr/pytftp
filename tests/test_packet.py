import pytest

import tftp
from tftp import TFTPErrorCode, TFTPOpcode


def test_request_roundtrip_with_options():
    raw = tftp.packet.encode_request(
        TFTPOpcode.RRQ, "boot/pxelinux.0", mode="octet", options={"blksize": 1428, "tsize": 0}
    )
    assert raw == b"\x00\x01boot/pxelinux.0\x00octet\x00blksize\x001428\x00tsize\x000\x00"
    packet = tftp.decode(raw)
    assert packet == tftp.RequestPacket(
        TFTPOpcode.RRQ, "boot/pxelinux.0", "octet", {"blksize": "1428", "tsize": "0"}, raw
    )
    assert packet.is_read
    assert packet.encode() == raw


def test_request_keeps_raw_bytes_and_original_options():
    raw = b"\x00\x01f\x00OcTeT\x00BlkSize\x00100\x00blksize\x00200\x00X-Vendor\x00v\x00"
    packet = tftp.decode(raw)
    assert packet.raw == raw and packet.encode() == raw
    assert packet.mode == "octet" and packet.options == {"blksize": "100", "x-vendor": "v"}
    assert packet.raw_options == [("BlkSize", "100"), ("blksize", "200"), ("X-Vendor", "v")]
    built = tftp.RequestPacket(TFTPOpcode.WRQ, "f", "octet", {"tsize": "5"})
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
    raw = tftp.packet.encode_request(TFTPOpcode.WRQ, name)
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
    assert tftp.decode(tftp.packet.encode_data(7, b"abc")) == tftp.DataPacket(7, b"abc")
    assert tftp.decode(tftp.packet.encode_ack(65535)) == tftp.AckPacket(65535)
    err = tftp.decode(tftp.packet.encode_error(TFTPErrorCode.FILE_NOT_FOUND, "nope"))
    assert err == tftp.ErrorPacket(1, "nope")
    assert tftp.decode(b"\x00\x05\x00\x02") == tftp.ErrorPacket(2, "")
    assert tftp.decode(tftp.packet.encode_oack({"blksize": 9})) == tftp.OptionAckPacket({"blksize": "9"})


def test_nul_in_strings_is_refused():
    with pytest.raises(ValueError):
        tftp.packet.encode_request(TFTPOpcode.RRQ, "a\0b")
    with pytest.raises(ValueError):
        tftp.packet.encode_request(TFTPOpcode.DATA, "a")


# -- wire vectors: RFC 1350 section 5 and RFC 2347 section 3 -----------------------

WIRE = [
    # opcode 01, filename, 0, mode, 0
    (tftp.RequestPacket(TFTPOpcode.RRQ, "rfc1350.txt", "netascii"), b"\x00\x01rfc1350.txt\x00netascii\x00"),
    (tftp.RequestPacket(TFTPOpcode.WRQ, "a", "octet"), b"\x00\x02a\x00octet\x00"),
    (tftp.RequestPacket(TFTPOpcode.RRQ, "a", "mail"), b"\x00\x01a\x00mail\x00"),
    # RFC 2347: the options follow the mode as name, 0, value, 0
    (
        tftp.RequestPacket(TFTPOpcode.RRQ, "f", "octet", {"blksize": "1024", "tsize": "0"}),
        b"\x00\x01f\x00octet\x00blksize\x001024\x00tsize\x000\x00",
    ),
    (tftp.DataPacket(1, b"abc"), b"\x00\x03\x00\x01abc"),
    (tftp.DataPacket(65535, b""), b"\x00\x03\xff\xff"),
    (tftp.AckPacket(0), b"\x00\x04\x00\x00"),
    (tftp.AckPacket(5), b"\x00\x04\x00\x05"),
    (tftp.ErrorPacket(1, "File not found."), b"\x00\x05\x00\x01File not found.\x00"),
    (tftp.ErrorPacket(8, ""), b"\x00\x05\x00\x08\x00"),
    (tftp.OptionAckPacket({"blksize": "1024"}), b"\x00\x06blksize\x001024\x00"),
]


@pytest.mark.parametrize("packet,wire", WIRE, ids=[repr(p)[:40] for p, _ in WIRE])
def test_the_wire_vectors(packet, wire):
    cls = type(packet)
    assert packet.encode() == wire
    assert bytes(packet) == wire
    assert cls.decode(wire) == packet
    assert tftp.decode(wire) == packet
    assert type(cls.decode(bytearray(wire))) is cls and cls.decode(memoryview(wire)) == packet


def test_bytes_of_a_packet_is_its_wire_form():
    assert bytes(tftp.AckPacket(5)) == b"\x00\x04\x00\x05"
    assert bytes(tftp.DataPacket(1, b"ab")) == b"\x00\x03\x00\x01ab"


@pytest.mark.parametrize("packet,wire", WIRE, ids=[repr(p)[:40] for p, _ in WIRE])
def test_a_packet_decoded_as_another_kind_is_refused(packet, wire):
    for cls in (
        tftp.RequestPacket,
        tftp.DataPacket,
        tftp.AckPacket,
        tftp.ErrorPacket,
        tftp.OptionAckPacket,
    ):
        if not isinstance(packet, cls):
            with pytest.raises(tftp.TFTPDecodeError):
                cls.decode(wire)


@pytest.mark.parametrize("packet,wire", WIRE, ids=[repr(p)[:40] for p, _ in WIRE])
def test_a_packet_is_a_value(packet, wire):
    import copy
    import pickle

    same = tftp.decode(wire)
    assert packet == same and hash(packet) == hash(same) and len({packet, same}) == 1
    assert packet.__eq__(tuple(packet.__dict__.values())) is NotImplemented
    assert packet != tuple(packet.__dict__.values()) and packet != wire and packet != repr(packet)
    with pytest.raises(AttributeError):
        packet.block = 1  # type: ignore[attr-defined]
    for clone in (copy.copy(packet), copy.deepcopy(packet), pickle.loads(pickle.dumps(packet))):
        assert clone == packet and type(clone) is type(packet) and hash(clone) == hash(packet)
    namespace = {name: getattr(tftp, name) for name in dir(tftp) if name[0].isupper()}
    assert eval(repr(packet), namespace) == packet


def test_packets_of_different_kinds_with_the_same_fields_differ():
    assert tftp.AckPacket(5) != tftp.DataPacket(5, b"")
    assert tftp.AckPacket(5) != (5,) and (5,) != tftp.AckPacket(5)
    assert tftp.AckPacket(5) not in {(5,)}
    assert tftp.ErrorPacket(1, "x") != (1, "x")
    with pytest.raises(TypeError):
        tuple(tftp.AckPacket(5))
    with pytest.raises(TypeError):
        tftp.AckPacket(5)[0]  # type: ignore[index]


def test_a_request_equals_by_its_fields_not_its_raw_bytes():
    built = tftp.RequestPacket(TFTPOpcode.RRQ, "f", "OCTET", {"BlkSize": 8})
    decoded = tftp.decode(b"\x00\x01f\x00octet\x00blksize\x008\x00")
    assert built == decoded and hash(built) == hash(decoded)
    assert built.options == {"blksize": "8"}
    assert built != tftp.RequestPacket(TFTPOpcode.WRQ, "f", "octet", {"blksize": "8"})
    assert built != tftp.RequestPacket(TFTPOpcode.RRQ, "f", "octet", {"blksize": "9"})


def test_the_options_of_a_packet_are_read_only():
    request = tftp.decode(b"\x00\x01f\x00octet\x00blksize\x008\x00")
    oack = tftp.decode(b"\x00\x06blksize\x008\x00")
    for options in (request.options, oack.options):
        with pytest.raises(TypeError):
            options["x"] = "1"  # type: ignore[index]
    source = {"blksize": "8"}
    packet = tftp.OptionAckPacket(source)
    source["tsize"] = "0"
    assert packet.options == {"blksize": "8"}


def test_an_error_code_is_a_member_that_carries_an_unknown_number():
    import copy
    import pickle

    known = tftp.decode(b"\x00\x05\x00\x02x\x00").code
    assert known is TFTPErrorCode.ACCESS_VIOLATION
    unknown = tftp.decode(b"\x00\x05\x01\x02x\x00").code
    assert isinstance(unknown, TFTPErrorCode) and unknown == 258 and int(unknown) == 258
    assert unknown.name == "CODE_258" and unknown not in list(TFTPErrorCode)
    assert tftp.ErrorPacket(258, "x").encode() == b"\x00\x05\x01\x02x\x00"  # forwarded as received
    assert tftp.decode(b"\x00\x05\xff\xffx\x00").code == 65535
    for clone in (copy.copy(unknown), copy.deepcopy(unknown), pickle.loads(pickle.dumps(unknown))):
        assert clone == 258 and isinstance(clone, TFTPErrorCode)
    for bad in (-1, 65536):
        with pytest.raises(ValueError):
            TFTPErrorCode(bad)
    assert tftp.TFTPError(258, "x").code == 258 and "CODE_258" in str(tftp.TFTPError(258, "x"))


@pytest.mark.parametrize(
    "build",
    [
        lambda: tftp.AckPacket(-1),
        lambda: tftp.AckPacket(65536),
        lambda: tftp.DataPacket(70000, b""),
        lambda: tftp.ErrorPacket(70000, "x"),
        lambda: tftp.ErrorPacket(-1, "x"),
        lambda: tftp.RequestPacket(TFTPOpcode.DATA, "f", "octet"),
        lambda: tftp.RequestPacket(TFTPOpcode.RRQ, "", "octet"),
    ],
)
def test_a_packet_constructor_refuses_a_value_the_wire_cannot_carry(build):
    with pytest.raises(ValueError):
        build()


@pytest.mark.parametrize(
    "build",
    [
        lambda: tftp.AckPacket("1"),
        lambda: tftp.AckPacket(True),
        lambda: tftp.AckPacket(1.0),
        lambda: tftp.DataPacket(1, "text"),
        lambda: tftp.DataPacket(1, None),
        lambda: tftp.ErrorPacket("1", "x"),
        lambda: tftp.ErrorPacket(1, b"x"),
        lambda: tftp.RequestPacket(TFTPOpcode.RRQ, b"f", "octet"),
        lambda: tftp.OptionAckPacket({"blksize": None}),
        lambda: tftp.OptionAckPacket({"windowsize": True}),
        lambda: tftp.OptionAckPacket({"windowsize": 1.5}),
    ],
)
def test_a_packet_constructor_refuses_a_wrong_type(build):
    with pytest.raises(TypeError):
        build()


# -- encode is strict ---------------------------------------------------------------


def _every_encoder_refuses(cases, error):
    for call in cases:
        with pytest.raises(error):
            call()


def test_encode_request_refuses_what_decode_refuses_and_what_rfc_2347_bounds():
    long_name = "n" * 600
    _every_encoder_refuses(
        [
            lambda: tftp.packet.encode_request(TFTPOpcode.RRQ, ""),
            lambda: tftp.packet.encode_request(TFTPOpcode.RRQ, "f", mode="bogus-mode"),
            lambda: tftp.packet.encode_request(TFTPOpcode.RRQ, "f", mode=""),
            lambda: tftp.packet.encode_request(TFTPOpcode.RRQ, "f", mode="oct\0et"),
            lambda: tftp.packet.encode_request(TFTPOpcode.RRQ, long_name),
            lambda: tftp.packet.encode_request(TFTPOpcode.RRQ, "f", mode="octet", options={"": "1"}),
            lambda: tftp.packet.encode_request(TFTPOpcode.RRQ, "f", mode="octet", options={"a\0": "1"}),
            lambda: tftp.packet.encode_request(TFTPOpcode.RRQ, "f", mode="octet", options={"a": "1\0"}),
            lambda: tftp.packet.encode_request(
                TFTPOpcode.RRQ, "f", mode="octet", options={"x%d" % i: "y" * 40 for i in range(20)}
            ),
            lambda: tftp.packet.encode_request(TFTPOpcode.DATA, "f"),
            lambda: tftp.RequestPacket(TFTPOpcode.RRQ, long_name, "octet").encode(),
            lambda: tftp.RequestPacket(TFTPOpcode.RRQ, "f", "bogus").encode(),
        ],
        ValueError,
    )
    _every_encoder_refuses(
        [
            lambda: tftp.packet.encode_request(TFTPOpcode.RRQ, b"f"),
            lambda: tftp.packet.encode_request(TFTPOpcode.RRQ, "f", mode=None),
            lambda: tftp.packet.encode_request(TFTPOpcode.RRQ, "f", mode="octet", options={"a": None}),
            lambda: tftp.packet.encode_request(TFTPOpcode.RRQ, "f", mode="octet", options={"a": True}),
            lambda: tftp.packet.encode_request(TFTPOpcode.RRQ, "f", mode="octet", options={"a": 1.5}),
            lambda: tftp.packet.encode_request(TFTPOpcode.RRQ, "f", mode="octet", options={1: "a"}),
        ],
        TypeError,
    )


def test_encode_request_accepts_the_largest_request_and_the_rfc_1350_modes():
    name = "n" * (512 - 2 - len("octet") - 2)
    assert len(tftp.packet.encode_request(TFTPOpcode.RRQ, name)) == 512
    with pytest.raises(ValueError):
        tftp.packet.encode_request(TFTPOpcode.RRQ, name + "n")
    for mode in ("netascii", "OCTET", "Mail"):
        assert tftp.decode(tftp.packet.encode_request(TFTPOpcode.RRQ, "f", mode=mode)).mode == mode.lower()
    assert tftp.packet.encode_request(
        TFTPOpcode.RRQ, "f", mode="octet", options={"blksize": 8, "x": "y"}
    ) == (b"\x00\x01f\x00octet\x00blksize\x008\x00x\x00y\x00")


def test_encode_oack_refuses_what_is_not_an_option_list():
    _every_encoder_refuses(
        [
            lambda: tftp.packet.encode_oack({}),
            lambda: tftp.packet.encode_oack({"": "x"}),
            lambda: tftp.packet.encode_oack({"a\0": "x"}),
            lambda: tftp.OptionAckPacket({}).encode(),
        ],
        ValueError,
    )
    _every_encoder_refuses(
        [
            lambda: tftp.packet.encode_oack({"tsize": None}),
            lambda: tftp.packet.encode_oack({"ok": True}),
            lambda: tftp.packet.encode_oack({"ok": 1.0}),
        ],
        TypeError,
    )


@pytest.mark.parametrize("block", [-1, 65536, 70000, 2**64])
def test_encode_data_and_ack_refuse_a_block_the_wire_cannot_carry(block):
    with pytest.raises(ValueError):
        tftp.packet.encode_data(block, b"")
    with pytest.raises(ValueError):
        tftp.packet.encode_ack(block)


@pytest.mark.parametrize("block", ["1", None, 1.0, True, b"\x00"])
def test_encode_data_and_ack_refuse_a_block_that_is_not_an_int(block):
    with pytest.raises(TypeError):
        tftp.packet.encode_data(block, b"")
    with pytest.raises(TypeError):
        tftp.packet.encode_ack(block)


def test_encode_data_and_ack_take_the_block_limits():
    assert (
        tftp.packet.encode_ack(0) == b"\x00\x04\x00\x00"
        and tftp.packet.encode_ack(65535) == b"\x00\x04\xff\xff"
    )
    assert tftp.packet.encode_data(65535, bytearray(b"x")) == b"\x00\x03\xff\xffx"


def test_encode_error_is_strict():
    _every_encoder_refuses(
        [
            lambda: tftp.packet.encode_error(70000, "x"),
            lambda: tftp.packet.encode_error(-1, "x"),
            lambda: tftp.packet.encode_error(1, "a\0b"),
            lambda: tftp.packet.encode_error(1, "x" * 513),
        ],
        ValueError,
    )
    _every_encoder_refuses(
        [
            lambda: tftp.packet.encode_error(None, "x"),
            lambda: tftp.packet.encode_error(True, "x"),
            lambda: tftp.packet.encode_error("1", "x"),
            lambda: tftp.packet.encode_error(1, b"x"),
            lambda: tftp.packet.encode_error(1, None),
        ],
        TypeError,
    )
    assert tftp.packet.encode_error(65535, "x" * 512) == b"\x00\x05\xff\xff" + b"x" * 512 + b"\x00"
    assert tftp.packet.encode_error(TFTPErrorCode.FILE_NOT_FOUND) == b"\x00\x05\x00\x01\x00"


def test_no_encoder_leaks_a_struct_error():
    import struct

    for call in (
        lambda: tftp.packet.encode_data(70000, b""),
        lambda: tftp.packet.encode_ack(-1),
        lambda: tftp.packet.encode_error(70000, "x"),
        lambda: tftp.packet.encode_data("a", b""),
    ):
        try:
            call()
        except struct.error:  # pragma: no cover - the failure being tested for
            pytest.fail("struct.error escaped an encoder")
        except (ValueError, TypeError):
            pass


def test_decode_then_encode_is_the_identity_for_what_encode_emits():
    import random

    rng = random.Random(1350)
    for _ in range(300):
        name = "".join(rng.choice("abc/._-\xe9 ") for _ in range(rng.randint(1, 40)))
        options = {"o%d" % i: str(rng.randint(0, 10**6)) for i in range(rng.randint(0, 5))}
        built = tftp.RequestPacket(rng.choice([TFTPOpcode.RRQ, TFTPOpcode.WRQ]), name, "octet", options)
        assert tftp.decode(built.encode()) == built
        block = rng.randint(0, 65535)
        data = bytes(rng.randint(0, 255) for _ in range(rng.randint(0, 20)))
        for packet in (
            tftp.DataPacket(block, data),
            tftp.AckPacket(block),
            tftp.ErrorPacket(rng.randint(0, 65535), name),
            tftp.OptionAckPacket(options or {"a": "b"}),
        ):
            assert tftp.decode(packet.encode()) == packet and bytes(packet) == packet.encode()


def test_errors_map_os_errors():
    from tftp.exceptions import TFTPError

    assert TFTPError.from_exception(FileNotFoundError(2, "x")).code == TFTPErrorCode.FILE_NOT_FOUND
    assert TFTPError.from_exception(PermissionError(13, "x")).code == TFTPErrorCode.ACCESS_VIOLATION
    assert TFTPError.from_exception(FileExistsError(17, "x")).code == TFTPErrorCode.FILE_EXISTS
    assert TFTPError.from_exception(OSError(28, "No space left")).code == TFTPErrorCode.DISK_FULL
    assert TFTPError.from_exception(RuntimeError("boom")).code == TFTPErrorCode.NOT_DEFINED
    # The OS text could leak server paths, so it never becomes the message.
    assert "secret" not in TFTPError.from_exception(FileNotFoundError(2, "/secret/path")).message
