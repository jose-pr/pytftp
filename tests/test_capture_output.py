"""What `pytftp capture` prints, writes and returns for a set of committed captures.

The captures and the output recorded for them are in `tests/capture_cases/`; the
command runs as a process, so the whole path from the file to the terminal is
what is compared, octet for octet.
"""

from __future__ import annotations

import importlib.util
import io
import pathlib
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
    return (folder / (variant + ".out")).read_bytes(), int((folder / (variant + ".status")).read_text())


@pytest.mark.parametrize("variant", build.VARIANTS)
@pytest.mark.parametrize("capture", build.CAPTURES)
def test_the_command_prints_what_was_recorded(capture, variant):
    out, status = build.run_capture(_CASES / capture, build.VARIANTS[variant])
    assert (out, status) == _expected(capture, variant)


@pytest.mark.parametrize("capture", build.CAPTURES)
def test_the_command_writes_the_files_that_were_recorded(capture, tmp_path):
    out, status, files = build.run_extract(_CASES / capture, tmp_path / "files")
    folder = _EXPECTED / pathlib.Path(capture).stem
    recorded = folder / build.EXTRACT
    assert (out, status) == _expected(capture, build.EXTRACT)
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
