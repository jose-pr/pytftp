"""The TFTP dissector: pktcap's contract, what it reads, where it registers, and the record it makes."""

from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import pickle
import subprocess
import sys

import pktcap
import pytest

import tftp.capture
from tftp import TFTPOpcode
from tftp.capture import TFTPLayer, dissect_tftp, register_tftp_dissector
from tftp.packet import encode_ack, encode_data, encode_error, encode_oack, encode_request

_CASES = pathlib.Path(__file__).resolve().parent / "capture_cases"

#: One well-formed packet of every opcode, and what a reader of the wire sees in it.
SAMPLES = {
    "RRQ": (
        encode_request(TFTPOpcode.RRQ, "boot/ipxe.efi", mode="octet", options={"blksize": 1024, "tsize": 0}),
        TFTPLayer(
            "RRQ", filename="boot/ipxe.efi", mode="octet", options=(("blksize", "1024"), ("tsize", "0"))
        ),
        b"",
    ),
    "WRQ": (
        encode_request(TFTPOpcode.WRQ, "up.txt", mode="netascii"),
        TFTPLayer("WRQ", filename="up.txt", mode="netascii", options=()),
        b"",
    ),
    "DATA": (encode_data(7, b"payload octets"), TFTPLayer("DATA", block=7), b"payload octets"),
    "ACK": (encode_ack(65535), TFTPLayer("ACK", block=65535), b""),
    "ERROR": (encode_error(1, "file not found"), TFTPLayer("ERROR", code=1, message="file not found"), b""),
    "OACK": (
        encode_oack({"blksize": 512, "windowsize": 4}),
        TFTPLayer("OACK", options=(("blksize", "512"), ("windowsize", "4"))),
        b"",
    ),
}


def test_the_dissector_keeps_pktcaps_contract_on_every_opcode():
    pktcap.check_dissector(dissect_tftp, [packet for packet, _, _ in SAMPLES.values()])


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_each_packet_is_read_as_the_layer_and_payload_the_wire_holds(name):
    packet, layer, payload = SAMPLES[name]
    dissected = dissect_tftp(packet)
    assert (dissected.layer, dissected.payload) == (layer, payload)
    assert dissected.next == () and dissected.fragment is None


@pytest.mark.parametrize(
    "data",
    [b"", b"\x00", b"\x00\x09secret-octets", b"\x00\x03\x00", b"\xff\xffsecret-octets", b"\x00\x01"],
    ids=["empty", "one octet", "unknown opcode", "short DATA", "high opcode", "request with no name"],
)
def test_octets_that_are_not_a_tftp_packet_are_a_dissect_error_that_quotes_none_of_them(data):
    with pytest.raises(pktcap.DissectError) as caught:
        dissect_tftp(data)
    assert isinstance(caught.value, ValueError)
    assert str(caught.value) == "not a TFTP packet"


def test_a_tftp_layer_is_an_immutable_value_equal_only_to_its_own_type():
    layer = TFTPLayer("ACK", block=3)
    same = TFTPLayer("ACK", block=3)
    assert layer == same and not layer != same and hash(layer) == hash(same)
    assert layer != TFTPLayer("ACK", block=4) and layer != TFTPLayer("DATA", block=3)
    as_tuple = tuple(layer)
    assert layer != as_tuple and as_tuple != layer and not layer == as_tuple
    assert {layer, same} == {layer}
    with pytest.raises(AttributeError):
        layer.block = 5  # type: ignore[misc]


def test_a_tftp_layer_has_a_repr_that_is_a_constructor_call_and_survives_copy_and_pickle():
    layer = SAMPLES["RRQ"][1]
    assert repr(layer).startswith("TFTPLayer(opcode='RRQ', ")
    assert eval(repr(layer), {"TFTPLayer": TFTPLayer}) == layer
    for clone in (copy.copy(layer), copy.deepcopy(layer), pickle.loads(pickle.dumps(layer))):
        assert clone == layer and type(clone) is TFTPLayer


def _read(registry):
    dissector = pktcap.FrameDissector(registry)
    return list(pktcap.read_dissected(str(_CASES / "plain.pcap"), dissector=dissector))


def _plain_script():
    spec = importlib.util.spec_from_file_location("capture_cases_build", _CASES / "build.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    return build.plain_script()


def test_a_capture_is_dissected_on_the_request_port_and_nowhere_else():
    registry = pktcap.DissectorRegistry()
    register_tftp_dissector(registry)
    frames = _read(registry)
    script = _plain_script()
    assert len(frames) == len(script)
    # Ground truth is the script the capture was built from: the datagrams to port 69 that are TFTP.
    expected = [
        i
        for i, (_, _, destination, payload) in enumerate(script)
        if destination[1] == 69 and payload != b"hello"
    ]
    assert len(expected) == 4
    assert [i for i, frame in enumerate(frames) if frame.layer(TFTPLayer) is not None] == expected
    stray = frames[next(i for i, (_, _, _, payload) in enumerate(script) if payload == b"hello")]
    assert stray.layer(TFTPLayer) is None and stray.error == "udp 69: not a TFTP packet"
    assert stray.payload == b"hello"
    names = [frames[i].layer(TFTPLayer).filename for i in expected]
    assert names == ["boot/ipxe.efi", "logs/Net Ascii.txt", "missing.bin", "holey.bin"]


def test_the_proto_key_selects_exactly_the_frames_that_have_the_layer():
    registry = pktcap.DissectorRegistry()
    register_tftp_dissector(registry)
    frames = _read(registry)
    wanted = pktcap.compile_capture_filter("proto=tftp", pktcap.frame_filter)
    selected = [frame for frame in frames if wanted(frame)]
    assert selected and selected == [frame for frame in frames if frame.layer(TFTPLayer) is not None]


def test_the_layer_is_written_by_pktcaps_records_as_plain_data():
    registry = pktcap.DissectorRegistry()
    register_tftp_dissector(registry)
    frame = next(f for f in _read(registry) if f.layer(TFTPLayer) is not None)
    record = pktcap.frame_record(frame)
    (tftp_record,) = [layer for layer in record["layers"] if layer["layer"] == "tftp"]
    assert tftp_record["opcode"] == "RRQ" and tftp_record["filename"] == "boot/ipxe.efi"
    parsed = json.loads(pktcap.dumps_record(record))
    assert [layer["layer"] for layer in parsed["layers"]][-2:] == ["udp", "tftp"]


def test_a_port_list_registers_each_port_and_a_taken_port_registers_nothing():
    registry = pktcap.DissectorRegistry()
    register_tftp_dissector(registry, ports=(69, 6969))
    assert registry.get("udp", 69) is dissect_tftp and registry.get("udp", 6969) is dissect_tftp

    def other(data):  # pragma: no cover - never called
        raise ValueError

    taken = pktcap.DissectorRegistry()
    taken.register("udp", 6969, other)
    with pytest.raises(ValueError):
        register_tftp_dissector(taken, ports=(69, 6969))
    assert taken.get("udp", 69) is None and taken.get("udp", 6969) is other
    with pytest.raises(ValueError):
        register_tftp_dissector(registry)  # already there: pktcap's rule, not a silent replacement


@pytest.mark.parametrize(
    "ports,error", [((0,), ValueError), ((69, 65536), ValueError), (("69",), TypeError), ((True,), TypeError)]
)
def test_a_port_that_is_not_one_to_65535_is_refused_before_anything_is_registered(ports, error):
    registry = pktcap.DissectorRegistry()
    with pytest.raises(error):
        register_tftp_dissector(registry, ports=ports)
    assert registry.get("udp", 69) is None


_FRESH = """
import sys
import tftp, tftp.capture
import pktcap
default = pktcap.default_registry()
assert default.get("udp", 69) is None, "importing tftp.capture registered a dissector"
assert not [s for s in default.selectors() if s[0] == "udp"], default.selectors()
tftp.capture.register_tftp_dissector()
assert default.get("udp", 69) is tftp.capture.dissect_tftp
print("ok")
"""


def test_importing_the_package_leaves_pktcaps_default_registry_alone_until_asked():
    out = subprocess.run([sys.executable, "-c", _FRESH], capture_output=True, text=True, timeout=120)
    assert (out.returncode, out.stdout.strip(), out.stderr) == (0, "ok", "")


def test_the_dissector_is_public_in_the_capture_module():
    assert {"TFTPLayer", "dissect_tftp", "register_tftp_dissector"} <= set(tftp.capture.__all__)
