from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

import pytest

pytest.importorskip("duho")

from tftp.cli import run  # noqa: E402


def _port(server) -> str:
    return str(server.server_address[1])


def test_get_to_file_and_json(root, make_server, tmp_path_factory, capsys):
    server = make_server(root)
    out = tmp_path_factory.mktemp("out") / "copy.bin"
    code = run(["get", "127.0.0.1", "big.bin", str(out), "-p", _port(server), "--json", "-w", "4"])
    assert code in (None, 0)
    assert out.read_bytes() == (root / "big.bin").read_bytes()
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] and report["bytes"] == 300_001 and report["windowsize"] == 4


def test_get_default_name_is_basename(root, make_server, tmp_path_factory, monkeypatch):
    server = make_server(root)
    tmp_path = tmp_path_factory.mktemp("cwd")
    monkeypatch.chdir(tmp_path)
    assert run(["get", "127.0.0.1", "sub/nested.bin", "-p", _port(server)]) in (None, 0)
    assert (tmp_path / "nested.bin").read_bytes() == b"nested"


def test_get_missing_file_exits_1(root, make_server, tmp_path_factory, capsys):
    server = make_server(root)
    tmp_path = tmp_path_factory.mktemp("out")
    assert run(["get", "127.0.0.1", "nope", str(tmp_path / "x"), "-p", _port(server)]) == 1
    assert "FILE_NOT_FOUND" in capsys.readouterr().err
    assert not (tmp_path / "x").exists()


def test_put_and_errors(root, make_server, tmp_path_factory):
    server = make_server(root, writable=True)
    tmp_path = tmp_path_factory.mktemp("src")
    src = tmp_path / "up.bin"
    src.write_bytes(os.urandom(3000))
    assert run(["put", "127.0.0.1", str(src), "-p", _port(server), "--no-options"]) in (None, 0)
    assert (root / "up.bin").read_bytes() == src.read_bytes()
    assert run(["put", "127.0.0.1", str(tmp_path / "missing"), "-p", _port(server)]) == 2
    assert run(["put", "127.0.0.1", str(src), "-p", _port(server)]) == 1  # exists


def test_bad_blksize_is_a_usage_error(capsys):
    assert run(["get", "127.0.0.1", "f", "-b", "4"]) == 2
    assert "blksize" in capsys.readouterr().err


def test_serve_not_a_directory(tmp_path):
    assert run(["serve", str(tmp_path / "nope")]) == 2


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
        client = tftp.Client("127.0.0.1", port)
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
    assert run(["get", url + "sub/nested.bin", str(out / "n.bin"), "--trace", "--pcap", str(pcap)]) in (
        None,
        0,
    )
    assert (out / "n.bin").read_bytes() == b"nested"
    trace = capsys.readouterr().err
    assert "RRQ 'sub/nested.bin' octet" in trace and "DATA 1 (6 bytes)" in trace
    from tftp.capture import analyze

    (transfer,) = analyze(pcap, ports=[server.server_address[1]]).transfers
    assert transfer.complete and transfer.data() == b"nested"
    (out / "up.txt").write_bytes(b"a\nb\n")
    assert run(["put", url + "up-url.txt;mode=netascii", str(out / "up.txt")]) in (None, 0)
    assert (root / "up-url.txt").read_bytes() == b"a\nb\n"
    assert run(["get", "127.0.0.1"]) == 2  # no file named


def test_compat_profile(root, make_server, tmp_path_factory, capsys):
    server = make_server(root)
    target = tmp_path_factory.mktemp("compat") / "513.bin"
    assert run(["get", "127.0.0.1", "513.bin", str(target), "-p", _port(server), "--compat", "legacy"]) in (
        None,
        0,
    )
    assert target.read_bytes() == (root / "513.bin").read_bytes()
    assert "blksize 512" in capsys.readouterr().err  # legacy sends a plain RFC 1350 request


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
        assert tftp.Client("127.0.0.1", relay_port).get("513.bin") == (root / "513.bin").read_bytes()
        summary = json.loads(relay.stdout.readline())
        assert summary["filename"] == "513.bin" and summary["reason"] == "complete"
    finally:
        _stop(relay)
    proxy, proxy_port = _serve_subprocess(["serve", "--upstream", target, "-l", "127.0.0.1", "-p", "0"])
    try:
        client = tftp.Client("127.0.0.1", proxy_port, blksize=None)
        assert client.get("big.bin") == (root / "big.bin").read_bytes()
    finally:
        _stop(proxy)


def test_relay_needs_a_destination():
    assert run(["relay"]) == 2
    assert run(["relay", "--route-subnet", "nonsense"]) == 2


def test_capture_command(root, make_server, tmp_path_factory, capsys):
    from tftp.capture import PcapWriter

    out = tmp_path_factory.mktemp("cap")
    pcap = out / "server.pcap"
    with PcapWriter(pcap) as writer:
        server = make_server(root, trace=writer)
        port = _port(server)
        client_for_cli = __import__("tftp").Client("127.0.0.1", int(port))
        client_for_cli.get("1428x3.bin")
        try:
            client_for_cli.get("missing")
        except Exception:
            pass
        server.stop()
    assert run(["capture", str(pcap), "-p", port, "--filter", "op=RRQ,ERROR"]) in (None, 0)
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 3 and "RRQ '1428x3.bin'" in lines[0] and "ERROR 1" in lines[-1]
    assert run(["capture", str(pcap), "-p", port, "--json", "--no-packets", "--transfers"]) in (None, 0)
    records = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert [r["transfer"]["filename"] for r in records] == ["1428x3.bin", "missing"]
    assert records[0]["transfer"]["complete"] and records[1]["transfer"]["error"]["code"] == 1
    target = out / "extracted"
    assert run(["capture", str(pcap), "-p", port, "--no-packets", "--extract", str(target)]) in (None, 0)
    (written,) = list(target.iterdir())
    assert written.name.endswith("-1428x3.bin") and written.read_bytes() == (root / "1428x3.bin").read_bytes()


def test_capture_errors(tmp_path):
    assert run(["capture"]) == 2
    assert run(["capture", str(tmp_path / "missing.pcap")]) == 2
    (tmp_path / "junk.pcap").write_bytes(b"not a capture at all")
    assert run(["capture", str(tmp_path / "junk.pcap")]) == 2
    assert run(["capture", str(tmp_path / "junk.pcap"), "--filter", "colour=red"]) == 2


def test_serve_deployment_flags(root):
    import tftp

    (root / "127.0.0.1").mkdir()
    (root / "127.0.0.1" / "Menu.CFG").write_bytes(b"per-client menu")
    args = ["serve", str(root), "-l", "127.0.0.1", "-p", "0", "--per-client", "--ignore-case"]
    args += ["--remap", "^pxelinux/=", "--port-range", "45100:45120"]
    proc, port = _serve_subprocess(args)
    try:
        events = []
        client = tftp.Client("127.0.0.1", port, trace=events.append)
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
    assert run(["serve", str(tmp_path), *args]) == 2
