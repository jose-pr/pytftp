"""Smoke test for an INSTALLED distribution (run outside pytest, from any cwd).

Release CI installs the built wheel into a fresh venv and runs this, so it
proves what users get: the package imports from site-packages (not a
checkout), its shipped docs are present, and a real loopback transfer works.

    python tests/integration/smoke_installed.py
"""

from __future__ import annotations

import importlib.resources
import os
import tempfile


def main() -> int:
    import tftp

    location = os.path.dirname(tftp.__file__)
    assert "site-packages" in location or "dist-packages" in location, (
        "imported from a checkout: %s" % location
    )
    files = importlib.resources.files("tftp")
    for name in ("AGENTS.md", "README.md", "py.typed"):
        assert files.joinpath(name).is_file(), "missing shipped file %s" % name
    assert not files.joinpath("AGENTS.local.md").is_file()

    payload = os.urandom(100_000)
    with tempfile.TemporaryDirectory() as root:
        with open(os.path.join(root, "f.bin"), "wb") as handle:
            handle.write(payload)
        with tftp.TFTPServer(root, host="127.0.0.1", port=0, writable=True) as server:
            server.start()
            client = tftp.TFTPClient("127.0.0.1", server.server_address[1], windowsize=8)
            assert client.get("f.bin") == payload
            client.put("up.bin", payload)
        with open(os.path.join(root, "up.bin"), "rb") as handle:
            assert handle.read() == payload
    print("tftp %s OK from %s (%d bytes each way)" % (tftp.__version__, location, len(payload)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
