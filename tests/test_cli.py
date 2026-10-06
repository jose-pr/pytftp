from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import threading
import time

import pytest

pytest.importorskip("duho")

import tftp  # noqa: E402
import tftp.options  # noqa: E402
from tftp.cli import main  # noqa: E402


def _port(server) -> str:
    return str(server.server_address[1])


def test_get_to_file_and_json(root, make_server, tmp_path_factory, capsys):
    server = make_server(root)
    out = tmp_path_factory.mktemp("out") / "copy.bin"
    code = main(["get", "127.0.0.1", "big.bin", str(out), "-p", _port(server), "--json", "-w", "4"])
    assert code == 0
    assert out.read_bytes() == (root / "big.bin").read_bytes()
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] and report["bytes"] == 300_001 and report["windowsize"] == 4


def test_get_default_name_is_basename(root, make_server, tmp_path_factory, monkeypatch):
    server = make_server(root)
    tmp_path = tmp_path_factory.mktemp("cwd")
    monkeypatch.chdir(tmp_path)
    assert main(["get", "127.0.0.1", "sub/nested.bin", "-p", _port(server)]) == 0
    assert (tmp_path / "nested.bin").read_bytes() == b"nested"


def test_get_missing_file_exits_1(root, make_server, tmp_path_factory, capsys):
    server = make_server(root)
    tmp_path = tmp_path_factory.mktemp("out")
    assert main(["get", "127.0.0.1", "nope", str(tmp_path / "x"), "-p", _port(server)]) == 1
    assert "FILE_NOT_FOUND" in capsys.readouterr().err
    assert not (tmp_path / "x").exists()


def test_put_and_errors(root, make_server, tmp_path_factory):
    server = make_server(root, writable=True)
    tmp_path = tmp_path_factory.mktemp("src")
    src = tmp_path / "up.bin"
    src.write_bytes(os.urandom(3000))
    assert main(["put", "127.0.0.1", str(src), "-p", _port(server), "--no-options"]) == 0
    assert (root / "up.bin").read_bytes() == src.read_bytes()
    assert main(["put", "127.0.0.1", str(tmp_path / "missing"), "-p", _port(server)]) == 2
    assert main(["put", "127.0.0.1", str(src), "-p", _port(server)]) == 1  # exists


def test_bad_blksize_is_a_usage_error(capsys):
    assert main(["get", "127.0.0.1", "f", "-b", "4"]) == 2
    assert "blksize" in capsys.readouterr().err


def test_serve_not_a_directory(tmp_path):
    assert main(["serve", str(tmp_path / "nope")]) == 2


def test_serve_end_to_end(root, tmp_path):
    import tftp

    proc = subprocess.Popen(
        [sys.executable, "-m", "tftp", "serve", str(root), "-l", "127.0.0.1", "-p", "0", "--json", "--write"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        port = None
        deadline = time.monotonic() + 15
        while port is None and time.monotonic() < deadline:
            line = proc.stderr.readline()
            match = re.search(r"port (\d+)", line)
            if match:
                port = int(match.group(1))
        assert port, "server never reported its port"
        client = tftp.TFTPClient("127.0.0.1", port)
        assert client.get("one.bin") == b"x"
        report = json.loads(proc.stdout.readline())
        assert report["ok"] and report["filename"] == "one.bin" and report["operation"] == "read"
    finally:
        proc.terminate()
        proc.wait(10)
        proc.stdout.close()
        proc.stderr.close()


def test_version():
    out = subprocess.run([sys.executable, "-m", "tftp", "--version"], capture_output=True, text=True)
    assert out.returncode == 0 and re.search(r"\d+\.\d+", out.stdout + out.stderr)


def test_get_and_put_by_url_with_trace_and_pcap(root, make_server, tmp_path_factory, capsys):
    server = make_server(root, writable=True)
    out = tmp_path_factory.mktemp("url")
    url = "tftp://127.0.0.1:%d/" % server.server_address[1]
    pcap = out / "client.pcap"
    assert main(["get", url + "sub/nested.bin", str(out / "n.bin"), "--trace", "--pcap", str(pcap)]) == 0
    assert (out / "n.bin").read_bytes() == b"nested"
    trace = capsys.readouterr().err
    assert "RRQ 'sub/nested.bin' octet" in trace and "DATA 1 (6 bytes)" in trace
    from tftp.capture import analyze

    (transfer,) = analyze(pcap, ports=[server.server_address[1]]).transfers
    assert transfer.is_complete and transfer.data() == b"nested"
    (out / "up.txt").write_bytes(b"a\nb\n")
    assert main(["put", url + "up-url.txt;mode=netascii", str(out / "up.txt")]) == 0
    assert (root / "up-url.txt").read_bytes() == b"a\nb\n"
    assert main(["get", "127.0.0.1"]) == 2  # no file named


def test_compat_profile(root, make_server, tmp_path_factory, capsys):
    server = make_server(root)
    target = tmp_path_factory.mktemp("compat") / "513.bin"
    assert main(["get", "127.0.0.1", "513.bin", str(target), "-p", _port(server), "--compat", "legacy"]) == 0
    assert target.read_bytes() == (root / "513.bin").read_bytes()
    assert "blksize 512" in capsys.readouterr().out  # legacy sends a plain RFC 1350 request


def _serve_subprocess(args):
    proc = subprocess.Popen(
        [sys.executable, "-m", "tftp", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    port = None
    deadline = time.monotonic() + 15
    while port is None and time.monotonic() < deadline:
        match = re.search(r"port (\d+)", proc.stderr.readline())
        if match:
            port = int(match.group(1))
    assert port, "never reported its port"
    return proc, port


def _stop(proc):
    proc.terminate()
    proc.wait(10)
    proc.stdout.close()
    proc.stderr.close()


def test_relay_and_proxy_commands(root, make_server):
    import tftp

    upstream = make_server(root)
    target = "127.0.0.1:%d" % upstream.server_address[1]
    relay, relay_port = _serve_subprocess(["relay", target, "-l", "127.0.0.1", "-p", "0", "--json"])
    try:
        assert tftp.TFTPClient("127.0.0.1", relay_port).get("513.bin") == (root / "513.bin").read_bytes()
        summary = json.loads(relay.stdout.readline())
        assert summary["filename"] == "513.bin" and summary["reason"] == "complete"
    finally:
        _stop(relay)
    proxy, proxy_port = _serve_subprocess(["serve", "--upstream", target, "-l", "127.0.0.1", "-p", "0"])
    try:
        client = tftp.TFTPClient("127.0.0.1", proxy_port, blksize=None)
        assert client.get("big.bin") == (root / "big.bin").read_bytes()
    finally:
        _stop(proxy)


def test_relay_needs_a_destination():
    assert main(["relay"]) == 2
    assert main(["relay", "--route-subnet", "nonsense"]) == 2


def test_capture_command(root, make_server, tmp_path_factory, capsys):
    from tftp.capture import PcapWriter

    out = tmp_path_factory.mktemp("cap")
    pcap = out / "server.pcap"
    with PcapWriter(pcap) as writer:
        server = make_server(root, trace=writer)
        port = _port(server)
        client_for_cli = __import__("tftp").TFTPClient("127.0.0.1", int(port))
        client_for_cli.get("1428x3.bin")
        try:
            client_for_cli.get("missing")
        except Exception:
            pass
        server.shutdown()
        assert server.wait_closed(5.0)
    assert main(["capture", str(pcap), "-p", port, "--filter", "op=RRQ,ERROR"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 3 and "RRQ '1428x3.bin'" in lines[0] and "ERROR 1" in lines[-1]
    assert main(["capture", str(pcap), "-p", port, "--json", "--no-packets", "--transfers"]) == 0
    records = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert [r["transfer"]["filename"] for r in records] == ["1428x3.bin", "missing"]
    assert records[0]["transfer"]["complete"] and records[1]["transfer"]["error"]["code"] == 1
    assert records[0]["transfer"]["bytes"] == 1428 * 3  # counted with payloads off, as here
    assert records[0]["transfer"]["retransmissions"] == 0 and records[0]["transfer"]["missing_blocks"] == []
    target = out / "extracted"
    assert main(["capture", str(pcap), "-p", port, "--no-packets", "--extract", str(target)]) == 0
    (written,) = list(target.iterdir())
    assert written.name.endswith("-1428x3.bin") and written.read_bytes() == (root / "1428x3.bin").read_bytes()


def test_capture_errors(tmp_path):
    assert main(["capture"]) == 2
    assert main(["capture", str(tmp_path / "missing.pcap")]) == 2
    (tmp_path / "junk.pcap").write_bytes(b"not a capture at all")
    assert main(["capture", str(tmp_path / "junk.pcap")]) == 2
    assert main(["capture", str(tmp_path / "junk.pcap"), "--filter", "colour=red"]) == 2


def test_serve_deployment_flags(root):
    import tftp

    (root / "127.0.0.1").mkdir()
    (root / "127.0.0.1" / "Menu.CFG").write_bytes(b"per-client menu")
    args = ["serve", str(root), "-l", "127.0.0.1", "-p", "0", "--per-client", "--ignore-case"]
    args += ["--remap", "^pxelinux/=", "--port-range", "45100:45120"]
    proc, port = _serve_subprocess(args)
    try:
        events = []
        client = tftp.TFTPClient("127.0.0.1", port, trace=events.append)
        assert client.get("pxelinux/menu.cfg") == b"per-client menu"
        assert all(45100 <= e.remote[1] <= 45120 for e in events if e.direction == "in")
    finally:
        _stop(proc)


@pytest.mark.parametrize(
    "args",
    [
        ["--port-range", "10"],
        ["--port-range", "9:8"],
        ["--remap", "nothing"],
        ["--http", "http://x/", "--per-client"],
    ],
)
def test_serve_deployment_flag_errors(tmp_path, args):
    assert main(["serve", str(tmp_path), *args]) == 2


def test_ls_against_serve_listing(root, capsys):
    proc, port = _serve_subprocess(["serve", str(root), "-l", "127.0.0.1", "-p", "0", "--listing"])
    try:
        assert not main(["ls", "127.0.0.1", "-p", str(port)])
        out = capsys.readouterr().out
        assert "sub/" in out and "big.bin" in out and "300001" in out
        assert not main(["ls", "tftp://127.0.0.1:%d/sub" % port, "--json"])
        listed = json.loads(capsys.readouterr().out)
        assert listed[0]["name"] == "nested.bin" and listed[0]["size"] == 6
        assert main(["ls", "127.0.0.1", "one.bin", "-p", str(port)]) == 1
        assert "not a directory" in capsys.readouterr().err
    finally:
        _stop(proc)


def test_serve_on_interface(root):
    import netimps

    import tftp

    iface = netimps.get_interface("127.0.0.1")
    if iface is None:
        pytest.skip("no loopback interface reported")
    proc, port = _serve_subprocess(["serve", str(root), "--interface", iface.name, "-p", "0"])
    try:
        assert tftp.TFTPClient(str(iface.primary_ip(ipv6=False).ip), port).get("one.bin") == b"x"
    finally:
        _stop(proc)


# -- every deployment flag on its own --------------------------------------------------------------


def test_serve_remap_alone_on_a_plain_directory(root):
    import tftp

    (root / "real").mkdir()
    (root / "real" / "a.txt").write_bytes(b"remapped")
    proc, port = _serve_subprocess(
        ["serve", str(root), "-l", "127.0.0.1", "-p", "0", "--remap", "^alias=real", "--listing"]
    )
    try:
        client = tftp.TFTPClient("127.0.0.1", port)
        assert client.get("alias/a.txt") == b"remapped"  # a name a rule rewrites
        assert client.get("one.bin") == b"x"  # a name no rule touches
        assert [entry.name for entry in client.listdir("real")] == ["a.txt"]
        assert [entry.name for entry in client.listdir("alias")] == ["a.txt"]  # a listing keeps its flag
    finally:
        _stop(proc)


@pytest.mark.parametrize(
    "flag, name, expected",
    [("--per-client", "Own.cfg", b"own"), ("--ignore-case", "ONE.BIN", b"x")],
)
def test_serve_per_client_and_ignore_case_each_on_their_own(root, flag, name, expected):
    import tftp

    (root / "127.0.0.1").mkdir()
    (root / "127.0.0.1" / "Own.cfg").write_bytes(b"own")
    proc, port = _serve_subprocess(["serve", str(root), "-l", "127.0.0.1", "-p", "0", flag])
    try:
        assert tftp.TFTPClient("127.0.0.1", port).get(name) == expected
    finally:
        _stop(proc)


# -- the stop path ---------------------------------------------------------------------------------


def _wait_exit(proc, seconds):
    try:
        return proc.wait(seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(5)
        pytest.fail("the command did not stop within %s s" % seconds)


@pytest.mark.skipif(os.name == "nt", reason="the console event is raised through a driver on Windows")
@pytest.mark.parametrize("name", ["SIGINT", "SIGTERM"])
@pytest.mark.parametrize("command", ["serve", "relay"])
def test_an_idle_command_stops_on_a_signal_and_reports_its_counters(root, name, command):
    import signal

    args = ["serve", str(root)] if command == "serve" else ["relay", "127.0.0.1:9"]
    proc, _ = _serve_subprocess([*args, "-l", "127.0.0.1", "-p", "0"])
    try:
        time.sleep(0.5)
        started = time.monotonic()
        proc.send_signal(getattr(signal, name))
        assert _wait_exit(proc, 5) == 0
        assert time.monotonic() - started < 3
        assert ("served:" if command == "serve" else "relayed:") in proc.stderr.read()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()
        proc.stderr.close()


@pytest.mark.skipif(os.name != "nt", reason="console control events are Windows'")
@pytest.mark.parametrize("event", ["ctrl-c", "ctrl-break"])
@pytest.mark.parametrize("command", ["serve", "relay"])
def test_an_idle_command_stops_on_a_console_event_and_reports_its_counters(tmp_path, event, command):
    report = tmp_path / "report.json"
    driver = os.path.join(os.path.dirname(__file__), "ctrlc_driver.py")
    proc = subprocess.Popen(
        [sys.executable, driver, command, event, str(report)],
        creationflags=subprocess.CREATE_NO_WINDOW,  # a console of its own: the event goes to everything on it
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    try:
        proc.wait(90)
    finally:
        if proc.poll() is None:
            proc.kill()
    marks = json.loads(report.read_text())
    assert marks["alive_before_event"], marks
    assert marks["exited_after"] is not None and marks["exited_after"] < 3, marks
    assert marks["returncode"] == 0, marks
    assert ("served:" if command == "serve" else "relayed:") in marks["stderr"], marks


# -- A tftp:// URL's options reach the transfer; a flag wins over them ----------------


def test_get_url_options_move_the_transfer(root, spy_server, tmp_path_factory, capsys):
    spy, base = spy_server()
    out = tmp_path_factory.mktemp("out") / "copy.bin"
    assert not main(["get", base + "big.bin?blksize=512&windowsize=4&cookie=abc", str(out), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert out.read_bytes() == (root / "big.bin").read_bytes()
    assert report["blksize"] == 512 and report["windowsize"] == 4
    wire = spy.requests[-1].options
    assert (wire["blksize"], wire["windowsize"], wire["cookie"]) == ("512", "4", "abc")
    assert not main(["get", base + "big.bin;blksize=1024;windowsize=2", str(out), "--json"])
    assert json.loads(capsys.readouterr().out)["blksize"] == 1024


def test_get_flags_win_over_the_url(root, spy_server, tmp_path_factory, capsys):
    spy, base = spy_server()
    out = tmp_path_factory.mktemp("out") / "copy.bin"
    url = base + "big.bin?blksize=512&windowsize=4"
    assert not main(["get", url, str(out), "--json", "-b", "1024", "-w", "2"])
    report = json.loads(capsys.readouterr().out)
    assert report["blksize"] == 1024 and report["windowsize"] == 2
    assert spy.requests[-1].options["blksize"] == "1024"
    # --no-options asks for nothing at all, whatever the URL says.
    assert not main(["get", url + "&cookie=abc", str(out), "--json", "--no-options"])
    capsys.readouterr()
    assert dict(spy.requests[-1].options) == {}
    # -b 0 requests no blksize.
    assert not main(["get", url, str(out), "--json", "-b", "0"])
    capsys.readouterr()
    assert "blksize" not in spy.requests[-1].options and spy.requests[-1].options["windowsize"] == "4"


def test_get_mode_from_the_url_unless_a_flag_says_otherwise(root, spy_server, tmp_path_factory):
    spy, base = spy_server()
    out = tmp_path_factory.mktemp("out") / "t.txt"
    assert not main(["get", base + "text.txt?mode=netascii", str(out)])
    assert spy.requests[-1].mode == "netascii"
    assert not main(["get", base + "text.txt;mode=netascii", str(out), "-m", "octet"])
    assert spy.requests[-1].mode == "octet"
    assert not main(["get", base + "text.txt", str(out)])
    assert spy.requests[-1].mode == "octet"


def test_put_url_options_move_the_transfer(root, spy_server, tmp_path_factory, capsys):
    spy, base = spy_server()
    src = tmp_path_factory.mktemp("src") / "up.bin"
    src.write_bytes(os.urandom(3000))
    assert not main(["put", base + "put-opts.bin?blksize=512&windowsize=2&cookie=up", str(src), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert (root / "put-opts.bin").read_bytes() == src.read_bytes()
    assert report["blksize"] == 512 and report["windowsize"] == 2
    assert spy.requests[-1].options["cookie"] == "up"
    assert not main(["put", base + "put-opts.bin;blksize=512", str(src), "--json", "-b", "1024"])
    assert json.loads(capsys.readouterr().out)["blksize"] == 1024


def test_ls_url_options_reach_the_server(root, spy_server, capsys):
    from tftp import TFTPServerOptions
    from tftp.options import LISTING_OPTIONS, STANDARD_OPTIONS

    spy, base = spy_server(options=TFTPServerOptions(allowed=STANDARD_OPTIONS | LISTING_OPTIONS))
    assert not main(["ls", base + "sub?cookie=abc&blksize=512", "--json"])
    assert json.loads(capsys.readouterr().out)[0]["name"] == "nested.bin"
    wire = spy.requests[-1].options
    assert (wire["cookie"], wire["blksize"]) == ("abc", "512")


def test_a_bad_url_option_is_a_usage_error(capsys):
    assert main(["get", "tftp://127.0.0.1/f?blksize=abc", "-p", "9"]) == 2
    assert "blksize" in capsys.readouterr().err


# -- a failure is one line and an exit status ---------------------------------------------------


def _error_lines(err: str) -> list:
    assert "Traceback" not in err, err
    return [line for line in err.splitlines() if line.startswith("error:")]


@pytest.fixture
def target_dir(tmp_path):
    (tmp_path / "is-a-directory").mkdir()
    return tmp_path


@pytest.mark.parametrize(
    "case",
    [
        "unwritable target",
        "target is a directory",
        "no such host",
        "put to no such host",
        "ls of no such host",
    ],
)
def test_an_everyday_failure_is_one_error_line_and_exit_1(root, make_server, target_dir, capsys, case):
    server = make_server(root)
    port = _port(server)
    argv = {
        "unwritable target": [
            "get",
            "127.0.0.1",
            "one.bin",
            str(target_dir / "no" / "such" / "out"),
            "-p",
            port,
        ],
        "target is a directory": [
            "get",
            "127.0.0.1",
            "one.bin",
            str(target_dir / "is-a-directory"),
            "-p",
            port,
        ],
        "no such host": ["get", "no-such-host.invalid", "one.bin", str(target_dir / "out"), "-t", "0.2"],
        "put to no such host": ["put", "no-such-host.invalid", str(root / "one.bin"), "-t", "0.2"],
        "ls of no such host": ["ls", "no-such-host.invalid", "-t", "0.2"],
    }[case]
    assert main(argv) == 1
    assert len(_error_lines(capsys.readouterr().err)) == 1
    assert not (target_dir / "out").exists()


def test_what_a_server_wrote_in_an_error_cannot_reach_the_terminal(target_dir, capsys):
    from conftest import FakePeer
    from tftp.packet import encode_error

    with FakePeer(lambda data: [encode_error(1, "gone\x1b[2J\x1b]0;owned\x07")]) as peer:
        assert (
            main(["get", "127.0.0.1", "f", str(target_dir / "out"), "-p", str(peer.port), "-t", "0.5"]) == 1
        )
    (line,) = _error_lines(capsys.readouterr().err)
    assert line.isprintable() and "\\x1b[2J" in line


# -- a file given to --pcap is not touched before the command can run ------------------------------


def _existing_pcap(tmp_path) -> "tuple[object, bytes]":
    path = tmp_path / "keep.pcap"
    path.write_bytes(b"a capture somebody wants" * 100)
    return path, path.read_bytes()


def _busy_port():
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    return sock, str(sock.getsockname()[1])


@pytest.mark.parametrize("command", ["get", "put", "serve", "relay", "serve timeout"])
def test_a_command_that_cannot_run_leaves_the_pcap_it_was_given(tmp_path, capsys, command):
    path, before = _existing_pcap(tmp_path)
    busy, port = _busy_port()
    with busy:
        argv = {
            "get": ["get", "127.0.0.1", "f", "-b", "4", "--pcap", str(path)],
            "put": ["put", "127.0.0.1", str(tmp_path / "missing"), "--pcap", str(path)],
            "serve": ["serve", str(tmp_path), "-l", "127.0.0.1", "-p", port, "--pcap", str(path)],
            "relay": ["relay", "127.0.0.1:9", "-l", "127.0.0.1", "-p", port, "--pcap", str(path)],
            "serve timeout": [
                "serve",
                str(tmp_path),
                "-l",
                "127.0.0.1",
                "-p",
                "0",
                "-t",
                "0",
                "--pcap",
                str(path),
            ],
        }[command]
        assert main(argv) in (1, 2)
    assert "Traceback" not in capsys.readouterr().err
    assert path.read_bytes() == before
    path.unlink()  # nothing holds it open


def test_a_pcap_that_cannot_be_written_is_an_error_line(root, make_server, tmp_path, capsys):
    server = make_server(root)
    argv = ["get", "127.0.0.1", "one.bin", str(tmp_path / "out"), "-p", _port(server)]
    assert main(argv + ["--pcap", str(tmp_path / "no" / "such" / "dir.pcap")]) == 1
    assert len(_error_lines(capsys.readouterr().err)) == 1
    assert not (tmp_path / "out").exists()


# -- a reader that closes the pipe early ----------------------------------------------------------


def test_a_closed_stdout_ends_a_printing_command_quietly(tmp_path):
    from tftp.capture import PcapWriter
    from tftp.packet import encode_ack, encode_data, encode_request

    path = tmp_path / "long.pcap"
    with PcapWriter(path) as writer:
        writer.write(1.0, ("10.0.0.5", 2000), ("10.0.0.1", 69), encode_request(1, "f"))
        for block in range(1, 3000):
            writer.write(
                2.0 + block, ("10.0.0.1", 3000), ("10.0.0.5", 2000), encode_data(block % 65536, b"x" * 512)
            )
            writer.write(2.5 + block, ("10.0.0.5", 2000), ("10.0.0.1", 3000), encode_ack(block % 65536))
    proc = subprocess.Popen(
        [sys.executable, "-m", "tftp", "capture", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert b"RRQ" in proc.stdout.readline()  # the first event, then the reader goes away
    proc.stdout.close()
    err = proc.stderr.read()
    assert proc.wait(30) in (0, 1)
    proc.stderr.close()
    assert err == b"", err


@pytest.mark.parametrize("command", ["serve", "relay"])
def test_serve_and_relay_record_what_they_move_when_given_a_pcap(root, make_server, tmp_path, command):
    """The capture file is opened after the socket is bound, and every datagram reaches it."""
    import tftp
    from tftp.capture import analyze

    pcap = tmp_path / "moved.pcap"
    if command == "serve":
        argv = ["serve", str(root), "-l", "127.0.0.1", "-p", "0", "--pcap", str(pcap)]
    else:
        upstream = make_server(root)
        argv = ["relay", "127.0.0.1:%d" % upstream.server_address[1], "-l", "127.0.0.1", "-p", "0"]
        argv += ["--pcap", str(pcap)]
    proc, port = _serve_subprocess(argv)
    try:
        assert tftp.TFTPClient("127.0.0.1", port).get("513.bin") == (root / "513.bin").read_bytes()
        time.sleep(0.3)  # the last datagrams are written as they are seen
    finally:
        _stop(proc)
    (transfer,) = [t for t in analyze(str(pcap), ports=[port]).transfers if t.filename == "513.bin"][:1]
    assert transfer.is_complete and transfer.size == 513


# -- the entry point and the root --------------------------------------------------------------


def test_the_logging_flags_work_before_the_subcommand(capsys):
    assert main(["-v", "get", "127.0.0.1", "f", "-b", "4"]) == 2
    assert "blksize" in capsys.readouterr().err
    assert main(["-q", "get", "127.0.0.1", "f", "-b", "4"]) == 2


def test_main_returns_an_int_for_every_outcome(root, make_server, tmp_path, capsys):
    server = make_server(root)
    status = main(["get", "127.0.0.1", "one.bin", str(tmp_path / "o"), "-p", _port(server)])
    assert type(status) is int and status == 0
    assert type(main(["relay"])) is int


def _code_without_duho(body: str) -> subprocess.CompletedProcess:
    code = "import sys\nsys.modules['duho'] = None  # an import of it raises ImportError\n" + body
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)


def test_without_the_extra_the_command_names_it_and_exits_1():
    done = _code_without_duho("from tftp.cli import main\nraise SystemExit(main(['--help']))\n")
    assert done.returncode == 1 and done.stdout == ""
    assert done.stderr.strip() == "pytftp: the CLI needs the 'cli' extra -- pip install 'tftp[cli]'"


def test_python_dash_m_without_the_extra_says_the_same():
    done = _code_without_duho("import runpy\nrunpy.run_module('tftp', run_name='__main__')\n")
    assert done.returncode == 1 and "'cli' extra" in done.stderr and "Traceback" not in done.stderr


def test_importing_the_command_package_needs_no_duho():
    done = _code_without_duho("import tftp, tftp.cli\nprint(tftp.cli.main.__name__)\n")
    assert done.returncode == 0 and done.stdout.strip() == "main"


def test_the_tool_server_is_not_started_by_the_environment():
    request = (
        '{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", '
        '"capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}\n'
        '{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}\n'
    )
    env = dict(os.environ, PYTFTP_MCP="stdio")
    done = subprocess.run(
        [sys.executable, "-m", "tftp"], input=request, capture_output=True, text=True, timeout=60, env=env
    )
    assert done.returncode == 2 and "jsonrpc" not in done.stdout and "tools" not in done.stdout
    assert "required" in done.stderr  # the plain command asking for a subcommand


@pytest.fixture
def serve_refused(monkeypatch, tmp_path, capsys):
    """``run(*args) -> (status, stderr)`` for a ``serve`` that must refuse its arguments.

    A serve that gets as far as serving raises instead of blocking the test.
    """

    def never(self):
        raise AssertionError("the server went on to serve")

    monkeypatch.setattr(tftp.TFTPServer, "serve_forever", never)

    def run(*args):
        status = main(["serve", str(tmp_path), "-l", "127.0.0.1", "-p", "0", *args])
        return status, capsys.readouterr().err

    return run


# -- one reporting path -------------------------------------------------------------------------


def _one_json_line(out: str) -> dict:
    assert out.endswith("\n") and out.count("\n") == 1, out
    return json.loads(out)


_LISTING_AND_STANDARD = tftp.options.STANDARD_OPTIONS | tftp.options.LISTING_OPTIONS


def test_the_text_result_of_get_and_put_is_on_stdout(root, make_server, tmp_path_factory, capsys):
    tmp_path = tmp_path_factory.mktemp("local")
    server = make_server(root, writable=True)
    assert main(["get", "127.0.0.1", "one.bin", str(tmp_path / "o"), "-p", _port(server)]) == 0
    out, err = capsys.readouterr()
    assert out.startswith("received 1 bytes in ") and out.count("\n") == 1 and err == ""
    (tmp_path / "up.bin").write_bytes(b"abcdef")
    assert main(["put", "127.0.0.1", str(tmp_path / "up.bin"), "-p", _port(server)]) == 0
    out, err = capsys.readouterr()
    assert out.startswith("sent 6 bytes in ") and out.count("\n") == 1 and err == ""


def test_quiet_silences_the_text_result_and_not_the_json(root, make_server, tmp_path, capsys):
    server = make_server(root, writable=True)
    get = ["get", "127.0.0.1", "one.bin", str(tmp_path / "o"), "-p", _port(server)]
    for quiet in ("-q", "-qq"):
        assert main(get + [quiet]) == 0
        assert capsys.readouterr() == ("", "")
    assert main(["-q", *get]) == 0  # the root takes it too
    assert capsys.readouterr() == ("", "")
    assert main(get + ["-q", "--json"]) == 0
    assert _one_json_line(capsys.readouterr().out)["bytes"] == 1


def test_json_is_one_object_on_one_line_for_get_put_and_ls(root, make_server, tmp_path_factory, capsys):
    tmp_path = tmp_path_factory.mktemp("local")
    server = make_server(root, writable=True, options=tftp.TFTPServerOptions(allowed=_LISTING_AND_STANDARD))
    port = _port(server)
    assert main(["get", "127.0.0.1", "big.bin", str(tmp_path / "o"), "-p", port, "--json", "-w", "4"]) == 0
    got = _one_json_line(capsys.readouterr().out)
    assert got["operation"] == "read" and got["bytes"] == 300_001 and got["windowsize"] == 4
    (tmp_path / "up.bin").write_bytes(b"abcdef")
    assert main(["put", "127.0.0.1", str(tmp_path / "up.bin"), "-p", port, "--json"]) == 0
    put = _one_json_line(capsys.readouterr().out)
    assert put["operation"] == "write" and put["bytes"] == 6 and put["ok"] is True
    assert main(["ls", "127.0.0.1", "sub", "-p", port, "--json"]) == 0
    listed = _one_json_line(capsys.readouterr().out)
    assert [entry["name"] for entry in listed] == ["nested.bin"]


def test_a_download_to_stdout_keeps_stdout_for_the_file(root, make_server):
    server = make_server(root)
    argv = [sys.executable, "-m", "tftp", "get", "127.0.0.1", "one.bin", "-", "-p", _port(server)]
    done = subprocess.run(argv, capture_output=True, timeout=60)
    assert done.returncode == 0 and done.stdout == b"x"
    assert done.stderr.decode().startswith("received 1 bytes in ")
    quiet = subprocess.run(argv + ["-q"], capture_output=True, timeout=60)
    assert quiet.returncode == 0 and quiet.stdout == b"x" and quiet.stderr == b""


def test_json_with_stdout_as_the_target_is_a_usage_error_and_no_transfer(spy_server, capsys):
    spy, base = spy_server()
    assert main(["get", base + "one.bin", "-", "--json"]) == 2
    out, err = capsys.readouterr()
    assert out == "" and err.startswith("error:") and "--json" in err and err.count("\n") == 1
    assert spy.requests == []


@pytest.mark.parametrize(
    "argv",
    [
        ["get", "{url}one.bin", "out", "third"],
        ["put", "{url}up.bin", "{src}", "third"],
        ["ls", "{url}sub", "third"],
    ],
)
def test_a_third_argument_after_a_url_is_refused_not_ignored(spy_server, tmp_path, capsys, argv):
    spy, base = spy_server()
    (tmp_path / "src.bin").write_bytes(b"data")
    argv = [part.format(url=base, src=tmp_path / "src.bin") for part in argv]
    assert main(argv) == 2
    err = capsys.readouterr().err
    assert err.startswith("error:") and "URL" in err and err.count("\n") == 1
    assert spy.requests == []


def test_ipv4_and_ipv6_together_are_refused(spy_server, capsys):
    spy, _ = spy_server()
    with pytest.raises(SystemExit) as exit:
        main(["get", "127.0.0.1", "one.bin", "-4", "-6"])
    assert exit.value.code == 2 and "not allowed with" in capsys.readouterr().err
    assert spy.requests == []


@pytest.mark.parametrize("flags", [["-b", "8192"], ["-w", "8"], ["--no-tsize"], ["--no-options"]])
def test_option_flags_beside_a_compat_profile_are_refused_by_the_client(
    spy_server, capsys, flags, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)  # the control download below writes to the working directory
    spy, base = spy_server()
    port = base.rsplit(":", 1)[1].rstrip("/")
    assert main(["get", "127.0.0.1", "one.bin", "-p", port, "--compat", "legacy", *flags]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error:") and "--compat" in err and flags[0] in err
    assert spy.requests == []
    assert main(["get", "127.0.0.1", "one.bin", "-p", port, "--compat", "legacy", "-t", "2", "-r", "1"]) == 0


@pytest.mark.parametrize(
    "flags",
    [
        ["--max-blksize", "1024"],
        ["--max-windowsize", "2"],
        ["--allow", "cookie"],
        ["--refuse", "tsize"],
        ["--fit-mtu"],
    ],
)
def test_option_flags_beside_a_compat_profile_are_refused_by_the_server(serve_refused, flags):
    status, err = serve_refused("--compat", "pxe", *flags)
    assert status == 2 and err.startswith("error:") and "--compat" in err and flags[0] in err


def test_put_says_what_is_wrong_with_the_local_file(root, make_server, tmp_path, capsys):
    server = make_server(root, writable=True)
    port = _port(server)
    assert main(["put", "127.0.0.1", str(tmp_path), "-p", port]) == 2
    assert "is a directory" in capsys.readouterr().err
    assert main(["put", "127.0.0.1", str(tmp_path / "missing"), "-p", port]) == 2
    assert "no such file" in capsys.readouterr().err


def test_help_shows_the_defaults_and_says_what_omitting_a_value_means(capsys):
    for argv, shown in (
        (["get", "--help"], ["(default: 69)", "(default: 5)", "Default: the URL's, else octet"]),
        (["serve", "--help"], ["(default: 500)", "(default: 69)", "Default: no limit", "not isolation"]),
        (["relay", "--help"], ["(default: 30.0)"]),
    ):
        with pytest.raises(SystemExit) as exit:
            main(argv)
        assert exit.value.code == 0
        text = " ".join(capsys.readouterr().out.split())
        assert [part for part in shown if part not in text] == []


# -- the deployment flags that bound a server --------------------------------------------------


def test_per_client_only_gives_a_client_with_no_directory_nothing(root):
    (root / "10.9.9.9").mkdir()
    (root / "10.9.9.9" / "secret.cfg").write_bytes(b"for 10.9.9.9 only")
    for flags in (["--per-client", "--per-client-only"], ["--per-client-only"]):
        proc, port = _serve_subprocess(["serve", str(root), "-l", "127.0.0.1", "-p", "0", *flags])
        try:
            client = tftp.TFTPClient("127.0.0.1", port, timeout=1.0, retries=2)
            for name in ("10.9.9.9/secret.cfg", "one.bin"):
                with pytest.raises(tftp.FileNotFound):
                    client.get(name)
        finally:
            _stop(proc)
    proc, port = _serve_subprocess(["serve", str(root), "-l", "127.0.0.1", "-p", "0", "--per-client"])
    try:
        # without the flag the default stands: the root, other clients' directories included
        assert tftp.TFTPClient("127.0.0.1", port).get("10.9.9.9/secret.cfg") == b"for 10.9.9.9 only"
    finally:
        _stop(proc)


def test_max_upload_bounds_what_a_client_may_send(root):
    proc, port = _serve_subprocess(
        ["serve", str(root), "-l", "127.0.0.1", "-p", "0", "--write", "--max-upload", "1000"]
    )
    try:
        client = tftp.TFTPClient("127.0.0.1", port)
        client.put("small.bin", b"s" * 900)
        assert (root / "small.bin").read_bytes() == b"s" * 900
        with pytest.raises(tftp.DiskFull):
            client.put("big-up.bin", b"b" * 2000)
        with pytest.raises(tftp.DiskFull):  # no announced size: the upload grows past the bound
            tftp.TFTPClient("127.0.0.1", port, tsize=False).put("grown.bin", b"g" * 2000)
        assert not (root / "big-up.bin").exists() and not (root / "grown.bin").exists()
    finally:
        _stop(proc)


def test_max_duration_ends_a_transfer_nobody_finishes(root):
    import socket

    from tftp.packet import encode_request

    args = ["--max-duration", "1", "-t", "1", "-r", "60", "--json"]
    proc, port = _serve_subprocess(["serve", str(root), "-l", "127.0.0.1", "-p", "0", *args])
    lines = []
    reader = threading.Thread(target=lambda: lines.append(proc.stdout.readline()), daemon=True)
    reader.start()
    started = time.monotonic()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(5)
            sock.sendto(encode_request(1, "big.bin", mode="octet"), ("127.0.0.1", port))
            assert sock.recvfrom(2048)[0][:2] == b"\x00\x03"  # DATA 1; never acknowledged
            reader.join(20)
        assert lines, "the transfer was not ended"
        record = json.loads(lines[0])
        assert record["ok"] is False and record["filename"] == "big.bin"
        assert time.monotonic() - started < 20  # sixty retransmissions would take a minute
    finally:
        _stop(proc)


@pytest.mark.parametrize(
    "flags",
    [
        ["--max-upload", "10", "--http", "http://x/"],
        ["--max-upload", "-1"],
        ["--max-duration", "0"],
        ["--max-duration", "-3"],
        ["--per-client-only", "--http", "http://x/"],
    ],
)
def test_the_bounding_flags_refuse_what_cannot_be_honoured(serve_refused, flags):
    status, err = serve_refused(*flags)
    assert status == 2 and err.startswith("error:")


def test_the_new_flags_are_documented_where_a_user_reads_them():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    texts = {
        "README.md": (root / "README.md").read_text(encoding="utf-8"),
        "the shipped header": (root / "src" / "tftp" / "AGENTS.md").read_text(encoding="utf-8"),
    }
    for flag in ("--max-duration", "--max-upload", "--per-client-only"):
        assert [name for name, text in texts.items() if flag not in text] == [], flag


def test_remap_applies_to_a_proxied_source_too(root, make_server):
    upstream = make_server(root)
    target = "127.0.0.1:%d" % upstream.server_address[1]
    args = ["serve", "--upstream", target, "-l", "127.0.0.1", "-p", "0", "--remap", "^alias$=one.bin"]
    proc, port = _serve_subprocess(args)
    try:
        assert tftp.TFTPClient("127.0.0.1", port).get("alias") == b"x"
    finally:
        _stop(proc)


# -- every serve flag on its own ---------------------------------------------------------------


def _download(port, name="big.bin", **client):
    """The result of a download from a serve on ``port``, judged from what the client negotiated."""
    client.setdefault("timeout", 1.0)
    return tftp.TFTPClient("127.0.0.1", port, **client).download(name, io.BytesIO())


def _alone(root, *flags):
    return _serve_subprocess(["serve", str(root), "-l", "127.0.0.1", "-p", "0", *flags])


def test_port_range_alone_pins_the_transfer_ports(root):
    proc, port = _alone(root, "--port-range", "45100:45120")
    try:
        events = []
        assert tftp.TFTPClient("127.0.0.1", port, trace=events.append).get("513.bin")
        assert {e.remote[1] for e in events if e.direction == "in"} <= set(range(45100, 45121))
    finally:
        _stop(proc)


@pytest.mark.parametrize(
    "flags, client, field, expected",
    [
        (["--max-blksize", "600"], {"blksize": 4000}, "blksize", 600),
        (["--max-windowsize", "2"], {"windowsize": 16}, "windowsize", 2),
        (["--refuse", "windowsize"], {"windowsize": 8}, "windowsize", 1),
        (["--compat", "legacy"], {"windowsize": 8}, "windowsize", 1),
        ([], {"windowsize": 8}, "windowsize", 8),
    ],
)
def test_a_negotiation_flag_alone_changes_what_the_client_is_granted(root, flags, client, field, expected):
    proc, port = _alone(root, *flags)
    try:
        assert getattr(_download(port, **client).negotiated, field) == expected
    finally:
        _stop(proc)


def test_allow_acknowledges_an_extension_option_and_without_it_the_option_is_dropped(root):
    for flags, echoed in ((["--allow", "cookie"], "abc"), ([], None)):
        proc, port = _alone(root, *flags)
        try:
            extra = _download(port, "one.bin", extra_options={"cookie": "abc"}).negotiated.extra
            assert extra.get("cookie") == echoed
        finally:
            _stop(proc)


def test_fit_mtu_serves_a_file_whole(root):
    proc, port = _alone(root, "--fit-mtu")
    try:
        assert tftp.TFTPClient("127.0.0.1", port).get("big.bin") == (root / "big.bin").read_bytes()
    finally:
        _stop(proc)


def test_write_overwrite_and_no_create_each_decide_what_an_upload_may_do(root):
    cases = [
        ([], "new.bin", tftp.AccessViolation),  # read-only
        (["--write"], "new.bin", None),
        (["--write"], "one.bin", tftp.FileAlreadyExists),
        (["--write", "--overwrite"], "one.bin", None),
        (["--write", "--no-create", "--overwrite"], "new.bin", tftp.FileNotFound),
        (["--write", "--no-create", "--overwrite"], "one.bin", None),
    ]
    for flags, name, refused in cases:
        before = (root / name).read_bytes() if (root / name).exists() else None
        proc, port = _alone(root, *flags)
        try:
            client = tftp.TFTPClient("127.0.0.1", port)
            if refused is None:
                client.put(name, b"uploaded")
                assert (root / name).read_bytes() == b"uploaded"
            else:
                with pytest.raises(refused):
                    client.put(name, b"uploaded")
                after = (root / name).read_bytes() if (root / name).exists() else None
                assert after == before
        finally:
            _stop(proc)
        (root / "one.bin").write_bytes(b"x")  # the next case starts from the same directory
        if (root / "new.bin").exists():
            (root / "new.bin").unlink()


def _hold_a_transfer(port, name="big.bin"):
    """A request that is answered with DATA 1 and never acknowledged: a transfer that stays open."""
    import socket

    from tftp.packet import encode_request

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5)
    sock.sendto(encode_request(1, name, mode="octet"), ("127.0.0.1", port))
    assert sock.recvfrom(2048)[0][:2] == b"\x00\x03"
    return sock


@pytest.mark.parametrize("flag", ["--max-sessions", "--max-per-client"])
def test_a_session_bound_alone_refuses_the_request_over_it(root, flag):
    proc, port = _alone(root, flag, "1", "-t", "30")
    holder = _hold_a_transfer(port)
    try:
        with pytest.raises(tftp.RemoteError):
            tftp.TFTPClient("127.0.0.1", port, timeout=2.0, retries=1).get("one.bin")
    finally:
        holder.close()
        _stop(proc)


def test_timeout_and_retries_decide_how_often_an_unanswered_block_is_sent(root):
    import socket

    from tftp.packet import encode_request

    proc, port = _alone(root, "-t", "0.5", "-r", "2")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(3)
            sock.sendto(encode_request(1, "big.bin", mode="octet"), ("127.0.0.1", port))
            blocks = 0
            try:
                while True:  # until the server has been silent for three seconds
                    if sock.recvfrom(2048)[0][:2] == b"\x00\x03":
                        blocks += 1
            except socket.timeout:
                pass
        assert blocks == 3  # the block, then two retransmissions
    finally:
        _stop(proc)


def test_http_alone_serves_what_the_origin_holds(tmp_path):
    import http.server

    class Origin(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"from the origin" if self.path == "/pxe/hello.txt" else b""
            self.send_response(200 if body else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Origin)
    thread = threading.Thread(target=origin.serve_forever, daemon=True)
    thread.start()
    proc, port = _serve_subprocess(
        [
            "serve",
            "--http",
            "http://127.0.0.1:%d/pxe/" % origin.server_address[1],
            "-l",
            "127.0.0.1",
            "-p",
            "0",
        ]
    )
    try:
        client = tftp.TFTPClient("127.0.0.1", port)
        assert client.get("hello.txt") == b"from the origin"
        with pytest.raises(tftp.FileNotFound):
            client.get("absent.txt")
    finally:
        _stop(proc)
        origin.shutdown()
        origin.server_close()
