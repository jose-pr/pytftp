"""A capability whose dependency is absent says which extra to install, and nothing else breaks.

Each test runs a fresh interpreter in which the optional dependency is refused
at import, so the message is read the way a user on a bare install reads it.
"""

from __future__ import annotations

import subprocess
import sys

import pytest


def without(module: str, body: str) -> subprocess.CompletedProcess:
    """Run ``body`` in a new interpreter where importing ``module`` raises ``ImportError``."""
    code = "import sys\nsys.modules[%r] = None\n" % module + body
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)


def test_without_duho_the_command_names_the_extra_and_exits_1():
    done = without("duho", "from tftp.cli import main\nraise SystemExit(main(['--help']))\n")
    assert done.returncode == 1 and done.stdout == ""
    assert done.stderr.strip() == "pytftp: the CLI needs the 'cli' extra -- pip install 'tftp[cli]'"


def test_python_dash_m_without_duho_says_the_same():
    done = without("duho", "import runpy\nrunpy.run_module('tftp', run_name='__main__')\n")
    assert done.returncode == 1 and "'cli' extra" in done.stderr and "Traceback" not in done.stderr


def test_importing_the_command_package_needs_no_duho():
    done = without("duho", "import tftp, tftp.cli\nprint(tftp.cli.main.__name__)\n")
    assert done.returncode == 0 and done.stdout.strip() == "main"


@pytest.mark.parametrize(
    "use",
    [
        "import tftp.path",
        "import tftp\ntftp.TFTPClient('127.0.0.1').path('x')",
        "import tftp\ntftp.TFTPURL.parse('tftp://h/f')\nimport tftp.path",
    ],
    ids=["import", "client-path", "import-after-use"],
)
def test_without_pathlib_next_a_path_names_the_extra(use):
    done = without("pathlib_next", use + "\n")
    assert done.returncode != 0 and "Traceback" in done.stderr
    assert done.stderr.strip().splitlines()[-1] == (
        "ImportError: tftp.path needs the 'path' extra: pip install 'tftp[path]'"
    )


def test_without_pathlib_next_the_rest_of_the_library_works():
    body = (
        "import tftp\n"
        "from tftp.backends import MemoryBackend\n"
        "server = tftp.TFTPServer(MemoryBackend({'f': b'abc'}), host='127.0.0.1', port=0, timeout=0.5).start()\n"
        "client = tftp.TFTPClient('127.0.0.1', server.server_address[1], timeout=0.5)\n"
        "print(client.get('f'))\n"
        "server.close()\n"
    )
    done = without("pathlib_next", body)
    assert done.returncode == 0 and done.stdout.strip() == "b'abc'", done.stderr


def test_without_uritools_the_uri_path_names_the_extra():
    pytest.importorskip("pathlib_next")
    body = (
        "import tftp.path\n"
        "tftp.path.TFTPPath\n"
        "try:\n"
        "    tftp.path.TFTPURIPath\n"
        "except ImportError as exc:\n"
        "    print(exc)\n"
    )
    done = without("uritools", body)
    assert "'path' extra" in done.stdout and "tftp[path]" in done.stdout, (done.stdout, done.stderr)


# -- pktcap is optional: `pip install tftp` alone transfers, serves and relays ---------------------

PKTCAP_LINE = "captures need the 'pktcap' extra: pip install \"tftp[pktcap]\""


def test_importing_the_library_and_its_command_package_needs_no_pktcap():
    body = "import tftp, tftp.capture, tftp.cli\nprint(sorted(n for n, m in sys.modules.items() if m is not None and n.startswith('pktcap')))\n"
    done = without("pktcap", body)
    assert done.returncode == 0 and done.stdout.strip() == "[]", done.stderr


@pytest.mark.parametrize(
    "call",
    [
        "tftp.capture.dissect_tftp(bytes(4))",
        "tftp.capture.register_tftp_dissector()",
        "tftp.capture.pktcap_plugin(object())",
        "tftp.capture.replay_transfers('boot.pcap', '127.0.0.1')",
        "tftp.capture.analyze('boot.pcap')",
        "tftp.capture.trace_to(object())",
        "tftp.capture.follow_transfers([], tftp.capture.FlowTracker())",
    ],
    ids=["dissect", "register", "plugin", "replay", "analyze-path", "trace_to", "follow"],
)
def test_a_capture_function_without_pktcap_raises_the_one_line(call):
    done = without("pktcap", "import tftp.capture\n%s\n" % call)
    assert done.returncode != 0
    assert done.stderr.strip().splitlines()[-1] == "ImportError: " + PKTCAP_LINE


def test_analyze_of_datagrams_needs_no_pktcap():
    body = "import tftp.capture\nprint(tftp.capture.analyze([]))\n"
    done = without("pktcap", body)
    assert done.returncode == 0 and done.stdout.strip() == "Analysis(events=[], transfers=[])", done.stderr


def _command(argv, cwd, before=""):
    """Run ``main(argv)`` in a child where pktcap is refused and binding a socket is a failure."""
    body = (
        "import socket\n"
        "def refuse(*a, **k):\n"
        "    raise AssertionError('a socket was bound')\n"
        "socket.socket.bind = refuse\n"
        "%s\nfrom tftp.cli import main\nraise SystemExit(main(%r))\n" % (before, argv)
    )
    code = "import sys\nsys.modules['pktcap'] = None\n" + body
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, cwd=cwd)


@pytest.mark.parametrize(
    "argv",
    [
        ["capture", "--input", "boot.pcap"],
        ["replay", "--input", "boot.pcap", "--to", "127.0.0.1"],
        ["-v", "capture", "--help"],
        ["get", "127.0.0.1", "f", "--pcap", "out.pcap"],
        ["put", "127.0.0.1", "f", "--pcap", "out.pcap"],
        ["serve", ".", "-l", "127.0.0.1", "-p", "0", "--pcap", "out.pcap"],
        ["relay", "127.0.0.1", "-l", "127.0.0.1", "-p", "0", "--pcap", "out.pcap"],
    ],
    ids=["capture", "replay", "capture-help", "get-pcap", "put-pcap", "serve-pcap", "relay-pcap"],
)
def test_a_command_that_needs_pktcap_names_the_extra_and_binds_nothing(argv, tmp_path):
    (tmp_path / "f").write_bytes(b"x")
    done = _command(argv, tmp_path)
    assert (done.returncode, done.stdout, done.stderr) == (1, "", PKTCAP_LINE + "\n")
    assert not (tmp_path / "out.pcap").exists()


def test_the_other_commands_run_without_pktcap(tmp_path):
    (tmp_path / "f").write_bytes(b"abc" * 100)
    before = (
        "import tftp, tftp.relay\n"
        "from tftp.options import LISTING_OPTIONS, STANDARD_OPTIONS, TFTPServerOptions\n"
        "listing = TFTPServerOptions(allowed=STANDARD_OPTIONS | LISTING_OPTIONS)\n"
        "server = tftp.TFTPServer('.', host='127.0.0.1', port=0, writable=True, timeout=0.5, options=listing)\n"
        "socket.socket.bind = socket.socket.__mro__[1].bind\n"
        "server.start()\n"
        "port = str(server.server_address[1])\n"
        "tftp.TFTPServer.serve_forever = lambda self: None\n"
        "tftp.relay.TFTPRelay.serve_forever = lambda self: None\n"
        "from tftp.cli import main\n"
        "assert main(['get', '127.0.0.1', 'f', 'got', '-p', port, '-q']) == 0\n"
        "assert open('got', 'rb').read() == b'abc' * 100\n"
        "assert main(['put', '127.0.0.1', 'got', 'up', '-p', port, '-q']) == 0\n"
        "assert open('up', 'rb').read() == b'abc' * 100\n"
        "assert main(['ls', '127.0.0.1', '-p', port]) == 0\n"
        "assert main(['serve', '.', '-l', '127.0.0.1', '-p', '0']) == 0\n"
        "assert main(['relay', '127.0.0.1', '-l', '127.0.0.1', '-p', '0']) == 0\n"
        "server.close()\n"
    )
    done = _command(["--version"], tmp_path, before)
    assert done.returncode == 0, done.stderr


def test_the_manifest_requires_netimps_alone_and_pktcap_is_an_extra_dev_installs():
    import pathlib
    import re

    text = (pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    required = re.search(r"^dependencies = \[(.*?)\]", text, re.M | re.S).group(1)
    assert re.findall(r'"([A-Za-z][\w.-]*)', required) == ["netimps"]
    extras = dict(re.findall(r"^(\w+) = \[(.*?)\]", text.split("[project.optional-dependencies]")[1], re.M))
    assert re.findall(r'"([A-Za-z][\w.-]*)', extras["pktcap"]) == ["pktcap"]
    assert "pktcap" in extras["dev"].replace("tftp[", "").split("]")[0].split(",")
    # The range of duho is the one pktcap's own `cli` extra declares, so the two commands build together.
    assert "duho>=0.7.0,<0.8" in extras["cli"]
