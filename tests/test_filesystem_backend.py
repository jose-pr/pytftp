"""FilesystemBackend and AtomicWriter: what stays inside the root, what is refused for space, how an upload lands."""

from __future__ import annotations

import collections
import shutil

import pytest

import tftp
from conftest import LISTING, client_for
from tftp.exceptions import AccessViolation, DiskFull


@pytest.fixture
def outside(tmp_path_factory):
    """A directory beside the served one, holding a file and a subdirectory with a file."""
    directory = tmp_path_factory.mktemp("outside")
    (directory / "secret.txt").write_bytes(b"secret")
    (directory / "dir").mkdir()
    (directory / "dir" / "inner.txt").write_bytes(b"inner")
    return directory


def test_a_symbolic_link_out_of_the_root_is_neither_read_nor_listed(root, make_server, symlink, outside):
    symlink(root / "leak.txt", outside / "secret.txt")
    symlink(root / "leakdir", outside / "dir")
    client = client_for(make_server(root, options=LISTING))
    for name in ("leak.txt", "leakdir/inner.txt"):
        with pytest.raises(AccessViolation):
            client.get(name)
    with pytest.raises(AccessViolation):
        client.listdir("leakdir")
    names = {entry.name for entry in client.listdir("")}
    assert {"one.bin", "sub"} <= names and not names & {"leak.txt", "leakdir"}


def test_a_symbolic_link_that_stays_inside_the_root_is_followed(root, make_server, symlink):
    symlink(root / "alias.bin", root / "one.bin")
    symlink(root / "aliasdir", root / "sub")
    client = client_for(make_server(root, options=LISTING))
    assert client.get("alias.bin") == b"x"
    assert client.get("aliasdir/nested.bin") == b"nested"
    assert {"alias.bin", "aliasdir"} <= {entry.name for entry in client.listdir("")}


def test_an_upload_through_a_symbolic_link_out_of_the_root_is_refused(root, make_server, symlink, outside):
    symlink(root / "leakdir", outside / "dir")
    client = client_for(make_server(root, writable=True, overwrite=True))
    with pytest.raises(AccessViolation):
        client.put("leakdir/inner.txt", b"overwritten")
    assert (outside / "dir" / "inner.txt").read_bytes() == b"inner"


_Usage = collections.namedtuple("_Usage", "total used free")


@pytest.mark.parametrize("free, accepted", [(1000, False), (4000, True), (10**12, True)])
def test_an_upload_larger_than_the_free_space_is_refused_before_any_data_moves(
    root, make_server, monkeypatch, free, accepted
):
    monkeypatch.setattr(shutil, "disk_usage", lambda path: _Usage(10**12, 10**12 - free, free))
    client = client_for(make_server(root, writable=True))
    if accepted:
        client.put("new.bin", b"y" * 4000)
        assert (root / "new.bin").read_bytes() == b"y" * 4000
    else:
        with pytest.raises(DiskFull):
            client.put("new.bin", b"y" * 4000)
        assert not (root / "new.bin").exists()


def test_an_upload_does_not_replace_a_file_that_appeared_while_it_was_written(tmp_path):
    target = tmp_path / "up.bin"
    writer = tftp.AtomicWriter(str(target), overwrite=False)
    writer.write(b"upload")
    target.write_bytes(b"theirs")
    with pytest.raises(tftp.TFTPError) as info:
        writer.close()
    assert info.value.code == tftp.TFTPErrorCode.FILE_EXISTS
    assert target.read_bytes() == b"theirs"
    assert [path.name for path in tmp_path.iterdir()] == ["up.bin"]


def test_an_upload_that_may_overwrite_replaces_a_file_that_appeared(tmp_path):
    target = tmp_path / "up.bin"
    writer = tftp.AtomicWriter(str(target), overwrite=True)
    writer.write(b"upload")
    target.write_bytes(b"theirs")
    writer.close()
    assert target.read_bytes() == b"upload"
    assert [path.name for path in tmp_path.iterdir()] == ["up.bin"]
