"""The pktcap plugin: the keys it registers, that they agree with the library's own filter, and what
it leaves alone.

Ground truth is the datagrams the tests write into a capture, never the plugin's own conversions.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid

import pktcap
import pytest

import tftp.capture
from tftp import TFTPOpcode
from tftp.capture import PacketEvent, TFTPLayer, compile_filter, dissect_tftp, pktcap_plugin
from tftp.packet import encode_ack, encode_data, encode_error, encode_oack, encode_request

CLIENT, SERVER = ("192.0.2.5", 2000), ("192.0.2.1", 69)

#: Every opcode, sent to the request port: the one place a plain dissection finds TFTP.
DATAGRAMS = [
    encode_request(TFTPOpcode.RRQ, "boot/ipxe.efi", options={"blksize": 1024}),
    encode_request(TFTPOpcode.RRQ, "pxelinux.0"),
    encode_request(TFTPOpcode.WRQ, "UP.EFI", mode="netascii"),
    encode_request(TFTPOpcode.WRQ, "a.bin"),
    encode_data(3, b"abc"),
    encode_data(0, b""),
    encode_ack(3),
    encode_ack(65535),
    encode_error(1, "file not found"),
    encode_error(8, "option refused"),
    encode_oack({"blksize": 512}),
]


def _write(path, datagrams):
    with pktcap.PcapWriter(path) as writer:
        for index, payload in enumerate(datagrams):
            writer.write(1_700_000_000.0 + index, CLIENT, SERVER, payload)


@pytest.fixture
def capture(tmp_path):
    path = tmp_path / "boot.pcap"
    _write(path, DATAGRAMS)
    return path


def _registry():
    registry = pktcap.DissectorRegistry()
    pktcap_plugin(registry)
    return registry


def _frame_selection(capture, text, registry):
    wanted = pktcap.compile_capture_filter(text, pktcap.frame_filter_for(registry))
    frames = pktcap.read_dissected(str(capture), dissector=pktcap.FrameDissector(registry))
    return [i for i, frame in enumerate(frames) if wanted(frame)]


def _event_selection(text):
    wanted = compile_filter(text)
    return [i for i, data in enumerate(DATAGRAMS) if wanted(PacketEvent(0.0, "seen", SERVER, CLIENT, data))]


# -- what a call registers -----------------------------------------------------------------


def test_the_plugin_declares_the_layer_with_its_four_keys_and_the_dissector_on_the_request_port():
    registry = _registry()
    assert registry.layers() == {"tftp": TFTPLayer}
    assert registry.get("udp", 69) is dissect_tftp
    assert ("udp", 69) in registry.selectors()
    keys = pktcap.frame_filter_keys(registry)
    for key in ("op", "file", "block", "code", "tftp.op", "tftp.file", "tftp.block", "tftp.code"):
        assert key in keys
    # The library's own trace keys are not registered: a frame has no session, leg or direction.
    for key in ("session", "leg", "direction"):
        assert key not in keys
        with pytest.raises(pktcap.CaptureFilterError):
            pktcap.compile_capture_filter(key + "=x", pktcap.frame_filter_for(registry))


def test_the_plugin_is_loaded_by_the_module_name_and_changes_only_the_registry_it_is_given():
    registry = pktcap.DissectorRegistry()
    default_before = pktcap.default_registry().selectors()
    (loaded,) = pktcap.load_plugins(registry, "tftp.capture")
    assert (loaded.name, loaded.layers, ("udp", 69) in loaded.selectors) == ("tftp.capture", ("tftp",), True)
    assert pktcap.default_registry().selectors() == default_before
    assert "tftp" not in pktcap.default_registry().layers()


def test_a_dissector_port_that_is_taken_registers_nothing_not_even_the_layer():
    registry = pktcap.DissectorRegistry()

    def other(data):  # pragma: no cover - never called
        raise ValueError

    registry.register("udp", 69, other)
    with pytest.raises(ValueError):
        pktcap_plugin(registry)
    assert registry.layers() == {} and registry.get("udp", 69) is other


def test_a_layer_name_that_is_taken_is_the_error_and_the_other_layer_stays():
    registry = pktcap.DissectorRegistry()

    class TftpLayer:
        pass

    registry.register_layer(TftpLayer, name="tftp")
    with pytest.raises(ValueError):
        pktcap_plugin(registry)
    assert registry.layers() == {"tftp": TftpLayer} and registry.get("udp", 69) is None


def test_a_second_call_on_one_registry_is_refused_and_leaves_the_first_in_place():
    registry = _registry()
    with pytest.raises(ValueError):
        pktcap_plugin(registry)
    assert registry.layers() == {"tftp": TFTPLayer} and registry.get("udp", 69) is dissect_tftp


# -- the two filters agree -----------------------------------------------------------------

AGREE = [
    "op=RRQ",
    "op=rrq",
    "op=Wrq",
    "op=RRQ,WRQ",
    "op=DATA",
    "op=ACK,ERROR,OACK",
    "op!=ACK",
    "op!=RRQ,WRQ,DATA,ACK,ERROR,OACK",
    "file=*.efi",
    "file=*.EFI",
    "file=boot/*",
    "file=?.bin",
    "file=a.bin,pxelinux.0",
    "file=*",
    "file!=*.efi",
    "block=3",
    "block=0",
    "block=0,65535",
    "block=03",
    "block=+3",
    "block!=3",
    "code=1",
    "code=1,8",
    "code=0",
    "code!=1",
    "op=RRQ and file=*.efi",
    "op=ERROR and code=8",
    "op=DATA and block=3",
    "op=ACK and block!=3",
]
REFUSED = [
    "block=x",
    "block=-1",
    "block=65536",
    "block=1,70000",
    "block=0x3",
    "block=3.5",
    "code=x",
    "code=65536",
    "code=-5",
]


@pytest.mark.parametrize("text", AGREE)
def test_the_library_filter_over_events_and_pktcaps_over_frames_select_the_same_datagrams(capture, text):
    by_events, by_frames = _event_selection(text), _frame_selection(capture, text, _registry())
    assert by_frames == by_events


def test_the_table_selects_something_for_most_rows_so_agreement_is_not_agreement_on_nothing(capture):
    empty = [t for t in AGREE if not _event_selection(t)]
    assert empty == ["op!=RRQ,WRQ,DATA,ACK,ERROR,OACK", "code=0"]
    assert _event_selection("op=RRQ and file=*.efi") == [0]
    assert _event_selection("file=*.EFI") == [2]
    assert _event_selection("block=0,65535") == [5, 7]


@pytest.mark.parametrize("text", REFUSED)
def test_a_value_the_library_filter_refuses_pktcaps_refuses_too_when_the_filter_is_compiled(text):
    with pytest.raises(ValueError):
        compile_filter(text)
    with pytest.raises(pktcap.CaptureFilterError) as caught:
        pktcap.compile_capture_filter(text, pktcap.frame_filter_for(_registry()))
    assert text.split("=")[0] in str(caught.value)


def test_an_opcode_name_that_is_none_of_the_six_is_refused_here_and_accepted_by_the_library_filter(capture):
    assert _event_selection("op=nosuch") == []  # the library's own filter takes any text
    registry = _registry()
    with pytest.raises(pktcap.CaptureFilterError) as caught:
        pktcap.compile_capture_filter("op=nosuch", pktcap.frame_filter_for(registry))
    assert "nosuch" in str(caught.value)
    for text in ("op=RRQ,nosuch", "tftp.op=bogus", "op=1", "op=OPT"):
        with pytest.raises(pktcap.CaptureFilterError):
            pktcap.compile_capture_filter(text, pktcap.frame_filter_for(registry))


def test_a_frame_with_no_tftp_layer_fails_a_clause_and_holds_its_negation(tmp_path):
    path = tmp_path / "mixed.pcap"
    _write(path, [b"hello", encode_ack(1)])
    registry = _registry()
    assert _frame_selection(path, "block=1", registry) == [1]
    assert _frame_selection(path, "block!=1", registry) == [0]
    assert _frame_selection(path, "op!=RRQ", registry) == [0, 1]


# -- which datagrams carry the layer (what the header says) --------------------------------


def test_only_datagrams_to_or_from_the_request_port_carry_the_layer(tmp_path):
    ephemeral = ("192.0.2.1", 40001)
    path = tmp_path / "transfer.pcap"
    with pktcap.PcapWriter(path) as writer:
        writer.write(1.0, CLIENT, SERVER, encode_request(TFTPOpcode.RRQ, "a.efi"))
        writer.write(2.0, ephemeral, CLIENT, encode_data(1, b"x"))
        writer.write(3.0, CLIENT, ephemeral, encode_ack(1))
        writer.write(4.0, SERVER, CLIENT, encode_error(1, "no"))
        writer.write(5.0, CLIENT, SERVER, encode_ack(7))
    registry = _registry()
    assert _frame_selection(path, "proto=tftp", registry) == [0, 3, 4]
    assert _frame_selection(path, "op=DATA", registry) == []
    assert _frame_selection(path, "block=1", registry) == []
    assert _frame_selection(path, "block=7", registry) == [4]


# -- importing leaves everything alone -----------------------------------------------------

_FRESH = """
import sys
import tftp, tftp.capture
assert "pktcap" not in sys.modules, "importing tftp.capture imported pktcap"
assert "asyncio" not in sys.modules, "importing tftp.capture imported asyncio"
assert callable(tftp.capture.pktcap_plugin) and "pktcap" not in sys.modules
import pktcap
default = pktcap.default_registry()
assert not [s for s in default.selectors() if s[0] == "udp"], default.selectors()
assert default.layers() == {}, default.layers()
print("ok")
"""


def test_importing_the_package_imports_neither_pktcap_nor_asyncio_and_registers_nothing():
    out = subprocess.run([sys.executable, "-c", _FRESH], capture_output=True, text=True, timeout=120)
    assert (out.returncode, out.stdout.strip(), out.stderr) == (0, "ok", "")


def test_the_hook_is_public_in_the_capture_module():
    assert "pktcap_plugin" in tftp.capture.__all__


# -- pktcap's own command ------------------------------------------------------------------

_OTHER = """
from typing import NamedTuple


class OtherLayer(NamedTuple):
    op: int


def pktcap_plugin(registry):
    registry.register_layer(OtherLayer, keys={"op": lambda clause: (lambda layer: False)})
"""


@pytest.fixture
def other_plugin(tmp_path, monkeypatch):
    """A second plugin with a key named ``op``, written into a directory on ``sys.path``."""
    directory = tmp_path / ("plugins-" + uuid.uuid4().hex[:8])
    directory.mkdir()
    name = "other_demo_" + uuid.uuid4().hex[:10]
    (directory / (name + ".py")).write_bytes(_OTHER.encode("utf-8"))
    monkeypatch.syspath_prepend(str(directory))
    import importlib

    importlib.invalidate_caches()
    yield name, directory
    for loaded in [m for m in sys.modules if m == name]:
        del sys.modules[loaded]


def test_a_second_layer_with_an_op_key_makes_the_bare_key_ambiguous_and_the_dotted_one_work(
    capture, other_plugin
):
    name, _ = other_plugin
    registry = pktcap.DissectorRegistry()
    pktcap.load_plugins(registry, "tftp.capture," + name)
    with pytest.raises(pktcap.CaptureFilterError) as caught:
        pktcap.compile_capture_filter("op=RRQ", pktcap.frame_filter_for(registry))
    text = str(caught.value)
    assert "tftp.op" in text and "other.op" in text
    assert _frame_selection(capture, "tftp.op=RRQ", registry) == [0, 1]


def _pktcap(arguments, environment, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("PKTCAP_")}
    env.update(environment, PKTCAP_CONFIG="none", PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "pktcap", *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=str(cwd),
        timeout=120,
    )


@pytest.fixture
def need_command():
    pytest.importorskip("duho")


def test_pktcap_convert_with_the_plugin_named_writes_the_one_request_the_filter_selects(
    need_command, capture, tmp_path
):
    out = _pktcap(
        ["convert", "-i", str(capture), "-f", "op=RRQ and file=*.efi"],
        {"PKTCAP_PLUGINS": "tftp.capture"},
        tmp_path,
    )
    assert out.returncode == 0, out.stderr
    records = [json.loads(line) for line in out.stdout.splitlines() if line.strip()]
    assert len(records) == 1
    layers = {layer["layer"]: layer for layer in records[0]["layers"]}
    assert (layers["tftp"]["opcode"], layers["tftp"]["filename"]) == ("RRQ", "boot/ipxe.efi")


def test_pktcap_convert_refuses_an_unknown_opcode_name_with_status_2(need_command, capture, tmp_path):
    out = _pktcap(
        ["convert", "-i", str(capture), "-f", "op=nosuch"], {"PKTCAP_PLUGINS": "tftp.capture"}, tmp_path
    )
    assert out.returncode == 2 and "nosuch" in out.stderr and out.stdout == ""


def test_with_another_op_key_loaded_the_bare_key_is_status_2_naming_both_and_the_dotted_key_works(
    need_command, capture, other_plugin, tmp_path
):
    name, directory = other_plugin
    environment = {"PKTCAP_PLUGINS": "tftp.capture," + name, "PYTHONPATH": str(directory)}
    bare = _pktcap(["convert", "-i", str(capture), "-f", "op=RRQ"], environment, tmp_path)
    assert bare.returncode == 2
    assert "tftp.op" in bare.stderr and "other.op" in bare.stderr
    dotted = _pktcap(["convert", "-i", str(capture), "-f", "tftp.op=RRQ"], environment, tmp_path)
    assert dotted.returncode == 0, dotted.stderr
    assert len([line for line in dotted.stdout.splitlines() if line.strip()]) == 2


def test_pktcap_plugins_lists_the_four_keys_and_the_layer(need_command, tmp_path):
    out = _pktcap(["plugins"], {"PKTCAP_PLUGINS": "tftp.capture"}, tmp_path)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    keys = next(line for line in lines if line.strip().startswith("keys:"))
    assert all(word in keys.replace(",", " ").split() for word in ("op", "file", "block", "code"))
    assert "tftp" in next(line for line in lines if line.strip().startswith("layers:"))
    assert any("tftp.capture" in line and "udp 69" in line and "layer tftp" in line for line in lines)
