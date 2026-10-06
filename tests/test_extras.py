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
