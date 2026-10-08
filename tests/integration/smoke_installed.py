"""Smoke test for an INSTALLED distribution (run outside pytest, from any cwd).

CI installs the package into a fresh environment, with and without its extras,
and runs this, so it proves what users get: the package imports from
site-packages (not a checkout), its shipped files are present, a real loopback
transfer works, and the ``pytftp`` script either starts or says which extra it
needs.

    python tests/integration/smoke_installed.py            # installed bare
    python tests/integration/smoke_installed.py --extras   # installed with tftp[cli,path,pktcap]
"""

from __future__ import annotations

import importlib.metadata
import importlib.resources
import os
import re
import shutil
import subprocess
import sys
import tempfile


def run(*argv: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(list(argv), capture_output=True, text=True, timeout=120)


def transfer(tftp) -> int:
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
    return len(payload)


def main(argv) -> int:
    extras = "--extras" in argv
    import tftp

    location = os.path.dirname(tftp.__file__)
    assert "site-packages" in location or "dist-packages" in location, (
        "imported from a checkout: %s" % location
    )
    assert importlib.metadata.version("tftp") == tftp.__version__, (
        importlib.metadata.version("tftp"),
        tftp.__version__,
    )
    files = importlib.resources.files("tftp")
    for name in ("AGENTS.md", "README.md", "py.typed"):
        assert files.joinpath(name).is_file(), "missing shipped file %s" % name
    # Every header the top one lists is in the package, and no other one is.
    listed = set(
        re.findall(
            r"^\| `tftp/((?:\w+/)?AGENTS\.md)` \|",
            files.joinpath("AGENTS.md").read_text(encoding="utf-8"),
            re.M,
        )
    )
    shipped = {"AGENTS.md"} | {
        "%s/AGENTS.md" % entry.name
        for entry in files.iterdir()
        if entry.is_dir() and entry.joinpath("AGENTS.md").is_file()
    }
    assert listed == shipped, (sorted(listed), sorted(shipped))
    assert not files.joinpath("AGENTS.local.md").is_file()
    size = transfer(tftp)

    script = shutil.which("pytftp", path=os.path.dirname(sys.executable))
    assert script, "the pytftp script is not installed beside %s" % sys.executable
    for argv_ in ([script, "--version"], [sys.executable, "-m", "tftp", "--version"]):
        done = run(*argv_)
        if extras:
            assert done.returncode == 0 and done.stdout.strip() == "pytftp %s" % tftp.__version__, (
                argv_,
                done,
            )
        else:
            assert done.returncode == 1 and done.stdout == "", (argv_, done)
            assert "'cli' extra" in done.stderr and "Traceback" not in done.stderr, (argv_, done.stderr)

    packets = run(sys.executable, "-c", "import tftp.capture; tftp.capture.dissect_tftp(bytes(4))")
    absent = run(sys.executable, "-c", "import pktcap")
    if extras:
        assert absent.returncode == 0, absent.stderr
        for command in ("capture", "replay"):
            done = run(script, command, "--help")
            assert done.returncode == 0 and "usage" in done.stdout.lower(), (command, done)
    else:
        assert absent.returncode != 0, "pktcap is installed in the bare environment"
        assert packets.returncode != 0 and 'pip install "tftp[pktcap]"' in packets.stderr, packets.stderr

    path = run(sys.executable, "-c", "import tftp.path")
    if extras:
        assert path.returncode == 0, path.stderr
    else:
        assert path.returncode != 0 and "needs the 'path' extra" in path.stderr, path.stderr
    print(
        "tftp %s OK from %s (%d bytes each way, %s the extras)"
        % (tftp.__version__, location, size, "with" if extras else "without")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
