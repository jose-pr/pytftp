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
import tftp.relay

pytest.importorskip("pathlib_next")

README = pathlib.Path(__file__).resolve().parents[1] / "README.md"
BLOCKS = re.findall(r"```python\n(.*?)```", README.read_text(encoding="utf-8"), re.S)
#: Example addresses that must not reach the network.
PLACEHOLDERS = ("example.net", "192.0.2.", "10.0.0.20", "/srv/tftp", "port=69")


@pytest.fixture
def served(tmp_path, monkeypatch):
    """A writable server on a free loopback port, and every server the blocks start."""
    root = tmp_path / "srv"
    for directory in ("images", "pxelinux.cfg", "logs"):
        (root / directory).mkdir(parents=True)
    payload = bytes(range(256)) * 50
    for name in ("images/vmlinuz", "vmlinuz", "pxelinux.0"):
        (root / name).write_bytes(payload)
    (root / "pxelinux.cfg" / "default").write_bytes(b"DEFAULT linux\n")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    created = []

    def remember(cls):
        original = cls.__init__

        def init(self, *args, **kwargs):
            kwargs.setdefault("host", "127.0.0.1")
            kwargs.setdefault("port", 0)
            original(self, *args, **kwargs)
            created.append(self)

        monkeypatch.setattr(cls, "__init__", init)

    remember(tftp.TFTPServer)
    remember(tftp.relay.TFTPRelay)
    server = tftp.TFTPServer(root, writable=True, timeout=0.5).start()
    yield server, root, tmp_path
    for item in created:
        item.close()


def substitute(code, server, root):
    port = str(server.server_address[1])
    pairs = [
        ('"boot.example.net"', '"127.0.0.1", ' + port),
        ("boot.example.net", "127.0.0.1:" + port),
        ('"192.0.2.1"', '"127.0.0.1", ' + port),
        ('"/srv/tftp"', repr(str(root))),
        ('host="::"', 'host="127.0.0.1"'),
        ("port=69", "port=0"),
        ('default="10.0.0.20"', 'default="127.0.0.1:%s"' % port),
        ('"boot.pcapng"', '"relay.pcap"'),
        (".serve_forever()", ".start()"),
    ]
    for old, new in pairs:
        code = code.replace(old, new)
    return code


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
