"""Replay: each captured transfer asked for again from a server the caller names.

What a test asserts is what the server received (its handler's requests, its trace of datagrams),
never what the replay says it sent.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

pktcap = pytest.importorskip("pktcap")
pytest.importorskip("duho")
from conftest import RequestSpy
from pktcap import CapturedDatagram

import tftp
from tftp import TFTPOpcode
from tftp.capture import ReplayedTransfers, replay_transfers
from tftp.cli import main
from tftp.packet import encode_ack, encode_data, encode_error, encode_request

_CASES = pathlib.Path(__file__).resolve().parent.parent / "capture_cases"
_PLAIN = str(_CASES / "plain.pcap")

CLIENT, SERVER = ("192.0.2.5", 2000), ("192.0.2.1", 69)
TID = ("192.0.2.1", 40001)


@pytest.fixture
def served(root, make_server):
    """``(spy, events, host, port)``: a writable server over ``root`` that keeps its requests and its datagrams."""
    (root / "boot").mkdir()
    (root / "boot" / "ipxe.efi").write_bytes(os.urandom(1124))
    (root / "holey.bin").write_bytes(os.urandom(600))
    (root / "logs").mkdir()
    spy, events = RequestSpy(root, writable=True, overwrite=True), []
    server = make_server(spy, trace=events.append)
    return spy, events, "127.0.0.1", server.server_address[1]


def _names(spy):
    return [(r.opcode.name, r.filename, r.mode) for r in spy.requests]


def _flow(*packets, start=1000.0, gap=0.01):
    return [CapturedDatagram(start + i * gap, src, dst, data) for i, (src, dst, data) in enumerate(packets)]


def _read_of(name, at, client_port=2000):
    """One request, from its own client port: the same request from one port again is a retransmission."""
    request = encode_request(TFTPOpcode.RRQ, name)
    return _flow(((CLIENT[0], client_port), SERVER, request), start=at)


def test_a_replay_asks_the_server_for_each_read_in_the_capture_order_with_the_options_it_carried(served):
    spy, _, host, port = served
    done = replay_transfers(_PLAIN, host, port, speed=None, timeout=0.5, retries=2)
    assert isinstance(done, ReplayedTransfers)
    # The capture's requests in the order it holds them: three reads, a write, and a stray datagram.
    assert _names(spy) == [
        ("RRQ", "boot/ipxe.efi", "octet"),
        ("RRQ", "missing.bin", "octet"),
        ("RRQ", "holey.bin", "octet"),
    ]
    first = spy.requests[0].options
    assert (first["blksize"], first["windowsize"], first["tsize"]) == ("512", "2", "0")
    assert set(first) == {"blksize", "windowsize", "tsize"}
    assert set(spy.requests[1].options) == set() and set(spy.requests[2].options) == set()
    # The server has no missing.bin: that transfer failed and the replay went on to the next.
    assert [r.is_ok for r in done.results] == [True, False, True]
    assert done.results[1].error is not None and done.skipped == 1  # the write is skipped


def test_every_address_the_server_saw_is_loopback_and_nothing_goes_to_the_captures(served):
    _, events, host, port = served
    replay_transfers(_PLAIN, host, port, speed=None, timeout=0.5, retries=1)
    assert events
    seen = {e.local[0] for e in events} | {e.remote[0] for e in events}
    assert seen == {"127.0.0.1"}


def test_a_write_is_replayed_only_when_asked_and_uploads_what_the_capture_holds(served, root):
    spy, _, host, port = served
    done = replay_transfers(_PLAIN, host, port, writes=True, speed=None, timeout=0.5, retries=2)
    assert [(n, f) for n, f, _ in _names(spy)] == [
        ("RRQ", "boot/ipxe.efi"),
        ("WRQ", "logs/Net Ascii.txt"),
        ("RRQ", "missing.bin"),
        ("RRQ", "holey.bin"),
    ]
    assert _names(spy)[1][2] == "netascii"
    assert (root / "logs" / "Net Ascii.txt").read_bytes().splitlines() == [b"one", b"two", b"three"]
    assert done.skipped == 0 and len(done.results) == 4 and done.results[1].operation == "write"


def test_without_writes_no_write_request_reaches_the_server_and_no_file_is_created(served, root):
    spy, _, host, port = served
    replay_transfers(_PLAIN, host, port, speed=None, timeout=0.5, retries=1)
    assert not any(r.opcode == TFTPOpcode.WRQ for r in spy.requests)
    assert list((root / "logs").iterdir()) == []


@pytest.mark.parametrize(
    "packets",
    [
        # DATA 2 never seen.
        [
            (CLIENT, SERVER, encode_request(TFTPOpcode.WRQ, "part.bin")),
            (TID, CLIENT, encode_ack(0)),
            (CLIENT, TID, encode_data(1, b"a" * 512)),
            (TID, CLIENT, encode_ack(1)),
            (CLIENT, TID, encode_data(3, b"c" * 5)),
            (TID, CLIENT, encode_ack(3)),
        ],
        # The server refused it.
        [(CLIENT, SERVER, encode_request(TFTPOpcode.WRQ, "part.bin")), (TID, CLIENT, encode_error(2, "no"))],
        # The last block was never acknowledged.
        [
            (CLIENT, SERVER, encode_request(TFTPOpcode.WRQ, "part.bin")),
            (TID, CLIENT, encode_ack(0)),
            (CLIENT, TID, encode_data(1, b"a" * 5)),
        ],
        # Only the request.
        [(CLIENT, SERVER, encode_request(TFTPOpcode.WRQ, "part.bin"))],
    ],
    ids=["a block missing", "refused", "last block unacknowledged", "request only"],
)
def test_a_write_the_capture_holds_only_partly_is_skipped_and_counted_even_when_writes_are_asked(
    served, root, packets
):
    spy, _, host, port = served
    done = replay_transfers(_flow(*packets), host, port, writes=True, speed=None, timeout=0.5, retries=1)
    assert (done.results, done.skipped) == ((), 1)
    assert spy.requests == [] and not (root / "part.bin").exists()


def test_a_write_the_capture_holds_whole_is_replayed_when_asked(served, root):
    spy, _, host, port = served
    packets = [
        (CLIENT, SERVER, encode_request(TFTPOpcode.WRQ, "whole.bin")),
        (TID, CLIENT, encode_ack(0)),
        (CLIENT, TID, encode_data(1, b"a" * 512)),
        (TID, CLIENT, encode_ack(1)),
        (CLIENT, TID, encode_data(2, b"b" * 5)),
        (TID, CLIENT, encode_ack(2)),
    ]
    done = replay_transfers(_flow(*packets), host, port, writes=True, speed=None, timeout=0.5)
    assert done.skipped == 0 and [r.is_ok for r in done.results] == [True]
    assert (root / "whole.bin").read_bytes() == b"a" * 512 + b"b" * 5


def test_a_read_the_capture_holds_only_partly_is_asked_for_again(served):
    spy, _, host, port = served
    packets = [
        (CLIENT, SERVER, encode_request(TFTPOpcode.RRQ, "holey.bin")),
        (TID, CLIENT, encode_data(1, b"a" * 512)),
        (TID, CLIENT, encode_data(3, b"c" * 5)),
    ]
    done = replay_transfers(_flow(*packets), host, port, speed=None, timeout=0.5)
    assert _names(spy) == [("RRQ", "holey.bin", "octet")] and done.skipped == 0


def _stubbed_sleep(monkeypatch):
    waits = []
    monkeypatch.setattr("tftp.capture._replay._sleep", waits.append)
    return waits


def test_each_wait_is_the_recorded_gap_over_speed_and_never_over_max_delay(served, monkeypatch):
    _, _, host, port = served
    waits = _stubbed_sleep(monkeypatch)
    # Requests at 100.0, 100.5 and a year later: the first has no wait before it.
    datagrams = (
        _read_of("one.bin", 100.0)
        + _read_of("one.bin", 100.5, 2001)
        + _read_of("one.bin", 100.5 + 31536000, 2002)
    )
    replay_transfers(datagrams, host, port, speed=2.0, max_delay=1.5, timeout=0.5)
    assert waits == [0.25, 1.5]


def test_without_a_speed_nothing_waits_and_by_default_the_recorded_pace_is_kept(served, monkeypatch):
    _, _, host, port = served
    waits = _stubbed_sleep(monkeypatch)
    datagrams = _read_of("one.bin", 100.0) + _read_of("one.bin", 100.75, 2001)
    replay_transfers(datagrams, host, port, speed=None, timeout=0.5)
    assert waits == []
    replay_transfers(datagrams, host, port, timeout=0.5)
    assert waits == [0.75]


def test_limit_ends_the_replay_after_that_many_transfers(served):
    spy, _, host, port = served
    datagrams = _read_of("one.bin", 1.0) + _read_of("512.bin", 2.0, 2001) + _read_of("513.bin", 3.0, 2002)
    done = replay_transfers(datagrams, host, port, speed=None, limit=2, timeout=0.5)
    assert [n for _, n, _ in _names(spy)] == ["one.bin", "512.bin"] and len(done.results) == 2


def test_only_requests_to_the_given_request_ports_are_replayed(served):
    spy, _, host, port = served
    other = ("192.0.2.1", 6969)
    datagrams = _flow((CLIENT, other, encode_request(TFTPOpcode.RRQ, "one.bin")))
    assert replay_transfers(datagrams, host, port, speed=None, timeout=0.5).results == ()
    assert spy.requests == []
    done = replay_transfers(datagrams, host, port, ports=(6969,), speed=None, timeout=0.5)
    assert [r.is_ok for r in done.results] == [True] and _names(spy) == [("RRQ", "one.bin", "octet")]


def test_a_mode_the_client_does_not_send_is_skipped_and_counted(served):
    spy, _, host, port = served
    datagrams = _flow((CLIENT, SERVER, encode_request(TFTPOpcode.RRQ, "one.bin", mode="mail")))
    done = replay_transfers(datagrams, host, port, speed=None, timeout=0.5)
    assert (done.results, done.skipped, spy.requests) == ((), 1, [])


@pytest.mark.parametrize(
    "arguments",
    [
        {"speed": 0},
        {"speed": -1.0},
        {"max_delay": -1},
        {"limit": -1},
        {"timeout": 0},
        {"retries": -1},
    ],
)
def test_arguments_out_of_range_are_refused_before_the_capture_is_read(arguments):
    with pytest.raises(ValueError):
        replay_transfers("a path that is not read", "127.0.0.1", 69, **arguments)


@pytest.mark.parametrize("port", [0, 65536, "69", True])
def test_a_port_out_of_range_is_a_value_error(port):
    with pytest.raises(ValueError):
        replay_transfers(_PLAIN, "127.0.0.1", port)


def test_a_file_that_is_no_capture_is_a_format_error(tmp_path):
    junk = tmp_path / "junk.pcap"
    junk.write_bytes(b"not a capture at all")
    with pytest.raises(pktcap.CaptureFormatError):
        replay_transfers(str(junk), "127.0.0.1", 69)


# -- the command ---------------------------------------------------------------------------------


def test_the_command_prints_one_object_per_transfer_and_is_status_one_when_one_failed(served, capsys):
    spy, _, host, port = served
    status = main(["replay", _PLAIN, host, "-p", str(port), "--speed", "100", "--json", "--timeout", "0.5"])
    captured = capsys.readouterr()
    assert status == 1  # the server has no missing.bin
    rows = [json.loads(line) for line in captured.out.splitlines()]
    assert [(r["operation"], r["filename"], r["ok"]) for r in rows] == [
        ("read", "boot/ipxe.efi", True),
        ("read", "missing.bin", False),
        ("read", "holey.bin", True),
    ]
    assert "skipped" in captured.err and not any(r.opcode == TFTPOpcode.WRQ for r in spy.requests)


def test_the_command_is_status_zero_when_every_transfer_run_succeeded(served, root, capsys):
    (root / "missing.bin").write_bytes(b"now it exists")
    _, _, host, port = served
    assert main(["replay", _PLAIN, host, "-p", str(port), "--speed", "100", "--timeout", "0.5"]) == 0
    out = capsys.readouterr().out
    assert len(out.splitlines()) == 3 and "boot/ipxe.efi" in out


def test_the_command_replays_a_write_only_with_the_flag_that_says_so(served, root, capsys):
    (root / "missing.bin").write_bytes(b"x")
    spy, _, host, port = served
    argv = ["replay", _PLAIN, host, "-p", str(port), "--speed", "100", "--timeout", "0.5"]
    assert main(argv) == 0
    assert not any(r.opcode == TFTPOpcode.WRQ for r in spy.requests)
    capsys.readouterr()
    assert main(argv + ["--writes"]) == 0
    assert any(r.opcode == TFTPOpcode.WRQ for r in spy.requests)


def test_the_command_limits_the_transfers_it_replays(served, capsys):
    spy, _, host, port = served
    main(
        [
            "replay",
            _PLAIN,
            host,
            "-p",
            str(port),
            "--speed",
            "100",
            "--limit",
            "1",
            "--json",
            "--timeout",
            "0.5",
        ]
    )
    assert [r.filename for r in spy.requests] == ["boot/ipxe.efi"]
    assert len(capsys.readouterr().out.splitlines()) == 1


def test_a_file_that_is_no_capture_is_one_error_line_and_status_two(tmp_path, capsys):
    junk = tmp_path / "junk.pcap"
    junk.write_bytes(b"not a capture at all")
    assert main(["replay", str(junk), "127.0.0.1"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("error: ") and len(captured.err.splitlines()) == 1
    assert main(["replay", str(tmp_path / "missing.pcap"), "127.0.0.1"]) == 2
    assert main(["replay", _PLAIN, "127.0.0.1", "--speed", "0"]) == 2


def test_a_host_that_does_not_resolve_is_one_error_line_and_status_one(capsys):
    assert main(["replay", _PLAIN, "no-such-host.invalid", "--speed", "100", "--timeout", "0.5"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("error: ")


def _run(*argv):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "tftp", *argv], capture_output=True, text=True, timeout=120, env=env
    )


def test_the_command_runs_as_a_process_and_its_help_says_a_write_overwrites(served):
    spy, _, host, port = served
    done = _run("replay", _PLAIN, host, "-p", str(port), "--speed", "100", "--json", "--timeout", "0.5")
    assert done.returncode == 1, done.stderr
    assert [json.loads(line)["filename"] for line in done.stdout.splitlines()] == [
        "boot/ipxe.efi",
        "missing.bin",
        "holey.bin",
    ]
    help_text = " ".join(_run("replay", "--help").stdout.split())
    assert "overwrite" in help_text and "--writes" in help_text and "reads only" in help_text.lower()
    assert "replay" in _run("--help").stdout
