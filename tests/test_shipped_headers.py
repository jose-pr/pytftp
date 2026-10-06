"""The shipped ``AGENTS.md`` headers stay complete, listed and true.

``src/tftp/AGENTS.md`` is the top header: every name the root exports with its signature and one
sentence. A topic lives whole in the header beside the code that implements it. These tests pin what
makes the split trustworthy: every public name is in the header of its module, every header is listed by
the one above it, none outgrows its limit, every signature a header prints is the live one, and each
header reaches the wheel.
"""

from __future__ import annotations

import ast
import fnmatch
import importlib
import inspect
import re
from pathlib import Path

import pytest

from surface import EXPECTED

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "tftp"
TOP = PACKAGE / "AGENTS.md"
SUBHEADERS = sorted(p for p in PACKAGE.rglob("AGENTS.md") if p != TOP)

#: The most lines a header may have: the top header ends under 350, each package header under 300, and
#: the repo-root file under 120. Past a limit, detail moves down or out.
TOP_MAX_LINES = 349
SUB_MAX_LINES = 299
ROOT_MAX_LINES = 119


def _text(path):
    return path.read_text(encoding="utf-8")


def _lines(path):
    return _text(path).splitlines()


def _inside_package(path):
    """The path a reader of the installed package uses: ``tftp/client/AGENTS.md``."""
    return "tftp/" + path.relative_to(PACKAGE).as_posix()


# -- sizes and the opening -----------------------------------------------------------------------


def test_the_top_header_is_not_over_its_limit():
    assert len(_lines(TOP)) <= TOP_MAX_LINES


@pytest.mark.parametrize("path", SUBHEADERS, ids=_inside_package)
def test_a_package_header_is_not_over_its_limit(path):
    assert len(_lines(path)) <= SUB_MAX_LINES


def test_there_are_package_headers():
    # A split that left nothing below would make the limits above vacuous.
    assert {p.parent.name for p in SUBHEADERS} >= {
        "client",
        "server",
        "options",
        "backends",
        "relay",
        "capture",
        "packet",
        "path",
        "cli",
        "transfer",
    }


@pytest.mark.parametrize("path", SUBHEADERS, ids=_inside_package)
def test_a_package_header_opens_as_one_and_names_the_top_header(path):
    head = "\n".join(_lines(path)[:12])
    assert head.startswith("# `tftp."), path
    assert "public API header" in head
    assert "`tftp/AGENTS.md`" in " ".join(head.split())


@pytest.mark.parametrize("path", [TOP] + SUBHEADERS, ids=_inside_package)
def test_a_header_has_no_repository_link(path):
    # An installed consumer has no repository: nothing relative, nothing into private notes.
    text = _text(path)
    assert not re.search(r"\]\((?!https?://)[^)]*\)", text), "a relative link"
    assert "." + "agents" not in text and "CHANGELOG.md" not in text


# -- the names -------------------------------------------------------------------------------------


def _sections(path):
    """``[(heading, body)]`` of the ``##`` sections of a header."""
    parts = re.split(r"^## (.*)$", _text(path), flags=re.M)
    return [("", parts[0])] + [(parts[i], parts[i + 1]) for i in range(1, len(parts), 2)]


def _spans(text):
    """The code spans and the fenced lines of a text."""
    return re.findall(r"`([^`\n]+)`", text) + [l for l in text.splitlines() if l and l[0] not in " #|-*"]


def _names_in(spans, name):
    pattern = r"(?<![\w.])(?:tftp(?:\.\w+)*\.)?%s(?!\w)" % re.escape(name)
    return any(re.search(pattern, span) for span in spans)


def _heading_modules(heading):
    return re.findall(r"`(tftp(?:\.\w+)*)", heading)


def _owned_spans(module):
    """The code of every section, in any header, whose heading names *module*; the root's is the top's."""
    texts = []
    for path in [TOP] + SUBHEADERS:
        for heading, body in _sections(path):
            if heading.startswith("Where names live"):
                continue
            if module == "tftp" and path == TOP or "`%s`" % module in heading or "`%s," % module in heading:
                texts.append(body)
    return _spans("\n".join(texts))


def _public_names():
    out = []
    for module in EXPECTED:
        live = importlib.import_module(module)
        out += [(module, name) for name in live.__all__]
    return out


@pytest.mark.parametrize("module,name", _public_names())
def test_every_public_name_is_in_the_header_of_its_module(module, name):
    spans = _owned_spans(module)
    assert _names_in(spans, name), "%s.%s is in __all__ but in no code span of a section naming `%s`" % (
        module,
        name,
        module,
    )


def test_the_set_of_public_modules_is_the_pinned_surface():
    assert all(importlib.import_module(m).__all__ for m in EXPECTED)


# -- the table of headers --------------------------------------------------------------------------


@pytest.mark.parametrize("path", SUBHEADERS, ids=_inside_package)
def test_every_package_header_is_in_the_top_headers_table(path):
    rows = [line for line in _lines(TOP) if line.startswith("|")]
    assert any(
        "`%s`" % _inside_package(path) in row for row in rows
    ), "%s is not named in a table row of the top header" % _inside_package(path)


def test_every_header_in_the_top_headers_table_exists():
    named = re.findall(r"^\| `(tftp/(?:\w+/)?AGENTS\.md)` \|", _text(TOP), flags=re.M)
    assert sorted(named) == sorted(["tftp/AGENTS.md"] + [_inside_package(p) for p in SUBHEADERS])


# -- the wheel -------------------------------------------------------------------------------------


def test_every_header_is_inside_what_the_wheel_ships():
    """The wheel packs ``src/tftp`` whole, minus the excluded patterns, and the sdist leaves out the root file."""
    config = _text(ROOT / "pyproject.toml")
    wheel = config.split("[tool.hatch.build.targets.wheel]")[1].split("\n[")[0]
    sdist = config.split("[tool.hatch.build.targets.sdist]")[1].split("\n[")[0]
    assert 'packages = ["src/tftp"]' in wheel
    excluded = re.findall(r'"([^"]+)"', next(l for l in wheel.splitlines() if l.startswith("exclude")))
    for path in [TOP] + SUBHEADERS:
        assert not any(fnmatch.fnmatch(path.name, pattern) for pattern in excluded), path
    skipped = re.findall(r'"([^"]+)"', next(l for l in sdist.splitlines() if l.startswith("exclude")))
    assert "/AGENTS.md" in skipped and not any("AGENTS" in e and e != "/AGENTS.md" for e in skipped)


def test_the_installed_wheel_check_covers_every_header():
    text = _text(ROOT / "tests" / "integration" / "smoke_installed.py")
    assert "AGENTS.md" in text and "iterdir" in text


# -- the signature probe ---------------------------------------------------------------------------

_FENCE = re.compile(r"^```python\n(.*?)^```", re.S | re.M)
_ENTRY = re.compile(r"\A(?:async )?([A-Za-z_][\w.]*)\((.*)\)\s*(?:->.*)?\Z", re.S)

_KINDS = {
    inspect.Parameter.POSITIONAL_ONLY: "positional_only",
    inspect.Parameter.POSITIONAL_OR_KEYWORD: "positional",
    inspect.Parameter.VAR_POSITIONAL: "var_positional",
    inspect.Parameter.KEYWORD_ONLY: "keyword",
    inspect.Parameter.VAR_KEYWORD: "var_keyword",
}


def _entries(block):
    """The signatures of a fenced block: one starts at an unindented line and ends where it balances."""
    entries, current, depth = [], [], 0
    for line in block.splitlines():
        if not current and (not line.strip() or line[0] in " #"):
            continue
        current.append(line)
        depth += line.count("(") - line.count(")")
        if depth <= 0:
            entries.append("\n".join(current))
            current, depth = [], 0
    assert not current, "an unbalanced signature: %r" % current
    return entries


def _printed(args):
    """``[(name, kind, default source or None)]`` of a printed parameter list."""
    spec = ast.parse("def f(%s): pass" % " ".join(args.split())).body[0].args
    positional = spec.posonlyargs + spec.args
    defaults = [None] * (len(positional) - len(spec.defaults)) + list(spec.defaults)
    out = []
    for arg, default in zip(positional, defaults):
        kind = "positional_only" if arg in spec.posonlyargs else "positional"
        out.append((arg.arg, kind, default))
    if spec.vararg:
        out.append((spec.vararg.arg, "var_positional", None))
    for arg, default in zip(spec.kwonlyargs, spec.kw_defaults):
        out.append((arg.arg, "keyword", default))
    if spec.kwarg:
        out.append((spec.kwarg.arg, "var_keyword", None))
    return [(n, k, None if d is None else ast.unparse(d)) for n, k, d in out]


def _live(obj):
    """``[(name, kind, ("=", default) or None)]`` with ``self`` and ``cls`` dropped."""
    out = []
    for p in inspect.signature(obj).parameters.values():
        if p.name in ("self", "cls"):
            continue
        out.append(
            (p.name, _KINDS[p.kind], None if p.default is inspect.Parameter.empty else ("=", p.default))
        )
    return out


def _same_default(printed, live, namespace):
    if printed is None or live is None:
        return printed is None and live is None
    try:
        if bool(eval(printed, dict(namespace)) == live[1]):
            return True
    except Exception:
        pass
    # A factory or a private sentinel prints as <...>: the header says what it stands for.
    return repr(live[1]).startswith("<") or printed == repr(live[1])


def _resolve(modules, dotted):
    for module in modules:
        obj = importlib.import_module(module)
        for part in dotted.split("."):
            obj = getattr(obj, part, None)
            if obj is None:
                break
        if obj is not None:
            return module, obj
    return None, None


def _probe(text):
    """``(checked, problems)`` for every signature the fenced blocks of *text* print."""
    checked, problems = 0, []
    position = 0
    for match in _FENCE.finditer(text):
        headings = re.findall(r"^## (.*)$", text[: match.start()], flags=re.M)
        modules = _heading_modules(headings[-1]) if headings else []
        if not modules:
            problems.append("a signature block under no heading that names its import path")
            continue
        for entry in _entries(match.group(1)):
            parsed = _ENTRY.match(entry)
            if parsed is None:
                problems.append("not a signature: %s" % " ".join(entry.split()))
                continue
            name, args = parsed.groups()
            module, obj = _resolve(modules, name)
            if obj is None:
                problems.append("%s does not exist in %s" % (name, ", ".join(modules)))
                continue
            checked += 1
            namespace = dict(vars(importlib.import_module(module)))
            try:
                printed, live = _printed(args), _live(obj)
            except (SyntaxError, ValueError, TypeError) as exc:
                problems.append("%s: %s" % (name, exc))
                continue
            same = len(printed) == len(live) and all(
                p[0] == l[0] and p[1] == l[1] and _same_default(p[2], l[2], namespace)
                for p, l in zip(printed, live)
            )
            if not same:
                problems.append("%s(%s)" % (name, " ".join(args.split())))
    return checked, problems


def test_every_printed_signature_is_the_live_one():
    checked, bad = 0, []
    for path in [TOP] + SUBHEADERS:
        n, problems = _probe(_text(path))
        checked += n
        bad.extend("%s: %s" % (_inside_package(path), p) for p in problems)
    assert not bad, "signature drift:\n" + "\n".join(bad)
    # A probe that matched nothing would pass whatever the headers say.
    assert checked >= 150, checked


def test_the_probe_sees_a_changed_signature():
    text = (
        "## Server (`tftp.server`)\n\n```python\n"
        "TFTPServerLimits(*, max_idle=60.0)\n"
        "TFTPServerLimits(*, max_request_size=1024, max_filename_length=512, max_options=16,\n"
        "    max_option_length=255, max_sessions_per_client=None, max_duration=None, max_idle=60.0)\n"
        "TFTPServerLimits(max_request_size=1024, max_filename_length=512, max_options=16,\n"
        "    max_option_length=255, max_sessions_per_client=None, max_duration=None, max_idle=60.0)\n"
        "PortRange.parse(table)\n"
        "```\n"
    )
    checked, bad = _probe(text)
    assert checked == 4
    assert len(bad) == 3  # a short list, the keyword-only star missing, a wrong name


def test_the_probe_refuses_a_block_under_no_import_path():
    checked, bad = _probe("## Gotchas\n\n```python\nTFTPClient(host)\n```\n")
    assert checked == 0 and bad


def test_the_probe_sees_a_name_that_does_not_exist():
    checked, bad = _probe("## Client (`tftp.client`)\n\n```python\nNoSuchThing(a)\n```\n")
    assert checked == 0 and "does not exist" in bad[0]
