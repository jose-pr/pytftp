"""The settings file of the ``pytftp`` command: where it comes from, what it may hold and what it refuses.

Each test runs the command line itself. A command that would start serving or capturing is replaced by one
that records what it was given and stops, so the file's effect is read from the values the command holds.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

pytest.importorskip("duho")

from tftp.cli import main  # noqa: E402
from tftp.cli._settings import MAX_OCTETS, scan_root_options  # noqa: E402
from tftp.cli.serve import Serve  # noqa: E402

PKTCAP_LINE = "captures need the 'pktcap' extra: pip install \"tftp[pktcap]\""
MARKER = "SECRETMARKER_7f3a"

SERVE_INI = "[serve]\nport = 6969\nmax_sessions = 7\n"


def run(capsys, *argv):
    """``(status, stdout, stderr)`` of ``main(argv)``; a usage error's ``SystemExit`` is its status."""
    try:
        status = main(list(argv))
    except SystemExit as stop:
        status = stop.code
    captured = capsys.readouterr()
    return status, captured.out, captured.err


def write(directory, name, text):
    path = pathlib.Path(directory) / name
    path.write_bytes(text.encode("utf-8"))
    return str(path)


def default_file(monkeypatch, directory, text):
    """The user's own ``pytftp/pytftp.ini``, written into ``directory`` where both variables point."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(directory))
    monkeypatch.setenv("APPDATA", str(directory))
    folder = pathlib.Path(directory) / "pytftp"
    folder.mkdir(exist_ok=True)
    return write(folder, "pytftp.ini", text)


@pytest.fixture
def stopped(monkeypatch):
    """The ``serve`` commands that were built: each stops before it binds, status 2 and ``error: stopped``."""
    built = []

    def stop(self):
        built.append(self)
        raise ValueError("stopped")

    monkeypatch.setattr(Serve, "_server", stop)
    return built


def refused(capsys, *argv):
    """The status, and the one line, of a run that must be refused as a usage error."""
    status, out, err = run(capsys, *argv)
    assert status == 2 and out == ""
    lines = err.splitlines()
    assert len(lines) == 1 and lines[0].startswith("error: "), err
    return lines[0]


# -- what a file sets ----------------------------------------------------------------------------


def test_the_table_of_a_command_sets_its_options_and_the_argument_wins(capsys, tmp_path, stopped):
    path = write(tmp_path, "a.ini", SERVE_INI)
    status, _, err = run(capsys, "-c", path, "serve", str(tmp_path), "--max-sessions", "9")
    assert (status, err) == (2, "error: stopped\n")
    (built,) = stopped
    assert (built.port, built.max_sessions) == (6969, 9)


def test_a_key_of_the_root_is_accepted_at_the_top_level(capsys, tmp_path, stopped):
    path = write(tmp_path, "a.ini", "[DEFAULT]\nverbose = 1\n\n[serve]\nport = 6969\n")
    assert run(capsys, "-c", path, "serve", str(tmp_path))[2] == "error: stopped\n"
    assert stopped[0].port == 6969


def _toml_readable():
    if sys.version_info >= (3, 11):
        return True
    try:
        import tomli  # noqa: F401
    except ImportError:
        return False
    return True


def _yaml_readable():
    try:
        import yaml  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.parametrize(
    "name, text",
    [
        ("a.ini", SERVE_INI),
        ("a.cfg", SERVE_INI),
        ("a.json", json.dumps({"serve": {"port": 6969, "max_sessions": 7}})),
        pytest.param(
            "a.toml",
            "[serve]\nport = 6969\nmax_sessions = 7\n",
            marks=pytest.mark.skipif(
                not _toml_readable(), reason="no TOML reader (Python < 3.11 without tomli)"
            ),
        ),
        pytest.param(
            "a.yaml",
            "serve:\n  port: 6969\n  max_sessions: 7\n",
            marks=pytest.mark.skipif(not _yaml_readable(), reason="PyYAML is not installed"),
        ),
    ],
)
def test_every_format_duho_reads_sets_the_same_options(capsys, tmp_path, stopped, name, text):
    path = write(tmp_path, name, text)
    assert run(capsys, "-c", path, "serve", str(tmp_path))[2] == "error: stopped\n"
    assert (stopped[0].port, stopped[0].max_sessions) == (6969, 7)


def test_a_table_for_capture_sets_load_and_format(capsys, tmp_path, monkeypatch):
    pytest.importorskip("pktcap")
    from tftp.cli.capture import CaptureCmd

    seen = []

    def record(self):
        seen.append(self)
        return 0

    monkeypatch.setattr(CaptureCmd, "__call__", record)
    path = write(tmp_path, "a.ini", "[capture]\nload = tftp.capture\nformat = json\n")
    assert run(capsys, "-c", path, "capture", "--input", "x.pcap")[0] == 0
    (built,) = seen
    assert built.format == "json" and built.load == ["tftp.capture"]
    # the argument over the file
    assert run(capsys, "-c", path, "capture", "--input", "x.pcap", "--format", "text")[0] == 0
    assert seen[1].format == "text"


# -- where the file comes from ---------------------------------------------------------------------


def test_the_variable_names_the_file_and_the_option_wins_over_it(capsys, tmp_path, monkeypatch, stopped):
    by_variable = write(tmp_path, "v.ini", "[serve]\nport = 1111\n")
    by_option = write(tmp_path, "o.ini", "[serve]\nport = 2222\n")
    monkeypatch.setenv("PYTFTP_CONFIG", by_variable)
    run(capsys, "serve", str(tmp_path))
    assert stopped[-1].port == 1111
    run(capsys, "--config", by_option, "serve", str(tmp_path))
    assert stopped[-1].port == 2222


def test_a_variable_that_names_no_file_is_an_error_line_and_status_1(capsys, tmp_path, monkeypatch, stopped):
    monkeypatch.setenv("PYTFTP_CONFIG", str(tmp_path / "missing.ini"))
    status, _, err = run(capsys, "serve", str(tmp_path))
    assert status == 1 and err.startswith("error: ") and len(err.splitlines()) == 1 and stopped == []


def test_an_option_that_names_no_file_is_a_usage_error(capsys, tmp_path, stopped):
    status, out, err = run(capsys, "-c", str(tmp_path / "missing.ini"), "serve", str(tmp_path))
    assert status == 2 and out == "" and "no such file" in err and stopped == []


def test_the_option_after_the_command_is_the_parsers_usage_error(capsys, tmp_path, stopped):
    path = write(tmp_path, "a.ini", SERVE_INI)
    status, out, err = run(capsys, "serve", str(tmp_path), "-c", path)
    assert status == 2 and out == "" and "unrecognized arguments" in err and stopped == []


def test_a_missing_default_file_is_nothing(capsys, tmp_path, monkeypatch, stopped):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "none"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "none"))
    assert run(capsys, "serve", str(tmp_path))[2] == "error: stopped\n"
    assert stopped[0].port == 69


def test_the_users_own_file_is_the_default(capsys, tmp_path, monkeypatch, stopped):
    default_file(monkeypatch, tmp_path, SERVE_INI)
    assert run(capsys, "serve", str(tmp_path))[2] == "error: stopped\n"
    assert (stopped[0].port, stopped[0].max_sessions) == (6969, 7)


def test_a_file_the_user_names_makes_the_default_moot(capsys, tmp_path, monkeypatch, stopped):
    default_file(monkeypatch, tmp_path, "this is not a settings file\n")
    path = write(tmp_path, "mine.ini", "[serve]\nport = 3333\n")
    run(capsys, "-c", path, "serve", str(tmp_path))
    assert stopped[0].port == 3333
    monkeypatch.setenv("PYTFTP_CONFIG", path)
    run(capsys, "serve", str(tmp_path))
    assert stopped[1].port == 3333


def test_the_default_is_for_one_run_only(capsys, tmp_path, monkeypatch, stopped):
    from tftp.cli._root import Pytftp

    default_file(monkeypatch, tmp_path, SERVE_INI)
    run(capsys, "serve", str(tmp_path))
    assert Pytftp._config_ is None


posix = pytest.mark.skipif(not hasattr(os, "geteuid"), reason="the owner and the mode are POSIX facts")


@posix
def test_a_default_file_everyone_may_write_is_refused_and_a_named_one_is_read(
    capsys, tmp_path, monkeypatch, stopped
):
    path = default_file(monkeypatch, tmp_path, SERVE_INI)
    os.chmod(path, 0o666)
    line = refused(capsys, "serve", str(tmp_path))
    assert "everyone may write it" in line and path in line and stopped == []
    run(capsys, "-c", path, "serve", str(tmp_path))
    assert stopped[0].port == 6969


@posix
def test_a_default_that_is_not_a_regular_file_is_refused(capsys, tmp_path, monkeypatch, stopped):
    folder = tmp_path / "pytftp"
    (folder / "pytftp.ini").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert "not a regular file" in refused(capsys, "serve", str(tmp_path))


def test_no_tool_server_is_started_and_the_file_applies_as_everywhere(capsys, tmp_path, monkeypatch, stopped):
    # PYTFTP_MCP is not read: nothing is served as a tool, so there is no served tree to give no file.
    monkeypatch.setenv("PYTFTP_MCP", "stdio")
    path = write(tmp_path, "a.ini", SERVE_INI)
    status, out, err = run(capsys, "-c", path, "serve", str(tmp_path))
    assert (status, out, err) == (2, "", "error: stopped\n") and stopped[0].port == 6969


# -- what is refused ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, needle",
    [
        ("[serve]\nbogus = %s\n" % MARKER, "unknown key 'bogus' in [serve]"),
        ("[nosuch]\nport = 1\n", "unknown table 'nosuch'"),
        ("[DEFAULT]\nport = %s\n" % MARKER, "unknown key 'port'"),
        ("[DEFAULT]\nconfig = %s\n" % MARKER, "unknown key 'config'"),
        ("[serve]\nlisten_on = %s\n" % MARKER, "unknown key 'listen_on' in [serve]"),
        ("[get]\nmax_sessions = %s\n" % MARKER, "unknown key 'max_sessions' in [get]"),
        ("[DEFAULT]\nserve = %s\n" % MARKER, "'serve' must be a table"),
    ],
    ids=["key", "table", "key-for-a-command", "config", "typo", "key-of-another-command", "not-a-table"],
)
def test_what_nothing_has_is_refused_with_one_line_that_holds_no_value(
    capsys, tmp_path, stopped, text, needle
):
    path = write(tmp_path, "a.ini", text)
    line = refused(capsys, "--loglevel", "TRACE", "-c", path, "serve", str(tmp_path))
    assert needle in line and path in line and MARKER not in line
    assert stopped == []


def test_an_option_the_command_hides_is_not_a_key_of_it(capsys, tmp_path, stopped):
    pytest.importorskip("pktcap")
    path = write(tmp_path, "a.ini", "[capture]\nlisten = 127.0.0.1:69\n")
    assert "unknown key 'listen' in [capture]" in refused(capsys, "-c", path, "capture", "--input", "x.pcap")
    path = write(tmp_path, "b.ini", "[replay]\nbroadcast = true\n")
    assert "unknown key 'broadcast' in [replay]" in refused(
        capsys, "-c", path, "replay", "--input", "x.pcap", "--to", "127.0.0.1"
    )


@pytest.mark.parametrize(
    "name, text",
    [
        ("a.ini", "[serve]\nport = 1\n%s this is no line of an ini file\n" % MARKER),
        ("a.json", '{"serve": {"port": %s}}' % MARKER),
        ("a.json", "[1, 2, 3]"),
        ("a.conf", "[serve]\nport = 1\n# %s\n"),
    ],
    ids=["ini", "json", "json-top-level-a-list", "unknown-ending"],
)
def test_a_document_that_cannot_be_used_is_one_line_that_carries_none_of_its_text(
    capsys, tmp_path, stopped, name, text
):
    path = write(tmp_path, name, text.replace("%s", MARKER))
    line = refused(capsys, "--loglevel", "TRACE", "-c", path, "serve", str(tmp_path))
    assert MARKER not in line and stopped == []


@pytest.mark.skipif(not _toml_readable(), reason="no TOML reader (Python < 3.11 without tomli)")
def test_a_malformed_toml_document_carries_none_of_its_text(capsys, tmp_path, stopped):
    path = write(tmp_path, "a.toml", "[serve]\nport = %s\n" % MARKER)
    assert MARKER not in refused(capsys, "-c", path, "serve", str(tmp_path))


def test_a_file_over_the_limit_is_refused_without_reading_it_all(capsys, tmp_path, stopped):
    path = write(tmp_path, "a.ini", "[serve]\nport = 1\n" + "# %s\n" % MARKER * (MAX_OCTETS // 8))
    line = refused(capsys, "-c", path, "serve", str(tmp_path))
    assert "over %d octets" % MAX_OCTETS in line and MARKER not in line


def test_the_file_a_default_run_found_is_refused_the_same_way(capsys, tmp_path, monkeypatch, stopped):
    default_file(monkeypatch, tmp_path, "[serve]\nbogus = %s\n" % MARKER)
    line = refused(capsys, "serve", str(tmp_path))
    assert "unknown key 'bogus'" in line and MARKER not in line


# -- the command word and the bare install -----------------------------------------------------------


@pytest.mark.parametrize(
    "tokens, expected",
    [
        ([], (False, None)),
        (["serve", "x"], (False, "serve")),
        (["-c", "f.ini", "capture"], (True, "capture")),
        (["--config", "f.ini", "capture"], (True, "capture")),
        (["--config=f.ini", "replay"], (True, "replay")),
        (["--conf", "f.ini", "replay"], (True, "replay")),
        (["-cf.ini", "capture"], (True, "capture")),
        (["-vc", "f.ini", "capture"], (True, "capture")),
        (["-vq", "capture"], (False, "capture")),
        (["--loglevel", "capture:DEBUG", "serve"], (False, "serve")),
        (["--log", "x", "-c", "f.ini", "get"], (True, "get")),
        (["--verbose", "ls"], (False, "ls")),
        (["--", "-c"], (False, None)),
    ],
)
def test_the_words_before_the_command_say_whether_a_file_is_named_and_which_command(tokens, expected):
    assert scan_root_options(tokens) == expected


def test_the_variable_counts_as_naming_the_file(monkeypatch):
    monkeypatch.setenv("PYTFTP_CONFIG", "/x/y.ini")
    assert scan_root_options(["serve"]) == (True, "serve")
    monkeypatch.setenv("PYTFTP_CONFIG", "  ")
    assert scan_root_options(["serve"]) == (False, "serve")


def _child(body, tmp_path):
    return subprocess.run(
        [sys.executable, "-c", body], capture_output=True, text=True, timeout=120, cwd=str(tmp_path)
    )


def test_importing_the_commands_and_running_one_without_a_file_loads_no_config_reader(tmp_path):
    body = (
        "import sys\n"
        "import tftp.cli, tftp.cli._root\n"
        "from tftp.cli import main\n"
        "try:\n"
        "    main(['get', '--help'])\n"
        "except SystemExit:\n"
        "    pass\n"
        "print('duho.config' in sys.modules)\n"
    )
    done = _child(body, tmp_path)
    assert done.stdout.strip().splitlines()[-1] == "False", done.stderr


def test_a_file_that_is_read_loads_it(tmp_path):
    path = write(tmp_path, "a.ini", "[DEFAULT]\nverbose = 0\n")
    body = (
        "import sys\n"
        "from tftp.cli import main\n"
        "try:\n"
        "    main(['-c', %r, 'get', '--help'])\n"
        "except SystemExit:\n"
        "    pass\n"
        "print('duho.config' in sys.modules)\n" % path
    )
    assert _child(body, tmp_path).stdout.strip().splitlines()[-1] == "True"


def _bare(argv, tmp_path):
    body = (
        "import sys\nsys.modules['pktcap'] = None\nfrom tftp.cli import main\nraise SystemExit(main(%r))\n"
        % argv
    )
    return _child(body, tmp_path)


def test_without_pktcap_a_table_for_capture_or_replay_is_refused_with_the_extra_named(tmp_path):
    for table in ("capture", "replay"):
        path = write(tmp_path, table + ".ini", "[%s]\nformat = json\n" % table)
        done = _bare(["-c", path, "ls", "127.0.0.1"], tmp_path)
        assert done.returncode == 2 and PKTCAP_LINE in done.stderr and "Traceback" not in done.stderr
        assert len(done.stderr.splitlines()) == 1


def test_without_pktcap_the_other_tables_still_apply_and_capture_is_still_the_one_line(tmp_path):
    path = write(tmp_path, "a.ini", SERVE_INI)
    done = _bare(["-c", path, "capture", "--input", "boot.pcap"], tmp_path)
    assert (done.returncode, done.stdout, done.stderr) == (1, "", PKTCAP_LINE + "\n")
    done = _bare(["-c", path, "serve", str(tmp_path), "--help"], tmp_path)
    assert done.returncode == 0 and "pytftp serve" in done.stdout
