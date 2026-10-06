"""Remap, PerClient and CaseInsensitive: the handlers that compose over a directory.

Each test moves real data over loopback through a server, and judges what a client
received or was refused, not what the wrapper believes it did.
"""

from __future__ import annotations

import os

import pytest

import tftp
from conftest import client_for
from tftp.backends import CaseInsensitive, FilesystemBackend, PerClient, Remap
from tftp.exceptions import TFTPValueError
from tftp.options import LISTING_OPTIONS, STANDARD_OPTIONS


@pytest.fixture
def served(tmp_path):
    """``(served directory, a file next to it that no request may reach)``."""
    root = tmp_path / "served"
    root.mkdir()
    (root / "one.bin").write_bytes(b"x")
    (root / "sub").mkdir()
    (root / "sub" / "nested.bin").write_bytes(b"nested")
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"outside the root")
    return root, outside


def _files(root):
    return FilesystemBackend(root, writable=True)


# -- Remap ---------------------------------------------------------------------------------------


def test_remap_rewrites_the_first_matching_rule(served, make_server):
    root, _ = served
    server = make_server(Remap(_files(root), [r"^/?pxelinux/=", "[.]BIN$=.bin"]))
    client = client_for(server)
    assert client.get("/pxelinux/one.bin") == b"x"
    assert client.get("one.BIN") == b"x"
    assert client.get("sub/nested.bin") == b"nested"  # no rule matches: unchanged


def test_remap_only_the_first_matching_rule_applies(served, make_server):
    root, _ = served
    handler = Remap(_files(root), ["^a=b", "^b=c"])
    assert handler.rewrite("a") == "b" and handler.rewrite("b") == "c" and handler.rewrite("x") == "x"
    assert Remap(_files(root), [r"(sub)/(\w+)=\2/\1"]).rewrite("sub/nested.bin") == "nested/sub.bin"


def test_remap_takes_pairs_and_compiled_patterns(served, make_server):
    import re

    root, _ = served
    handler = Remap(_files(root), [("alias", "one"), (re.compile(r"^NESTED$", re.I), "sub/nested.bin")])
    client = client_for(make_server(handler))
    assert client.get("alias.bin") == b"x"
    assert client.get("nested") == b"nested"


def test_remap_uploads_go_to_the_rewritten_name(served, make_server):
    root, _ = served
    client = client_for(make_server(Remap(_files(root), ["^in/=sub/"])))
    client.put("in/up.bin", b"up")
    assert (root / "sub" / "up.bin").read_bytes() == b"up"


@pytest.mark.parametrize("rule", ["no-equals", "=x", "(=x"])
def test_remap_refuses_a_rule_it_cannot_read(served, rule):
    with pytest.raises(TFTPValueError):
        Remap(_files(served[0]), [rule])


@pytest.mark.parametrize("rule", [3, ("only-one",), (1, "x"), ("a", 2)])
def test_remap_refuses_a_rule_of_another_shape(served, rule):
    with pytest.raises(TypeError):
        Remap(_files(served[0]), [rule])


def test_remap_keeps_the_listing_flag_and_the_interface_of_the_request(served, make_server):
    root, _ = served
    seen = []

    class Spy(FilesystemBackend):
        def open_read(self, context):
            seen.append((context.filename, context.listing, context.interface_index, context.local_address))
            return super().open_read(context)

    server = make_server(
        Remap(Spy(root), ["^alias$=sub"]),
        options=tftp.TFTPServerOptions(allowed=STANDARD_OPTIONS | LISTING_OPTIONS),
    )
    client = client_for(server)
    assert [entry.name for entry in client.listdir("alias")] == ["nested.bin"]
    name, listing, index, local = seen[-1]
    assert name == "sub" and listing is True
    if server.has_pktinfo:
        assert local is not None and index > 0


@pytest.mark.parametrize("name", ["../secret.txt", "a/../../secret.txt", "..\\secret.txt"])
def test_a_rewrite_cannot_leave_the_served_directory(served, make_server, name):
    root, outside = served
    client = client_for(make_server(Remap(_files(root), ["^a/=", "^go=.."])))
    with pytest.raises(tftp.AccessViolation):
        client.get(name)
    with pytest.raises(tftp.AccessViolation):
        client.get("go/secret.txt")  # a rule that builds the escape
    with pytest.raises(tftp.AccessViolation):
        client.put("go/planted.txt", b"x")
    assert outside.read_bytes() == b"outside the root" and not (root.parent / "planted.txt").exists()


def test_remap_is_a_handler_that_opens_as_fast_as_what_it_wraps(served):
    root, _ = served

    class Slow:
        pass

    assert Remap(_files(root), []).opens_fast is True and Remap(Slow(), []).opens_fast is False
    assert "Remap(" in repr(Remap(_files(root), ["^a=b"])) and "^a=b" in repr(Remap(_files(root), ["^a=b"]))


# -- PerClient -----------------------------------------------------------------------------------


@pytest.fixture
def per_client(tmp_path):
    root = tmp_path / "clients"
    for name, content in (("127.0.0.1", b"mine"), ("10.9.9.9", b"for 10.9.9.9 only")):
        (root / name).mkdir(parents=True)
        (root / name / "cfg").write_bytes(content)
    (root / "shared.bin").write_bytes(b"shared")
    (tmp_path / "secret.txt").write_bytes(b"outside the root")
    return root


def test_per_client_serves_the_clients_own_directory(per_client, make_server):
    client = client_for(make_server(PerClient(per_client, _files)))
    assert client.get("cfg") == b"mine"
    client.put("up.bin", b"u")
    assert (per_client / "127.0.0.1" / "up.bin").read_bytes() == b"u"
    assert not (per_client / "up.bin").exists()


def test_a_client_without_a_directory_is_served_the_root_with_every_other_clients_directory(
    per_client, make_server
):
    (per_client / "127.0.0.1" / "cfg").unlink()
    (per_client / "127.0.0.1").rmdir()
    client = client_for(make_server(PerClient(per_client, _files)))
    assert client.get("shared.bin") == b"shared"
    assert client.get("10.9.9.9/cfg") == b"for 10.9.9.9 only"  # a convenience, not isolation


def test_without_fallback_a_client_with_no_directory_is_given_nothing(per_client, make_server):
    (per_client / "127.0.0.1" / "cfg").unlink()
    (per_client / "127.0.0.1").rmdir()
    server = make_server(PerClient(per_client, _files, fallback=False))
    client = client_for(server)
    for name in ("shared.bin", "10.9.9.9/cfg", "cfg"):
        with pytest.raises(tftp.FileNotFound):
            client.get(name)
    with pytest.raises(tftp.FileNotFound):
        client.put("planted.bin", b"x")
    assert not (per_client / "planted.bin").exists()


def test_without_fallback_a_client_cannot_reach_another_clients_directory(per_client, make_server):
    client = client_for(make_server(PerClient(per_client, _files, fallback=False)))
    assert client.get("cfg") == b"mine"
    for name in ("../10.9.9.9/cfg", "..\\10.9.9.9\\cfg", "../shared.bin", "../../secret.txt"):
        with pytest.raises(tftp.AccessViolation):
            client.get(name)
    with pytest.raises(tftp.FileNotFound):
        client.get("10.9.9.9/cfg")  # not below its own directory


def test_per_client_makes_one_handler_for_each_directory(per_client, make_server):
    made = []

    def make(directory):
        made.append(directory)
        return _files(directory)

    client = client_for(make_server(PerClient(per_client, make)))
    for _ in range(3):
        client.get("cfg")
    assert len(made) == 1 and made[0].endswith("127.0.0.1")


def test_the_directory_of_a_client_is_its_address_written_for_a_file_system():
    assert PerClient.directory(("::ffff:10.0.0.1", 1)) == "10.0.0.1"
    assert PerClient.directory(("fe80::1%3", 1)) == "fe80--1"
    assert PerClient.directory(("2001:db8::7", 1)) == "2001-db8--7"
    assert PerClient.directory(("192.0.2.9", 1)) == "192.0.2.9"


def test_per_client_prints_what_it_serves(per_client):
    assert repr(PerClient(per_client, _files, fallback=False)).endswith("fallback=False)")


# -- CaseInsensitive -----------------------------------------------------------------------------


def test_case_insensitive_lookup(served, make_server):
    root, _ = served
    (root / "Boot").mkdir()
    (root / "Boot" / "BCD").write_bytes(b"bcd")
    client = client_for(make_server(CaseInsensitive(root, writable=True)))
    assert client.get("\\boot\\bcd") == b"bcd"
    assert client.get("ONE.BIN") == b"x"
    client.put("boot/New.Cfg", b"n")
    assert (root / "Boot" / "New.Cfg").read_bytes() == b"n"


def test_case_insensitive_exact_name_wins(served, make_server):
    root, _ = served
    (root / "A.txt").write_bytes(b"upper")
    (root / "a.txt").write_bytes(b"lower")
    if sorted(n for n in os.listdir(root) if n.lower() == "a.txt") != ["A.txt", "a.txt"]:
        pytest.skip("this file system holds one file for both names")
    client = client_for(make_server(CaseInsensitive(root)))
    assert client.get("a.txt") == b"lower" and client.get("A.txt") == b"upper"


@pytest.mark.parametrize("name", ["../SECRET.TXT", "../secret.txt", "SUB/../../secret.txt", "..\\Secret.txt"])
def test_case_insensitive_matching_cannot_leave_the_served_directory(served, make_server, name):
    root, outside = served
    client = client_for(make_server(CaseInsensitive(root, writable=True)))
    with pytest.raises(tftp.AccessViolation):
        client.get(name)
    with pytest.raises(tftp.AccessViolation):
        client.put(name, b"x")
    assert outside.read_bytes() == b"outside the root"


def test_a_filesystem_backend_prints_its_root_and_kind(served):
    root, _ = served
    text = repr(CaseInsensitive(root, writable=True))
    assert text.startswith("CaseInsensitive(") and "writable=True" in text
    assert os.path.realpath(root) in text.replace("\\\\", "\\")
