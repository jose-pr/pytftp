"""The docs' Python blocks and the example scripts, run as written against loopback.

A docs block runs with only its addresses substituted (see ``conftest.substitute``),
or carries ``<!-- not run: <reason> -->`` on the line above its fence, and then
only its imports are executed and every name it uses is resolved. The example
scripts run as subprocesses: the client one against a server, the serving ones
until a client has fetched from them.
"""

from __future__ import annotations

import ast
import pathlib
import re
import socket
import subprocess
import sys
import threading
import time
import types

import pytest

import tftp
from conftest import HAS_IPV6, PLACEHOLDERS, substitute

pytest.importorskip("pathlib_next")

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGES = sorted(path for path in (ROOT / "docs").glob("*.md") if path.stem != "changelog")
FENCE = re.compile(r"(?:<!-- not run: (?P<why>[^\n]*?) -->[ \t]*\n)?```python\n(?P<code>.*?)```", re.S)


def blocks(page: pathlib.Path):
    """``[(code, reason or None)]`` for each Python block of a docs page, in order."""
    return [(m.group("code"), m.group("why")) for m in FENCE.finditer(page.read_text(encoding="utf-8"))]


CASES = [(page, index) for page in PAGES for index in range(len(blocks(page)))]
IDS = ["%s-%d" % (page.stem, index) for page, index in CASES]
RUNNABLE = [(page, index) for page, index in CASES if blocks(page)[index][1] is None]
RUNNABLE_IDS = ["%s-%d" % (page.stem, index) for page, index in RUNNABLE]


def _imports(code: str) -> str:
    """The import statements of ``code``, as source."""
    tree = ast.parse(code)
    return "\n".join(
        ast.unparse(node) for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
    )


def _page_namespace(page: pathlib.Path, upto: int) -> dict:
    """The names the page's blocks before ``upto`` import, which a later block may rely on."""
    namespace: dict = {"__name__": "docs_block", "tftp": tftp}
    for code, _ in blocks(page)[:upto]:
        exec(compile(_imports(code), str(page), "exec"), namespace)
    return namespace


def test_the_docs_have_python_blocks_and_most_of_them_run():
    reasons = [why for page in PAGES for _, why in blocks(page)]
    assert len(reasons) >= 15 and sum(1 for why in reasons if why is None) >= 8
    assert all(why is None or len(why.split()) >= 3 for why in reasons), "a reason says why in words"
    marked = sum(page.read_text(encoding="utf-8").count("<!-- not run") for page in PAGES)
    assert marked == sum(1 for why in reasons if why), "a marker that is not on the line above a fence"


@pytest.mark.parametrize("page, index", CASES, ids=IDS)
def test_every_name_a_docs_block_uses_resolves(page, index):
    code, _ = blocks(page)[index]
    tree = ast.parse(code)
    namespace = _page_namespace(page, index + 1)
    problems = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        chain = []
        inner = node
        while isinstance(inner, ast.Attribute):
            chain.append(inner.attr)
            inner = inner.value
        if not isinstance(inner, ast.Name) or inner.id not in namespace:
            continue
        found = namespace[inner.id]
        walked = inner.id
        for attribute in reversed(chain):
            if not isinstance(found, (types.ModuleType, type)):
                break
            if not hasattr(found, attribute):
                problems.append("%s.%s" % (walked, attribute))
                break
            found = getattr(found, attribute)
            walked += "." + attribute
    assert problems == [], "%s block %d names what the package does not have" % (page.name, index)


@pytest.mark.parametrize("page, index", RUNNABLE, ids=RUNNABLE_IDS)
def test_a_docs_block_runs_as_written(page, index, served):
    code, _ = blocks(page)[index]
    server, root, _ = served
    namespace = _page_namespace(page, index)
    code = substitute(code, server, root)
    left = [text for text in PLACEHOLDERS if text in code]
    assert left == [], "an example address would reach the network: %r" % left
    exec(compile(code, "%s python block %d" % (page.name, index), "exec"), namespace)


# -- the example scripts ---------------------------------------------------------------------

EXAMPLES = ROOT / "examples"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_the_fetch_example_downloads_from_a_server(root, make_server, tmp_path):
    server = make_server(root)
    port = str(server.server_address[1])
    done = subprocess.run(
        [sys.executable, str(EXAMPLES / "fetch.py"), "127.0.0.1", "512.bin", port],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "512.bin").read_bytes() == (root / "512.bin").read_bytes()
    assert "512 bytes" in done.stdout


@pytest.fixture
def running_example(tmp_path):
    """``start(script, *arguments)`` runs an example until a line of its output says it is serving."""
    processes = []

    def start(script, *arguments):
        proc = subprocess.Popen(
            [sys.executable, "-u", str(EXAMPLES / script), *arguments],
            cwd=str(tmp_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        lines: list = []
        for stream in (proc.stdout, proc.stderr):
            threading.Thread(target=lambda s=stream: lines.extend(iter(s.readline, "")), daemon=True).start()
        processes.append((proc, lines))
        return proc, lines

    yield start
    for proc, _ in processes:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(10)
        proc.stdout.close()
        proc.stderr.close()


def _until(condition, what, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError("gave up waiting for %s" % what)


def _fetch(port, name, attempts=1):
    client = tftp.TFTPClient("127.0.0.1", port, timeout=1, retries=attempts + 2)
    return client.get(name)


@pytest.mark.skipif(not HAS_IPV6, reason="the example listens on '::'")
def test_the_serve_directory_example_serves_its_directory(root, running_example):
    port = _free_port()
    proc, lines = running_example("serve_directory.py", str(root), str(port))
    _until(
        lambda: any("serving" in line for line in lines) or proc.poll() is not None, "the example to serve"
    )
    assert proc.poll() is None, "".join(lines)
    assert _fetch(port, "513.bin") == (root / "513.bin").read_bytes()
    with pytest.raises(tftp.AccessViolation):
        tftp.TFTPClient("127.0.0.1", port, timeout=1, retries=2).put("up.bin", b"x")


@pytest.mark.skipif(not HAS_IPV6, reason="the example listens on '::'")
def test_the_dynamic_boot_menu_example_names_the_client_and_refuses_uploads(root, running_example):
    port = _free_port()
    proc, lines = running_example("dynamic_boot_menu.py", str(root), str(port))
    client = tftp.TFTPClient("127.0.0.1", port, timeout=1, retries=30)
    menu = client.get("pxelinux.cfg/default")
    assert proc.poll() is None, "".join(lines)
    assert b"ip=127.0.0.1" in menu and menu.startswith(b"DEFAULT linux")
    assert client.get("one.bin") == b"x"
    with pytest.raises(tftp.AccessViolation):
        client.put("up.bin", b"x")
    assert not (root / "up.bin").exists()


def test_every_example_script_has_a_test():
    scripts = {path.name for path in EXAMPLES.glob("*.py")}
    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    assert scripts and all(name in source for name in scripts), scripts
