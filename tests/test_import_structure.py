"""How the package is laid out: its loggers, its modules and the way they import each other."""

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


# -- modules: which are public, how long they are, how they import each other ----------------

#: The most lines a module may have. A module is a unit someone reviews in one
#: sitting; past this it is split along a seam, or named below with the reason.
MAX_MODULE_LINES = 500

#: Modules over the limit, each with why.
_LONG_MODULES = {
    "client/_asyncio.py": (
        "the asyncio client and the datagram protocol and timer driver only it uses; "
        "its methods mirror the blocking client's one for one"
    ),
    "client/_core.py": (
        "the one base both clients share: option building, first-response handling, "
        "stat and probe read as one contract"
    ),
    "server/_sync.py": (
        "the selector lifecycle the relay shares and the blocking server: the loop, "
        "the timer heap and the per-session callbacks share their state"
    ),
}

#: Modules whose import path is a promise, because the package or the role is public.
_PUBLIC = {
    "tftp",
    "tftp.__main__",
    "tftp.backends",
    "tftp.capture",
    "tftp.cli",
    "tftp.client",
    "tftp.exceptions",
    "tftp.options",
    "tftp.packet",
    "tftp.path",
    "tftp.relay",
    "tftp.server",
    # Topic modules: each exports names a caller reads qualified by the module.
    "tftp.listing",
    "tftp.netascii",
    "tftp.transfer",
}

_LEAVING = "leaves for another package with the rest of the capture decoding"
_COMMAND = "a command module of the command line package, which is laid out separately"

#: Modules public by name that are not in the role-based surface, each with why.
_PUBLIC_FOR_NOW = {
    "tftp.capture.frames": _LEAVING,
    "tftp.capture.live": _LEAVING,
    "tftp.capture.pcap": _LEAVING,
    "tftp.cli.capture": _COMMAND,
    "tftp.cli.common": _COMMAND,
    "tftp.cli.handlers": _COMMAND,
    "tftp.cli.relay": _COMMAND,
    "tftp.cli.serve": _COMMAND,
    "tftp.cli.transfer": _COMMAND,
}

#: A name imported from a package ``__init__`` by code that is not itself one,
#: by ``(file, package, name)``, with why. Every other internal import names the
#: module that defines the name.
_FROM_AN_INIT = {
    ("__main__.py", "tftp.cli", "run"): "the entry point's only job is to call the command line's",
    ("backends/_http.py", "tftp", "__version__"): "defined in the root, which has no other module for it",
}

#: A function-local import of a sibling module, by ``(file, imported module)``,
#: with the cycle or the optional dependency that forces it. Every other import
#: of a sibling is at the top of its module.
_LOCAL_IMPORTS = {
    ("backends/_http.py", "tftp.__version__"): "the root defines it after it has imported this package",
    ("client/__init__.py", "tftp.client._asyncio"): (
        "the asyncio twin is bound on first use, so importing the package does not import asyncio"
    ),
    ("server/__init__.py", "tftp.server._asyncio"): (
        "the asyncio twin is bound on first use, so importing the package does not import asyncio"
    ),
    ("client/_sync.py", "tftp.path._local"): (
        "TFTPPath needs the optional path extra, so it is loaded only when a path is asked for"
    ),
    ("path/__init__.py", "tftp.path._uri"): (
        "TFTPURIPath needs uritools, an optional dependency, so it is loaded only when asked for"
    ),
    ("server/_core.py", "tftp.backends._filesystem"): (
        "_filesystem imports server._handler, so a top-level import here runs while the "
        "server package is half built"
    ),
}


def _modules():
    return sorted(_SRC.rglob("*.py"))


def _name(path):
    return path.relative_to(_SRC).as_posix()


def _dotted(path):
    parts = list(path.relative_to(_SRC.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def test_every_module_that_is_not_a_public_path_is_private_by_name():
    public = {
        _dotted(p)
        for p in _modules()
        if not any(part.startswith("_") and part != "__main__" for part in _dotted(p).split(".")[1:])
    }
    declared = _PUBLIC | set(_PUBLIC_FOR_NOW)
    assert public - declared == set(), "start its name with an underscore, or declare it public"
    assert declared - public == set(), "no such public module: remove it from the list"
    assert all(reason.strip() for reason in _PUBLIC_FOR_NOW.values())
    assert not _PUBLIC & set(_PUBLIC_FOR_NOW)


def _package_of(path):
    """The dotted package a module's relative imports start from."""
    parts = list(path.relative_to(_SRC.parent).with_suffix("").parts)
    parts.pop()  # the module itself, or `__init__`
    return parts


def _resolved(path, node):
    package = _package_of(path)
    if node.level:
        base = package[: len(package) - (node.level - 1)]
        return base + (node.module.split(".") if node.module else [])
    return node.module.split(".") if node.module else []


def _is_package(parts):
    return _SRC.parent.joinpath(*parts, "__init__.py").exists()


def _is_module(parts):
    return _SRC.parent.joinpath(*parts).with_suffix(".py").exists() or _is_package(parts)


def test_no_module_imports_a_name_from_a_package_that_re_exports_it():
    """The lazily bound asyncio twins are the one stated exception: they are bound inside the
    packages' own ``__init__`` files, which this test does not read."""
    used = set()
    for path in _modules():
        if path.name == "__init__.py" or _name(path).startswith("cli/"):
            continue  # a package's own re-exports; the command line consumes the public API
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ImportFrom):
                continue
            target = _resolved(path, node)
            if not target or target[0] != "tftp" or not _is_package(target):
                continue
            for alias in node.names:
                if not _is_module(target + [alias.name]):
                    used.add((_name(path), ".".join(target), alias.name))
    assert sorted(used - set(_FROM_AN_INIT)) == [], "import it from the module that defines it"
    assert sorted(set(_FROM_AN_INIT) - used) == [], "no such import: remove it from the list"
    assert all(reason.strip() for reason in _FROM_AN_INIT.values())


def _local_sibling_imports(path):
    """``{imported module}`` for every relative import inside a function."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()

    def visit(node, inside):
        for child in ast.iter_child_nodes(node):
            now = inside or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            if now and isinstance(child, ast.ImportFrom) and child.level:
                base = _resolved(path, child)
                if child.module:
                    found.add(".".join(base))
                else:
                    found.update(".".join(base + [alias.name]) for alias in child.names)
            visit(child, now)

    visit(tree, False)
    return found


def test_no_module_is_over_the_size_limit_without_a_reason():
    lengths = {_name(p): len(p.read_text(encoding="utf-8").splitlines()) for p in _modules()}
    long = {name: n for name, n in lengths.items() if n > MAX_MODULE_LINES}
    unexplained = {name: n for name, n in long.items() if name not in _LONG_MODULES}
    assert unexplained == {}, "split it, or name it in _LONG_MODULES with a reason"
    stale = [name for name in _LONG_MODULES if name not in long]
    assert stale == [], "no longer over the limit: remove it from _LONG_MODULES"
    assert all(reason.strip() for reason in _LONG_MODULES.values())


def test_a_sibling_is_imported_at_the_top_of_a_module_or_the_reason_is_recorded():
    # the command line's own lazy imports belong to the package's layout, which is separate
    used = {
        (_name(p), target)
        for p in _modules()
        if not _name(p).startswith("cli/")
        for target in _local_sibling_imports(p)
    }
    assert sorted(used - set(_LOCAL_IMPORTS)) == [], "lift the import to the top, or record the cycle"
    assert sorted(set(_LOCAL_IMPORTS) - used) == [], "no such function-local import: remove it from the list"
    assert all(reason.strip() for reason in _LOCAL_IMPORTS.values())


def test_importing_the_package_loads_no_optional_dependency():
    code = (
        "import sys, tftp\n"
        "print(sorted(n for n in sys.modules if n.split('.')[0] in ('netimps', 'duho', 'pathlib_next')))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert ast.literal_eval(out.strip()) == []
