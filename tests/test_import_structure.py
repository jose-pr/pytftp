"""How the package is laid out: its loggers, and (below) its modules and the way they import each other."""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest

import tftp

_SRC = pathlib.Path(tftp.__file__).parent

#: The loggers a caller filters on. They are named for the role and not for the
#: module, so a split or a rename never moves one; ``getLogger(__name__)`` would.
_LIBRARY_LOGGERS = {"tftp.backends", "tftp.client", "tftp.relay", "tftp.server"}

#: The command line logs under the package's own name.
_COMMAND_LOGGER = "tftp"


def _get_logger_calls():
    """``(path relative to the package, argument)`` of every ``getLogger(...)`` call."""
    found = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "getLogger"
            ):
                argument = node.args[0] if node.args else None
                value = argument.value if isinstance(argument, ast.Constant) else ast.dump(argument)
                found.append((path.relative_to(_SRC).as_posix(), value))
    return found


def test_the_library_creates_exactly_the_named_loggers_and_only_in_one_module():
    library = [(path, name) for path, name in _get_logger_calls() if not path.startswith("cli/")]
    assert sorted(name for _, name in library) == sorted(_LIBRARY_LOGGERS)
    assert {path for path, _ in library} == {"_loggers.py"}


def test_the_command_line_logs_under_the_package_name():
    commands = [(path, name) for path, name in _get_logger_calls() if path.startswith("cli/")]
    assert commands and {name for _, name in commands} == {_COMMAND_LOGGER}


def test_importing_every_module_creates_no_logger_beyond_the_named_ones():
    pytest.importorskip("pathlib_next")  # the path and cli modules need their extras
    pytest.importorskip("duho")
    code = (
        "import importlib, logging, pathlib, tftp\n"
        "root = pathlib.Path(tftp.__file__).parent\n"
        "for path in sorted(root.rglob('*.py')):\n"
        "    parts = list(path.relative_to(root).with_suffix('').parts)\n"
        "    if parts[-1] == '__init__':\n"
        "        parts.pop()\n"
        "    if parts != ['__main__']:\n"
        "        importlib.import_module('.'.join(['tftp'] + parts))\n"
        "print(sorted(n for n, l in logging.root.manager.loggerDict.items()\n"
        "             if isinstance(l, logging.Logger) and n.split('.')[0] == 'tftp'))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert ast.literal_eval(out.strip()) == sorted(_LIBRARY_LOGGERS)
