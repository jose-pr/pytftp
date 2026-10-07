"""Importing the package, and using its blocking half, does not import asyncio.

Each check runs in a fresh interpreter, because this process has long since
imported it.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

import tftp


def run(code: str, *args: str) -> str:
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    return proc.stdout.strip()


def loaded_after(statement: str) -> str:
    return run("import sys\n%s\nprint('asyncio' in sys.modules)" % statement)


@pytest.mark.parametrize(
    "statement",
    ["import tftp", "import tftp.client", "import tftp.server", "import tftp.relay", "import tftp.capture"],
)
def test_a_blocking_import_leaves_asyncio_alone(statement):
    assert loaded_after(statement) == "False"


def test_importing_the_command_line_adds_no_asyncio_import_of_its_own():
    pytest.importorskip("duho")
    assert loaded_after("import tftp.cli") == loaded_after("import duho")


def test_a_blocking_download_leaves_asyncio_alone(tmp_path):
    (tmp_path / "f").write_bytes(b"x" * 5000)
    server = tftp.TFTPServer(str(tmp_path), host="127.0.0.1", port=0, timeout=0.5).start()
    try:
        out = run(
            """
            import sys, tftp
            data = tftp.TFTPClient("127.0.0.1", int(sys.argv[1]), timeout=1).get("f")
            tftp.TFTPServer
            print(len(data), 'asyncio' in sys.modules)
            """,
            str(server.server_address[1]),
        )
    finally:
        server.close()
    assert out == "5000 False"


def test_a_blocking_relay_leaves_asyncio_alone():
    out = run("""
        import sys, tftp.relay
        with tftp.relay.TFTPRelay("127.0.0.1", host="127.0.0.1", port=0) as relay:
            relay.start()
        print('asyncio' in sys.modules)
        """)
    assert out == "False"


def test_a_blocking_server_leaves_asyncio_alone(tmp_path):
    out = run(
        """
        import sys, tftp
        with tftp.TFTPServer(sys.argv[1], host="127.0.0.1", port=0) as server:
            server.start()
        print('asyncio' in sys.modules)
        """,
        str(tmp_path),
    )
    assert out == "False"


@pytest.mark.parametrize(
    "statement",
    [
        "import tftp; tftp.AsyncTFTPClient",
        "import tftp; tftp.AsyncTFTPServer",
        "from tftp import AsyncTFTPClient",
        "from tftp.client import AsyncTFTPClient",
        "from tftp.server import AsyncTFTPServer",
        "from tftp.relay import AsyncTFTPRelay",
        "import tftp.relay; tftp.relay.AsyncTFTPRelay",
    ],
)
def test_touching_an_asyncio_twin_imports_asyncio(statement):
    assert loaded_after(statement) == "True"


def test_a_twin_is_the_one_object_everywhere_and_is_bound_once():
    out = run("""
        import sys, tftp, tftp.client, tftp.server, tftp.relay
        from tftp.client._asyncio import AsyncTFTPClient as defined
        from tftp.server._asyncio import AsyncTFTPServer as served
        from tftp.relay._asyncio import AsyncTFTPRelay as relayed
        assert tftp.AsyncTFTPClient is defined is tftp.client.AsyncTFTPClient
        assert tftp.AsyncTFTPServer is served is tftp.server.AsyncTFTPServer
        assert "AsyncTFTPClient" in vars(tftp) and "AsyncTFTPServer" in vars(tftp.server)
        assert tftp.relay.AsyncTFTPRelay is relayed and "AsyncTFTPRelay" in vars(tftp.relay)
        assert "AsyncTFTPRelay" in tftp.relay.__all__ and "AsyncTFTPRelay" in dir(tftp.relay)
        assert "AsyncTFTPClient" in tftp.__all__ and "AsyncTFTPClient" in dir(tftp)
        assert "AsyncTFTPServer" in dir(tftp.server) and "AsyncTFTPClient" in dir(tftp.client)
        print("ok")
        """)
    assert out == "ok"


def test_an_unknown_name_is_still_an_attribute_error():
    for module in ("tftp", "tftp.client", "tftp.server", "tftp.relay"):
        out = run(
            "import %s as m\ntry:\n    m.NoSuchThing\nexcept AttributeError as exc:\n    print(exc)" % module
        )
        assert "NoSuchThing" in out and module in out
