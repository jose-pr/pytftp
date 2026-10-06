"""One script of requests, run against every backend a server can be given.

Each implementation is built over a store whose contents the test can read back
without going through TFTP, so what a request did is judged from the store and
from the octets the client received.
"""

from __future__ import annotations

import os

import pytest

import tftp
from conftest import HttpStore, client_for
from tftp.backends import (
    CaseInsensitive,
    FilesystemBackend,
    HTTPBackend,
    MemoryBackend,
    PerClient,
    Remap,
    UpstreamBackend,
)

SIZES = [0, 1, 511, 512, 513, 4096, 20_000]
FILES = {"size-%d.bin" % size: os.urandom(size) for size in SIZES}
FILES["dir/nested.bin"] = b"nested"


class Store:
    """A backend and a way to read what it holds, without TFTP.

    ``has_directories`` is False for a store of flat keys (a name containing
    ``/`` is one key, and a key and the "directory" before its ``/`` coexist);
    ``confines_names`` is False where a name has no outside to climb to.
    """

    def __init__(self, backend, read, *, has_directories=True, confines_names=True):
        self.backend = backend
        self.read = read  # name -> bytes or None
        self.has_directories = has_directories
        self.confines_names = confines_names


def _populate(directory):
    for name, data in FILES.items():
        path = directory.joinpath(*name.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return directory


def _filesystem(tmp_path, writable, **_):
    root = _populate(tmp_path / "fs")
    return Store(FilesystemBackend(root, writable=writable), lambda n: _file(root / n))


def _memory(tmp_path, writable, **_):
    backend = MemoryBackend(dict(FILES), writable=writable)
    return Store(backend, lambda n: backend.files.get(n), has_directories=False, confines_names=False)


def _http(tmp_path, writable, web, **_):
    HttpStore.store.clear()
    HttpStore.store.update({"/c/" + name: data for name, data in FILES.items()})
    return Store(
        HTTPBackend(web + "/c", writable=writable),
        lambda n: HttpStore.store.get("/c/" + n),
        has_directories=False,
    )


def _upstream(tmp_path, writable, make_server, **_):
    root = _populate(tmp_path / "upstream")
    upstream = make_server(root, writable=writable, overwrite=True)
    backend = UpstreamBackend(
        ("127.0.0.1", upstream.server_address[1]),
        client_options={"timeout": 0.5, "retries": 3},
        writable=writable,
    )
    return Store(backend, lambda n: _file(root / n))


def _remap(tmp_path, writable, **_):
    root = _populate(tmp_path / "remap")
    backend = Remap(FilesystemBackend(root, writable=writable), [r"^/?alias/="])
    return Store(backend, lambda n: _file(root / n))


def _per_client(tmp_path, writable, **_):
    root = tmp_path / "per-client"
    own = _populate(root / "127.0.0.1")
    backend = PerClient(root, lambda d: FilesystemBackend(d, writable=writable), fallback=False)
    return Store(backend, lambda n: _file(own / n))


def _case_insensitive(tmp_path, writable, **_):
    root = _populate(tmp_path / "case")
    return Store(CaseInsensitive(root, writable=writable), lambda n: _file(root / n))


def _file(path):
    return path.read_bytes() if path.is_file() else None


BACKENDS = {
    "filesystem": _filesystem,
    "memory": _memory,
    "http": _http,
    "upstream": _upstream,
    "remap": _remap,
    "per-client": _per_client,
    "case-insensitive": _case_insensitive,
}


@pytest.fixture(params=list(BACKENDS))
def build(request, tmp_path, make_server, web):
    """``build(writable)``: ``(store, client)`` for the backend under test, served by a real server."""

    def make(writable):
        store = BACKENDS[request.param](tmp_path, writable, make_server=make_server, web=web)
        server = make_server(store.backend)
        store.client = lambda **options: client_for(server, timeout=1, **options)
        return store, store.client()

    return make


def test_every_size_is_read_exactly(build):
    store, client = build(writable=False)
    for name, data in FILES.items():
        assert client.get(name) == data, name


def test_a_download_with_small_blocks_and_a_window_is_read_exactly(build):
    store, client = build(writable=False)
    small = store.client(blksize=64, windowsize=4)
    assert small.get("size-20000.bin") == FILES["size-20000.bin"]


def test_the_size_a_backend_knows_is_the_size_it_serves(build):
    store, client = build(writable=False)
    for name in ("size-0.bin", "size-513.bin", "size-20000.bin"):
        assert client.size(name) == len(FILES[name]), name


@pytest.mark.parametrize("name", ["missing.bin", "dir/missing.bin", "missing/dir/f.bin"])
def test_a_name_that_is_not_there_is_file_not_found(build, name):
    store, client = build(writable=False)
    with pytest.raises(tftp.FileNotFound):
        client.get(name)


@pytest.mark.parametrize("name", ["../outside.bin", "dir/../../outside.bin", "/../outside.bin"])
def test_a_name_that_climbs_out_is_refused_and_returns_nothing(build, name):
    store, client = build(writable=False)
    with pytest.raises(tftp.RemoteError) as info:
        client.get(name)
    assert info.value.code in (tftp.TFTPErrorCode.ACCESS_VIOLATION, tftp.TFTPErrorCode.FILE_NOT_FOUND)


def test_a_directory_is_not_a_file(build):
    store, client = build(writable=False)
    with pytest.raises(tftp.RemoteError) as info:
        client.get("dir")
    assert info.value.code in (tftp.TFTPErrorCode.ACCESS_VIOLATION, tftp.TFTPErrorCode.FILE_NOT_FOUND)


def test_a_read_only_backend_refuses_an_upload_and_changes_nothing(build):
    store, client = build(writable=False)
    before = {name: store.read(name) for name in FILES}
    with pytest.raises(tftp.AccessViolation):
        client.put("new.bin", b"data")
    assert store.read("new.bin") is None
    assert {name: store.read(name) for name in FILES} == before


@pytest.mark.parametrize("size", [0, 1, 513, 20_000])
def test_an_upload_lands_whole_in_the_store_and_reads_back(build, size):
    store, client = build(writable=True)
    data = os.urandom(size)
    client.put("new-%d.bin" % size, data)
    assert store.read("new-%d.bin" % size) == data
    assert client.get("new-%d.bin" % size) == data


def test_an_upload_that_names_a_directory_changes_no_file(build):
    store, client = build(writable=True)
    before = {name: store.read(name) for name in FILES}
    if store.has_directories:
        with pytest.raises(tftp.RemoteError):
            client.put("dir", b"data")
        assert {name: store.read(name) for name in FILES} == before
    else:
        client.put("dir", b"data")  # a flat key beside "dir/nested.bin", which it leaves alone
        assert store.read("dir") == b"data" and store.read("dir/nested.bin") == before["dir/nested.bin"]


def test_an_upload_that_climbs_out_is_refused(build):
    store, client = build(writable=True)
    if store.confines_names:
        with pytest.raises(tftp.RemoteError):
            client.put("../outside.bin", b"data")
        assert store.read("../outside.bin") is None
    else:
        client.put("../outside.bin", b"data")  # a key like any other: there is no outside
        assert store.read("../outside.bin") == b"data"


def test_the_hooks_are_the_handler_contract(build):
    store, _ = build(writable=False)
    for hook in ("open_read", "open_write"):
        assert callable(getattr(store.backend, hook))
    assert isinstance(getattr(store.backend, "opens_fast", False), bool)
