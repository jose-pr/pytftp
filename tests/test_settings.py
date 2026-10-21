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
from tftp.cli._settings import MAX_OCTETS, command_word, read_settings  # noqa: E402
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
    """What a run refused as a usage error wrote: status 2, nothing on stdout, an error line last."""
    status, out, err = run(capsys, *argv)
    assert status == 2 and out == "", (status, out, err)
    assert "error: " in err.splitlines()[-1], err
    return err


def refused_in_one_line(capsys, *argv):
    """The one line of a run that the program itself refused, before duho's parser wrote anything."""
    err = refused(capsys, *argv)
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


def test_the_default_belongs_to_the_class_and_follows_the_environment_when_it_is_read(
    capsys, tmp_path, monkeypatch, stopped
):
    from tftp.cli._root import Pytftp

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    default_file(monkeypatch, first, "[serve]\nport = 1111\n")
    run(capsys, "serve", str(tmp_path))
    assert Pytftp._config_ is not None and stopped[-1].port == 1111
    default_file(monkeypatch, second, "[serve]\nport = 2222\n")
    run(capsys, "serve", str(tmp_path))
    assert Pytftp._config_ is not None and stopped[-1].port == 2222


def test_an_empty_path_is_no_file_and_the_default_is_not_read_either(capsys, tmp_path, monkeypatch, stopped):
    default_file(monkeypatch, tmp_path, "[serve]\nbogus = %s\n" % MARKER)
    run(capsys, "--config", "", "serve", str(tmp_path))
    run(capsys, "--config=", "serve", str(tmp_path))
    run(capsys, "-c", "", "serve", str(tmp_path))
    monkeypatch.setenv("PYTFTP_CONFIG", "")
    run(capsys, "serve", str(tmp_path))
    assert [built.port for built in stopped] == [69, 69, 69, 69]


def test_an_empty_variable_gives_way_to_the_option_that_names_a_file(capsys, tmp_path, monkeypatch, stopped):
    path = write(tmp_path, "a.ini", SERVE_INI)
    monkeypatch.setenv("PYTFTP_CONFIG", "")
    run(capsys, "-c", path, "serve", str(tmp_path))
    assert stopped[0].port == 6969


posix = pytest.mark.skipif(not hasattr(os, "geteuid"), reason="the owner and the mode are POSIX facts")


@posix
def test_a_default_file_everyone_may_write_is_refused_and_a_named_one_is_read(
    capsys, tmp_path, monkeypatch, stopped
):
    path = default_file(monkeypatch, tmp_path, SERVE_INI)
    os.chmod(path, 0o666)
    line = refused_in_one_line(capsys, "serve", str(tmp_path))
    assert "everyone may write it" in line and path in line and stopped == []
    run(capsys, "-c", path, "serve", str(tmp_path))
    assert stopped[0].port == 6969


@posix
@pytest.mark.parametrize("source", ["argument", "field", "env"])
def test_the_loader_reads_a_file_it_was_told_the_user_named_as_it_is(tmp_path, source):
    path = pathlib.Path(write(tmp_path, "a.ini", SERVE_INI))
    os.chmod(path, 0o666)
    assert read_settings(path, source)["serve"]["port"] == "6969"


@posix
def test_the_loader_refuses_the_default_file_that_everyone_may_write(tmp_path):
    path = pathlib.Path(write(tmp_path, "a.ini", SERVE_INI))
    os.chmod(path, 0o666)
    with pytest.raises(ValueError, match="everyone may write it"):
        read_settings(path, "default")
    os.chmod(path, 0o644)
    assert read_settings(path, "default")["serve"]["port"] == "6969"


def test_the_loader_applies_the_limit_whatever_the_source(tmp_path):
    path = pathlib.Path(write(tmp_path, "a.ini", "[serve]\nport = 1\n" + "# x\n" * MAX_OCTETS))
    for source in ("argument", "field", "env", "default"):
        with pytest.raises(ValueError, match="over %d octets" % MAX_OCTETS):
            read_settings(path, source)


@posix
def test_a_default_that_is_not_a_regular_file_is_refused(capsys, tmp_path, monkeypatch, stopped):
    folder = tmp_path / "pytftp"
    (folder / "pytftp.ini").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert "not a regular file" in refused_in_one_line(capsys, "serve", str(tmp_path))


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
        ("[serve]\nbogus = %s\n" % MARKER, "'serve.bogus'"),
        ("[nosuch]\nport = 1\n", "'nosuch'"),
        ("[DEFAULT]\nbogus = %s\n" % MARKER, "'bogus'"),
        ("[DEFAULT]\nport = %s\n" % MARKER, "'port'"),
        ("[DEFAULT]\nconfig = %s\n" % MARKER, "'config'"),
        ("[serve]\nlisten_on = %s\n" % MARKER, "'serve.listen_on'"),
        ("[get]\nmax_sessions = %s\n" % MARKER, "'get.max_sessions'"),
        ("[DEFAULT]\nserve = %s\n" % MARKER, "'serve'"),
    ],
    ids=[
        "key-of-a-command",
        "table",
        "key",
        "key-for-a-command",
        "config",
        "typo",
        "other-command",
        "not-a-table",
    ],
)
def test_what_nothing_has_is_status_2_with_the_key_named_and_no_value_of_the_file(
    capsys, tmp_path, stopped, text, needle
):
    path = write(tmp_path, "a.ini", text)
    err = refused(capsys, "--loglevel", "TRACE", "-c", path, "serve", str(tmp_path))
    assert len(err.splitlines()) == 1, err  # duho's check: no usage lines before it
    assert needle in err and path in err and MARKER not in err
    assert stopped == []


def test_an_option_the_command_hides_is_not_a_key_of_it(capsys, tmp_path, stopped):
    pytest.importorskip("pktcap")
    path = write(tmp_path, "a.ini", "[capture]\nlisten = 127.0.0.1:69\n")
    assert "'capture.listen'" in refused(capsys, "-c", path, "capture", "--input", "x.pcap")
    path = write(tmp_path, "b.ini", "[replay]\nbroadcast = true\n")
    assert "'replay.broadcast'" in refused(
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
    err = refused(capsys, "--loglevel", "TRACE", "-c", path, "serve", str(tmp_path))
    assert MARKER not in err and stopped == []


def test_help_is_answered_whatever_the_file_holds(capsys, tmp_path, stopped):
    path = write(tmp_path, "a.ini", "[serve]\nport = 1\nthis is no line of an ini file\n")
    for argv in (("-c", path, "--help"), ("-c", path, "serve", "--help")):
        status, out, err = run(capsys, *argv)
        assert status == 0 and out.startswith("usage: pytftp") and err == ""
    assert run(capsys, "-c", path, "serve", str(tmp_path))[0] == 2


def test_the_key_nothing_has_keeps_the_program_name_in_the_line_and_the_rest_do_not(
    capsys, tmp_path, stopped
):
    typo = write(tmp_path, "typo.ini", "[serve]\nbogus = 1\n")
    assert refused(capsys, "-c", typo, "serve", str(tmp_path)).startswith("pytftp: error: duho: config file ")
    broken = write(tmp_path, "broken.ini", "[serve]\nthis is no line\n")
    assert refused(capsys, "-c", broken, "serve", str(tmp_path)).startswith("error: duho: invalid INI ")


@pytest.mark.skipif(not _toml_readable(), reason="no TOML reader (Python < 3.11 without tomli)")
def test_a_malformed_toml_document_carries_none_of_its_text(capsys, tmp_path, stopped):
    path = write(tmp_path, "a.toml", "[serve]\nport = %s\n" % MARKER)
    assert MARKER not in refused_in_one_line(capsys, "-c", path, "serve", str(tmp_path))


def test_a_file_over_the_limit_is_refused_without_reading_it_all(capsys, tmp_path, stopped):
    path = write(tmp_path, "a.ini", "[serve]\nport = 1\n" + "# %s\n" % MARKER * (MAX_OCTETS // 8))
    line = refused_in_one_line(capsys, "-c", path, "serve", str(tmp_path))
    assert "over %d octets" % MAX_OCTETS in line and MARKER not in line


def test_the_file_a_default_run_found_is_refused_the_same_way(capsys, tmp_path, monkeypatch, stopped):
    default_file(monkeypatch, tmp_path, "[serve]\nbogus = %s\n" % MARKER)
    err = refused(capsys, "serve", str(tmp_path))
    assert "'serve.bogus'" in err and MARKER not in err


# -- the command word and the bare install -----------------------------------------------------------


@pytest.mark.parametrize(
    "tokens, expected",
    [
        ([], None),
        (["serve", "x"], "serve"),
        (["-c", "f.ini", "capture"], "capture"),
        (["--config", "f.ini", "capture"], "capture"),
        (["--config", "", "capture"], "capture"),
        (["--config=f.ini", "replay"], "replay"),
        (["--conf", "f.ini", "replay"], "replay"),
        (["-cf.ini", "capture"], "capture"),
        (["-vc", "f.ini", "capture"], "capture"),
        (["-vq", "capture"], "capture"),
        (["--loglevel", "capture:DEBUG", "serve"], "serve"),
        (["--log", "x", "-c", "f.ini", "get"], "get"),
        (["--verbose", "ls"], "ls"),
        (["--", "-c"], None),
    ],
)
def test_the_words_before_the_command_say_which_command_it_is(tokens, expected):
    assert command_word(tokens) == expected


def _child(body, tmp_path):
    return subprocess.run(
        [sys.executable, "-c", body], capture_output=True, text=True, timeout=120, cwd=str(tmp_path)
    )


def _loaded(tmp_path, body):
    """The names of ``duho.config``, ``tomllib``, ``tomli`` and ``yaml`` the child holds after ``body``."""
    names = ("duho", "duho.config", "tomllib", "tomli", "yaml")
    program = "import sys\n%s\nprint([name for name in %r if name in sys.modules])\n" % (body, names)
    done = _child(program, tmp_path)
    assert done.returncode == 0, done.stderr
    return eval(done.stdout.strip().splitlines()[-1])


_RUN_GET = "from tftp.cli import main\ntry:\n    main(%r)\nexcept SystemExit:\n    pass\n"


def test_importing_the_command_package_loads_no_duho_and_the_settings_reader_no_duho_config(tmp_path):
    assert _loaded(tmp_path, "import tftp.cli") == []
    assert _loaded(tmp_path, "import tftp.cli._settings") == []


def test_the_root_holds_the_users_path_unresolved_and_a_run_without_a_file_loads_no_format_reader(tmp_path):
    # `_config_` is duho's `user_path`, which is only a path to be resolved, so importing the root loads
    # duho.config and no reader of a format: TOML and YAML are loaded when a file in them is read.
    assert _loaded(tmp_path, "import tftp.cli._root") == ["duho", "duho.config"]
    assert _loaded(tmp_path, _RUN_GET % ["get", "--help"]) == ["duho", "duho.config"]


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
