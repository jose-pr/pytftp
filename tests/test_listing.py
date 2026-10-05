"""pytftp's x-list / x-mtime extensions: listing format, server, client, paths."""

from __future__ import annotations

import asyncio
import os

import pytest

import tftp
import tftp.aio
from conftest import client_for
from tftp.listing import DirectoryListing, ListEntry, dumps, loads

LISTING = tftp.TFTPServerOptions(allowed=tftp.STANDARD_OPTIONS | tftp.LISTING_OPTIONS)


@pytest.fixture
def server(root, make_server):
    os.utime(root / "one.bin", (1_700_000_000, 1_700_000_000))
    return make_server(root, options=LISTING)


def test_format_round_trip():
    entries = [
        ListEntry("plain", False, 5, 7),
        ListEntry("dir", True, 0, None),
        ListEntry("sp ace %25 and\nnewline\rcr", False, 1, 2),
        ListEntry("ÃƒÂ©", False, 3, 4),
    ]
    data = dumps(entries)
    assert data.count(b"\n") == 4
    assert loads(data) == entries
    assert loads(b"garbage\nf x 1 bad-size\nq 1 1 kind\nf 1 - ok\n") == [ListEntry("ok", False, 1)]


def test_directory_listing_stream(root):
    (root / ".up.bin.abc.part").write_bytes(b"in progress")
    listing = DirectoryListing(str(root))
    names = {e.name: e for e in loads(listing.read())}
    assert names["sub"].is_dir and names["one.bin"].size == 1
    assert ".up.bin.abc.part" not in names
    assert listing.size == len(listing.getvalue()) and listing.mtime


def test_client_listdir(root, server):
    client = client_for(server)
    entries = {e.name: e for e in client.listdir()}
    assert set(entries) == {p.name for p in root.iterdir()}
    assert entries["sub"].is_dir and not entries["big.bin"].is_dir
    assert entries["big.bin"].size == 300_001 and entries["one.bin"].mtime == 1_700_000_000
    assert [e.name for e in client.listdir("sub")] == ["nested.bin"]
    assert [e.name for e in client.listdir("/sub/")] == ["nested.bin"]
    with pytest.raises(NotADirectoryError):
        client.listdir("one.bin")
    with pytest.raises(tftp.FileNotFound):
        client.listdir("missing")
    stats = server.stats_snapshot()
    assert stats["failed"] == 0 and stats["declined"] == 1  # the file refused as a listing


def test_client_stat(root, server):
    client = client_for(server)
    assert client.stat("one.bin") == tftp.RemoteStat(1, 1_700_000_000, False)
    info = client.stat("sub")
    assert info.is_dir and info.size is None and info.mtime
    with pytest.raises(tftp.FileNotFound):
        client.stat("missing")


def test_server_without_listing(root, make_server):
    plain = client_for(make_server(root))
    assert plain.stat("one.bin") == tftp.RemoteStat(1)
    with pytest.raises(tftp.FileNotFound):
        plain.stat("sub")
    with pytest.raises(tftp.FileNotFound):
        plain.listdir("sub")
    with pytest.raises(NotADirectoryError):
        plain.listdir("one.bin")


def test_listing_not_offered_when_not_allowed(root, make_server):
    only_mtime = make_server(
        root, options=tftp.TFTPServerOptions(allowed=tftp.STANDARD_OPTIONS | {"x-mtime"})
    )
    info = client_for(only_mtime).stat("one.bin")
    assert info.size == 1 and info.mtime and not info.is_dir
    with pytest.raises(tftp.FileNotFound):
        client_for(only_mtime).listdir("sub")


def test_async_client(root, server):
    async def main():
        client = tftp.aio.AsyncTFTPClient("127.0.0.1", server.server_address[1], timeout=0.5)
        names = [e.name for e in await client.listdir("sub")]
        info = await client.stat("sub")
        with pytest.raises(NotADirectoryError):
            await client.listdir("one.bin")
        return names, info

    names, info = asyncio.run(main())
    assert names == ["nested.bin"] and info.is_dir


def test_async_server_lists(root):
    async def main():
        async with tftp.aio.AsyncTFTPServer(root, "127.0.0.1", 0, options=LISTING) as server:
            await server.start()
            client = tftp.aio.AsyncTFTPClient("127.0.0.1", server.server_address[1], timeout=0.5)
            return [e.name for e in await client.listdir("sub")]

    assert asyncio.run(main()) == ["nested.bin"]


# -- paths ---------------------------------------------------------------------------


def test_tftp_path_walk_glob_and_stat(root, server):
    pytest.importorskip("pathlib_next")
    client = client_for(server)
    top = client.path("")
    assert {p.name for p in top.iterdir()} == {p.name for p in root.iterdir()}
    assert client.path("sub").is_dir() and not client.path("one.bin").is_dir()
    assert client.path("one.bin").stat().st_mtime == 1_700_000_000
    assert sorted(p.as_posix() for p in top.glob("**/*.bin") if p.name.startswith("n")) == ["sub/nested.bin"]
    walked = {dirpath.as_posix(): sorted(files) for dirpath, _, files in client.path("sub").walk()}
    assert walked == {"sub": ["nested.bin"]}


def test_uri_path_listing(root, server, tmp_path_factory):
    pytest.importorskip("pathlib_next")
    pytest.importorskip("uritools")
    from pathlib_next import LocalPath
    from pathlib_next.uri import UriPath

    base = UriPath("tftp://127.0.0.1:%d/" % server.server_address[1])
    assert "sub" in {p.name for p in base.iterdir()}
    assert (base / "sub").is_dir()
    target = LocalPath(tmp_path_factory.mktemp("tree") / "copy")
    (base / "sub").copy(target, recursive=True)  # needs listing
    assert (target / "nested.bin").read_bytes() == b"nested"
