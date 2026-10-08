"""What `pytftp capture` prints, writes and returns for a set of committed captures.

The captures and the output recorded for them are in `tests/capture_cases/`; the
command runs as a process, so the whole path from the file to the terminal is
what is compared, octet for octet: standard output, standard error and the status.
`capture_cases/listing_before/` holds the listing the command printed before it was
pktcap's, and the listing it prints now is held to it.
"""

from __future__ import annotations

import importlib.util
import io
import pathlib
import re
import struct

import pytest

pytest.importorskip("pktcap")
pytest.importorskip("duho")

_CASES = pathlib.Path(__file__).resolve().parent / "capture_cases"
_spec = importlib.util.spec_from_file_location("capture_cases_build", _CASES / "build.py")
assert _spec is not None and _spec.loader is not None
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)

_EXPECTED = _CASES / "expected"


def _expected(capture: str, variant: str):
    folder = _EXPECTED / pathlib.Path(capture).stem
    return (
        (folder / (variant + ".out")).read_bytes(),
        (folder / (variant + ".err")).read_bytes(),
        int((folder / (variant + ".status")).read_text()),
    )


@pytest.mark.parametrize("variant", build.VARIANTS)
@pytest.mark.parametrize("capture", build.CAPTURES)
def test_the_command_prints_what_was_recorded(capture, variant):
    assert build.run_capture(_CASES / capture, build.VARIANTS[variant]) == _expected(capture, variant)


#: What the listing keeps from the one it replaced, line for line. The time was local and is UTC with the
#: date now, a DATA's size is not in the layer, and pktcap writes an address as the capture holds it.
_LINE = re.compile(r"^\d{4}-\d\d-\d\dT(\S+)Z (\S+ > \S+) tftp: (\[c\d+\]) (.*)$")
_SIZE = re.compile(r" \(\d+ bytes\)$")
_MAPPED = re.compile(r"\[::ffff:([0-9.]+)\]")
#: A line the old listing had and this one has not, and why: the datagram a snap length cut is left alone.
_LEFT_OUT = {"cut": {"22:13:21.200000 [c1] 192.0.2.1:40001 > 192.0.2.5:2000 DATA 2"}}


@pytest.mark.parametrize("capture", [name for name in build.CAPTURES if name != "wifi.pcapng"])
def test_the_listing_is_the_one_the_command_printed_before_but_for_what_is_named(capture):
    stem = pathlib.Path(capture).stem
    before = (_CASES / "listing_before" / (stem + ".txt")).read_text(encoding="utf-8").splitlines()
    kept = []
    for line in before:
        line = _SIZE.sub("", line)
        if "malformed" not in line and line not in _LEFT_OUT.get(stem, ()):
            kept.append(line)
    out, _, status = _expected(capture, "text")
    after = []
    for line in out.decode("ascii").splitlines():
        found = _LINE.match(line)
        assert found, line
        time, endpoints, session, text = found.groups()
        after.append("%s %s %s %s" % (time, session, _MAPPED.sub(r"\1", endpoints), text))
    assert after == kept and status == 0


def test_the_line_of_a_datagram_that_is_not_tftp_is_the_only_one_the_listing_lost_besides_the_cut_one():
    before = (_CASES / "listing_before" / "plain.txt").read_text(encoding="utf-8").splitlines()
    assert [line for line in before if "malformed" in line] == [
        "22:13:20.746902 192.0.2.77:5555 > 192.0.2.1:69 malformed (unknown opcode 26725)"
    ]
    # It is still counted: the summary on standard error says so.
    assert _expected("plain.pcap", "text")[1] == b"21 frames read, 19 written, 2 skipped, 1 malformed\n"


@pytest.mark.parametrize("capture", build.CAPTURES)
def test_the_command_writes_the_files_that_were_recorded(capture, tmp_path):
    out, err, status, files = build.run_extract(_CASES / capture, tmp_path / "files")
    folder = _EXPECTED / pathlib.Path(capture).stem
    recorded = folder / build.EXTRACT
    assert (out, err, status) == _expected(capture, build.EXTRACT)
    assert files == ({p.name: p.read_bytes() for p in recorded.glob("*")} if recorded.exists() else {})


@pytest.mark.parametrize("capture", sorted(build.BUILT))
def test_the_committed_captures_are_what_the_builders_write(capture):
    assert (_CASES / capture).read_bytes() == build.BUILT[capture]()


def test_the_plain_script_written_through_pktcap_is_the_committed_capture_but_for_its_snap_length():
    """``plain.pcap`` was written by the library's own writer; the datagrams are the same, as are the octets."""
    import pktcap
    from tftp.capture import PacketEvent, trace_to

    stream = io.BytesIO()
    with pktcap.PcapWriter(stream) as writer:
        hook = trace_to(writer)
        for time, source, destination, payload in build.plain_script():
            hook(PacketEvent(time, "seen", destination, source, payload))
    written, committed = stream.getvalue(), (_CASES / "plain.pcap").read_bytes()
    assert len(written) == len(committed)
    assert (written[:16], written[20:]) == (committed[:16], committed[20:])
    assert struct.unpack("<I", committed[16:20]) == (65535,) and struct.unpack("<I", written[16:20]) == (
        262144,
    )


def test_every_recorded_directory_belongs_to_a_capture():
    assert sorted(p.name for p in _EXPECTED.iterdir()) == sorted(pathlib.Path(c).stem for c in build.CAPTURES)
