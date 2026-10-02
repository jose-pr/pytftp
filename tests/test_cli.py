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
