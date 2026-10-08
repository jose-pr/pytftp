"""The README's Python blocks run, against a loopback server, as written.

Only the addresses are substituted (the example host, the served directory, the
port) and a call that would serve forever starts the server and returns. A block
that names something the package does not export fails here.
"""

from __future__ import annotations

import asyncio
import pathlib
import re

import pytest

import tftp
from conftest import PLACEHOLDERS, substitute

pytest.importorskip("pathlib_next")

README = pathlib.Path(__file__).resolve().parents[1] / "README.md"
BLOCKS = re.findall(r"```python\n(.*?)```", README.read_text(encoding="utf-8"), re.S)


def test_the_readme_has_python_blocks_to_run():
    assert len(BLOCKS) >= 6


@pytest.mark.parametrize("index", range(len(BLOCKS)))
def test_a_readme_python_block_runs(index, served):
    server, root, _ = served
    code = substitute(BLOCKS[index], server, root)
    left = [text for text in PLACEHOLDERS if text in code]
    assert left == [], "an example address would reach the network: %r" % left
    namespace = {"__name__": "readme_block"}
    exec(compile(code, "README.md python block %d" % index, "exec"), namespace)
    fetch = namespace.get("fetch")
    if fetch is not None:
        asyncio.run(asyncio.wait_for(fetch(), 30))


def test_the_blocks_moved_real_data(served):
    server, root, _ = served
    block = next(text for text in BLOCKS if "blksize=1428, windowsize=16" in text)
    exec(compile(substitute(block, server, root), "README.md client block", "exec"), {})
    assert pathlib.Path("vmlinuz").read_bytes() == (root / "vmlinuz").read_bytes()
    assert (root / "logs" / "boot.txt").read_bytes() == b"ok\n"


# -- the command lines ------------------------------------------------------------------------

BASH = re.findall(r"```bash\n(.*?)```", README.read_text(encoding="utf-8"), re.S)
#: Every ``pytftp`` command of the README's shell blocks; a ``tcpdump ... |`` in front of one is the capture it reads.
COMMANDS = [
    line.strip()
    for block in BASH
    for line in block.splitlines()
    if line.strip().startswith("pytftp") or "| pytftp" in line
]


def _arguments(line, root, port):
    """The command's argument vector with the README's example addresses pointed at loopback."""
    import shlex

    text = line.split("| pytftp", 1)[1] if "| pytftp" in line else line.split("pytftp", 1)[1]
    points = {"192.0.2.1": "127.0.0.1", "/srv/tftp": str(root), "6969": "0"}
    arguments = [points.get(token, token) for token in shlex.split(text)]
    command = arguments[0]
    if command in ("get", "put", "ls", "replay"):
        arguments += ["-p", str(port)]
    elif command in ("serve", "relay") and "--port" not in arguments:
        arguments += ["-p", "0"]
    if command in ("serve", "relay"):
        arguments += ["-l", "127.0.0.1"]
    return arguments


def test_the_readme_has_command_lines_to_run():
    assert len(COMMANDS) >= 8 and {line.split()[1] for line in COMMANDS if line.startswith("pytftp")} >= {
        "get",
        "put",
        "serve",
        "relay",
    }


@pytest.mark.parametrize("line", COMMANDS)
def test_a_readme_command_line_runs_as_written(line, served):
    import subprocess
    import sys
    import threading
    import time

    pytest.importorskip("duho")
    server, root, tmp_path = served
    port = server.server_address[1]
    arguments = _arguments(line, root, port)
    command = arguments[0]
    work = pathlib.Path.cwd()
    (work / "firmware.bin").write_bytes(b"firmware")
    argv = [sys.executable, "-m", "tftp", *arguments]
    if command in ("get", "put", "ls", "capture", "replay"):
        stdin = None
        if command in ("capture", "replay"):
            pytest.importorskip("pktcap")
            from pktcap import PcapWriter
            from tftp.capture import trace_to

            capture = tmp_path / "wire.pcap"
            with PcapWriter(capture) as writer:
                other = tftp.TFTPServer(root, host="127.0.0.1", port=0, trace=trace_to(writer)).start()
                try:
                    tftp.TFTPClient("127.0.0.1", other.server_address[1], timeout=0.5).get("pxelinux.0")
                finally:
                    other.close()
            if command == "capture":
                stdin = capture.read_bytes()
                argv += ["-p", str(other.server_address[1])]
            else:
                # The README's capture is recorded again against a server that holds the file, and
                # replayed to the served one: both reads succeed.
                arguments = [str(capture) if token == "boot.pcapng" else token for token in arguments]
                argv = [
                    sys.executable,
                    "-m",
                    "tftp",
                    *arguments,
                    "--request-port",
                    str(other.server_address[1]),
                ]
        feed = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
        done = subprocess.run(argv, capture_output=True, timeout=60, **feed)
        assert done.returncode == 0, done.stderr.decode()
        assert b"Traceback" not in done.stderr
        if command == "get":
            assert (work / arguments[2]).read_bytes() == (root / arguments[2]).read_bytes()
        if command == "put":
            assert (root / arguments[2]).read_bytes() == b"firmware"
        if command == "replay":
            assert b"replayed 1 transfers, 0 failed, 0 skipped" in done.stderr
        return
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    lines = []
    reader = threading.Thread(target=lambda: lines.extend(iter(proc.stderr.readline, "")), daemon=True)
    reader.start()
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and proc.poll() is None:
            if any(("serving" in text or "relaying" in text) for text in lines):
                break
            time.sleep(0.05)
        assert proc.poll() is None, "".join(lines)
        assert any(("serving" in text or "relaying" in text) for text in lines), "".join(lines)
    finally:
        proc.terminate()
        proc.wait(10)
        reader.join(10)
        proc.stdout.close()
        proc.stderr.close()
