"""TFTPPath and TFTPURIPath (the ``path`` extra)."""

from __future__ import annotations

import io
import os

import pytest

pytest.importorskip("pathapi")
pytest.importorskip("uripath")

import tftp  # noqa: E402
from conftest import FakePeer, client_for  # noqa: E402
from pathapi import LocalPath, MemPath  # noqa: E402
from pathapi.protocols import Listing, Remove, supports  # noqa: E402
from uripath import URIPath  # noqa: E402
from tftp.path import TFTPPath, TFTPURIPath  # noqa: E402

LISTING = tftp.TFTPServerOptions(allowed=tftp.options.STANDARD_OPTIONS | tftp.options.LISTING_OPTIONS)


@pytest.fixture
def server(root, make_server):
    return make_server(root, writable=True, overwrite=True)


def test_read_write_stat(root, server):
    client = client_for(server)
    big = client.path("big.bin")
    assert isinstance(big, TFTPPath)
    assert big.read_bytes() == (root / "big.bin").read_bytes()
    assert big.stat().st_size == 300_001 and big.exists() and big.is_file() and not big.is_dir()
    assert not client.path("missing").exists()
    with pytest.raises(FileNotFoundError):
        client.path("missing").read_bytes()
    client.path("written.txt").write_text("hÃ©llo\n", encoding="utf-8", newline="\n")
    assert (root / "written.txt").read_bytes() == "hÃ©llo\n".encode()
    assert client.path("text.txt", mode="netascii").read_bytes() == (root / "text.txt").read_bytes()


def test_streaming_handles(root, server):
    client = client_for(server, windowsize=4)
    with client.path("big.bin").open("rb") as handle:
        assert handle.read(10) == (root / "big.bin").read_bytes()[:10]  # abandon the rest
    with client.path("streamed.bin").open("wb") as handle:
        for _ in range(100):
            handle.write(b"0123456789" * 100)
    assert (root / "streamed.bin").read_bytes() == b"0123456789" * 10_000


def test_modes_and_unsupported_operations(root, make_server):
    readonly = make_server(root)
    client = client_for(readonly)
    with pytest.raises(PermissionError):
        client.path("new.bin").write_bytes(b"x")
    with pytest.raises(FileExistsError):
        client.path("one.bin").open("xb")
    with pytest.raises(NotImplementedError):
        client.path("one.bin").open("ab")
    with pytest.raises(FileNotFoundError):
        list(client.path("sub").iterdir())  # a server without x-list: no directories
    with pytest.raises(NotImplementedError):
        client.path("one.bin").unlink()


def test_pure_path_behaviour(server):
    client = client_for(server)
    path = client.path("boot", "efi", "grub.cfg")
    assert path.as_posix() == "boot/efi/grub.cfg" and path.name == "grub.cfg" and path.suffix == ".cfg"
    assert path.parent == client.path("boot/efi") and (path.parent / "x").as_posix() == "boot/efi/x"
    assert client.path("\\boot\\x").as_posix() == "/boot/x" and client.path("/boot/x").is_absolute()
    assert path.relative_to("boot").as_posix() == "efi/grub.cfg"
    with pytest.raises(ValueError):
        path.relative_to("other")
    other = client_for(server, host="localhost").path("boot/efi/grub.cfg")
    assert path != other  # same name, another endpoint
    assert len({path, client.path("boot/efi/grub.cfg")}) == 1
    port = server.server_address[1]
    assert path.as_uri() == "tftp://127.0.0.1:%d/boot/efi/grub.cfg" % port
    with pytest.raises(TypeError):
        TFTPPath("x")  # no client


def test_copy_between_tftp_local_and_memory(root, server, tmp_path_factory):
    client = client_for(server)
    local = LocalPath(tmp_path_factory.mktemp("local") / "copy.bin")
    client.path("big.bin").copy(local)
    assert local.read_bytes() == (root / "big.bin").read_bytes()
    local.copy(client.path("copied-back.bin"))
    assert (root / "copied-back.bin").read_bytes() == (root / "big.bin").read_bytes()
    memory = MemPath("/m.bin")
    memory.write_bytes(b"from memory")
    memory.copy(client.path("from-mem.bin"))
    assert (root / "from-mem.bin").read_bytes() == b"from memory"


def test_uri_path(root, server, tmp_path_factory):
    base = "tftp://127.0.0.1:%d/" % server.server_address[1]
    path = URIPath(base + "big.bin")
    assert isinstance(path, TFTPURIPath)
    assert path.read_bytes() == (root / "big.bin").read_bytes()
    assert path.stat().st_size == 300_001 and path.exists()
    assert not URIPath(base + "nope").exists()
    tuned = path.with_options(blksize=8192, windowsize=8, timeout=0.5)
    assert tuned.read_bytes() == (root / "big.bin").read_bytes()
    assert URIPath(base + "text.txt;mode=netascii").read_bytes() == (root / "text.txt").read_bytes()
    URIPath(base + "uri-up.bin").write_bytes(b"over a uri")
    assert (root / "uri-up.bin").read_bytes() == b"over a uri"
    target = tmp_path_factory.mktemp("uri") / "out.bin"
    path.copy(URIPath(target.as_uri()))  # tftp: -> file:
    assert target.read_bytes() == (root / "big.bin").read_bytes()
    with pytest.raises(FileNotFoundError):
        list(URIPath(base + "sub/").iterdir())


def test_client_path_needs_no_pathlib_import_until_used():
    import subprocess
    import sys

    code = (
        "import sys, tftp; assert 'pathapi' not in sys.modules and 'uripath' not in sys.modules; print('ok')"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.stdout.strip() == "ok", out.stderr


# -- a path is synchronous: an asyncio client is refused where it is bound ------------------


def test_an_asyncio_client_has_no_path(server):
    from tftp import AsyncTFTPClient

    assert not hasattr(AsyncTFTPClient, "path")
    client = AsyncTFTPClient("127.0.0.1", server.server_address[1], timeout=0.5, retries=1)
    with pytest.raises(AttributeError):
        client.path("one.bin")


def test_a_path_refuses_an_asyncio_client_at_construction_and_in_with_client(server):
    from tftp import AsyncTFTPClient

    asynchronous = AsyncTFTPClient("127.0.0.1", server.server_address[1], timeout=0.5, retries=1)
    with pytest.raises(TypeError, match="synchronous"):
        TFTPPath("one.bin", client=asynchronous)
    with pytest.raises(TypeError, match="synchronous"):
        client_for(server).path("one.bin").with_client(asynchronous)
    uri = TFTPURIPath("tftp://127.0.0.1:%d/one.bin" % server.server_address[1])
    with pytest.raises(TypeError, match="synchronous"):
        uri.with_client(asynchronous)
    # the synchronous client still binds
    assert uri.with_client(client_for(server)).read_bytes() == b"x"


# -- A URI's options reach the transfer --------------------------------------------------


def test_uri_path_options_move_the_transfer(root, spy_server):
    spy, base = spy_server()
    big = (root / "big.bin").read_bytes()
    assert URIPath(base + "big.bin?blksize=512&windowsize=2&cookie=abc").read_bytes() == big
    wire = spy.requests[-1].options
    assert (wire["blksize"], wire["windowsize"], wire["cookie"]) == ("512", "2", "abc")
    assert URIPath(base + "big.bin;blksize=1024;windowsize=4").read_bytes() == big
    wire = spy.requests[-1].options
    assert (wire["blksize"], wire["windowsize"]) == ("1024", "4")
    URIPath(base + "uri-opts-up.bin;blksize=512;cookie=up").write_bytes(b"x" * 2000)
    assert (root / "uri-opts-up.bin").read_bytes() == b"x" * 2000
    assert (spy.requests[-1].options["blksize"], spy.requests[-1].options["cookie"]) == ("512", "up")


def test_uri_path_mode_and_name_come_from_the_parameters(root, spy_server):
    spy, base = spy_server()
    path = URIPath(base + "sub/nested.bin;mode=netascii;blksize=512")
    assert path.filename == "sub/nested.bin" and path.transfer_mode == "netascii"
    assert path.read_bytes() == b"nested"
    assert spy.requests[-1].mode == "netascii" and spy.requests[-1].filename == "sub/nested.bin"
    query = URIPath(base + "sub/nested.bin?mode=netascii&blksize=512")
    assert query.filename == "sub/nested.bin" and query.transfer_mode == "netascii"
    assert URIPath(base + "one.bin").transfer_mode == "octet"


def test_uri_path_with_options_wins_over_the_uri(root, spy_server):
    spy, base = spy_server()
    path = URIPath(base + "big.bin?blksize=512&windowsize=4")
    tuned = path.with_options(blksize=1024, timeout=0.5)
    assert tuned.read_bytes() == (root / "big.bin").read_bytes()
    wire = spy.requests[-1].options
    assert (wire["blksize"], wire["windowsize"]) == ("1024", "4")  # the keyword wins, the rest stays
    # A client that was supplied is used as it is.
    port = int(base.rsplit(":", 1)[1].split("/")[0])
    supplied = path.with_client(tftp.TFTPClient("127.0.0.1", port, blksize=2048, timeout=0.5))
    assert supplied.read_bytes() == (root / "big.bin").read_bytes()
    assert spy.requests[-1].options["blksize"] == "2048" and "windowsize" not in spy.requests[-1].options


def test_uri_path_refuses_what_the_url_grammar_refuses(root, spy_server):
    spy, base = spy_server()
    for text in (
        "big.bin?blksize=abc",
        "big.bin;blksize=abc",
        "big.bin;mode=netascii?blksize=512",
        "big.bin?a=1;b=2",
    ):
        with pytest.raises(ValueError):
            URIPath(base + text).read_bytes()
    assert spy.requests == []


class _Hostile:
    """A server whose directory lists names no directory can contain."""

    opens_fast = True

    def open_read(self, context):
        import io

        from tftp.listing import ListEntry, dumps

        name = context.filename.replace("\\", "/").strip("/")
        if context.listing and name in ("", ".", "pub"):
            entries = [
                ListEntry("ok.txt", False, 2, 1),
                ListEntry("..", True, 0, 1),
                ListEntry("../outside.txt", False, 5, 1),
                ListEntry("/abs.txt", False, 5, 1),
                ListEntry("a/b.txt", False, 5, 1),
            ]
            stream = io.BytesIO(dumps(entries))
            stream.lists_directories = True
            stream.size = len(stream.getvalue())
            return stream
        return io.BytesIO(b"ok" if name.endswith("ok.txt") else b"planted")

    def open_write(self, context, size):
        raise tftp.TFTPError(2)


def test_a_recursive_copy_creates_only_the_entries_a_directory_can_contain(make_server, tmp_path):
    """Whatever names a server lists, what lands on disk is under the copy target and is its own entry."""
    options = tftp.TFTPServerOptions(allowed=tftp.options.STANDARD_OPTIONS | tftp.options.LISTING_OPTIONS)
    client = client_for(make_server(_Hostile(), options=options))
    assert [p.name for p in client.path("pub").iterdir()] == ["ok.txt"]
    target = tmp_path / "a" / "b" / "dest"
    target.parent.mkdir(parents=True)
    client.path("pub").copy(LocalPath(str(target)), recursive=True)
    created = sorted(
        os.path.relpath(os.path.join(base, name), tmp_path)
        for base, _, files in os.walk(tmp_path)
        for name in files
    )
    assert created == [os.path.join("a", "b", "dest", "ok.txt")]
    assert (target / "ok.txt").read_bytes() == b"ok"


# -- a failed write leaves the server as it was -----------------------------------------------


def _settled(root, expected, seconds=5.0):
    """The names in ``root`` once they equal ``expected`` (polled: the server acts after the client returns)."""
    import time

    deadline = time.monotonic() + seconds
    while True:
        names = sorted(os.listdir(root))
        if names == expected or time.monotonic() > deadline:
            return names
        time.sleep(0.02)


def _path_threads():
    import threading

    return [t for t in threading.enumerate() if t.name == "tftp-path"]


def _targets(server):
    client = client_for(server)
    base = "tftp://127.0.0.1:%d/" % server.server_address[1]
    return {
        "path": lambda name: client.path(name),
        "uri": lambda name: URIPath(base + name),
    }


@pytest.mark.parametrize("kind", ["path", "uri"])
@pytest.mark.parametrize("flush", [False, True])
def test_a_with_block_that_raises_leaves_the_servers_file_as_it_was(root, server, kind, flush):
    (root / "keep.bin").write_bytes(b"the complete previous file")
    before = sorted(os.listdir(root))
    threads = len(_path_threads())
    target = _targets(server)[kind]
    for name in ("keep.bin", "fresh.bin"):
        with pytest.raises(RuntimeError):
            with target(name).open("wb") as handle:
                handle.write(b"first half of the file;" * (200 if flush else 1))
                if flush:
                    handle.flush()
                raise RuntimeError("the producer failed")
    assert _settled(root, before) == before  # no fresh.bin, and no temporary file
    assert (root / "keep.bin").read_bytes() == b"the complete previous file"
    assert len(_path_threads()) == threads  # the upload's thread was joined


def test_a_block_that_completes_still_commits(root, server):
    client = client_for(server)
    with client.path("done.bin").open("wb") as handle:
        handle.write(b"all of it")
    assert (root / "done.bin").read_bytes() == b"all of it"
    assert _path_threads() == []


class _FailsHalfWay(io.RawIOBase):
    """A binary source that raises once ``limit`` octets have been read."""

    def __init__(self, inner, limit=70_000):
        self.inner, self.limit, self.given = inner, limit, 0

    def readable(self):
        return True

    def close(self):
        self.inner.close()
        super().close()

    def readinto(self, buffer):
        if self.given >= self.limit:
            raise OSError(5, "the source failed half-way")
        n = self.inner.readinto(buffer)
        self.given += n or 0
        return n


class _FlakyLocal(LocalPath):
    def open(self, mode="r", *args, **kwargs):
        handle = super().open(mode, *args, **kwargs)
        if "r" in mode and "b" in mode:
            return io.BufferedReader(_FailsHalfWay(handle))
        return handle


def test_a_copy_whose_source_fails_leaves_the_target_as_it_was(root, server, tmp_path_factory):
    source = tmp_path_factory.mktemp("flaky") / "src.bin"
    source.write_bytes(b"A" * 200_000)
    (root / "keep.bin").write_bytes(b"the complete previous file")
    before = sorted(os.listdir(root))
    client = client_for(server)
    for name in ("keep.bin", "fresh.bin"):
        with pytest.raises(OSError):
            _FlakyLocal(source).copy(client.path(name), overwrite=True)
    assert _settled(root, before) == before
    assert (root / "keep.bin").read_bytes() == b"the complete previous file"
    assert _path_threads() == []


# -- a text-mode write that fails, or is never closed, sends nothing ----------------------------


@pytest.mark.parametrize("kind", ["path", "uri"])
def test_a_text_with_block_that_raises_leaves_the_servers_file_as_it_was(root, server, kind):
    (root / "keep.txt").write_bytes(b"the complete previous file")
    before = sorted(os.listdir(root))
    target = _targets(server)[kind]
    for name in ("keep.txt", "fresh.txt"):
        with pytest.raises(RuntimeError):
            with target(name).open("w") as handle:
                handle.write("PARTIAL")
                raise RuntimeError("the producer failed")
    assert _settled(root, before) == before
    assert (root / "keep.txt").read_bytes() == b"the complete previous file"
    assert _path_threads() == []


@pytest.mark.parametrize("kind", ["path", "uri"])
def test_a_text_write_that_cannot_be_encoded_leaves_the_servers_file_as_it_was(root, server, kind):
    (root / "keep.txt").write_bytes(b"the complete previous file")
    before = sorted(os.listdir(root))
    target = _targets(server)[kind]
    for name in ("keep.txt", "fresh.txt"):
        with pytest.raises(UnicodeEncodeError):
            target(name).write_text("é", encoding="ascii")
    assert _settled(root, before) == before
    assert (root / "keep.txt").read_bytes() == b"the complete previous file"
    assert _path_threads() == []


@pytest.mark.parametrize("kind", ["path", "uri"])
@pytest.mark.parametrize("text", [False, True], ids=["binary", "text"])
def test_a_file_collected_unclosed_is_discarded_with_one_resource_warning(root, server, kind, text):
    import gc

    (root / "keep.txt").write_bytes(b"the complete previous file")
    before = sorted(os.listdir(root))
    target = _targets(server)[kind]
    for name in ("keep.txt", "fresh.txt"):
        handle = target(name).open("w" if text else "wb")
        handle.write("half" if text else b"half")
        with pytest.warns(ResourceWarning) as caught:
            del handle
            gc.collect()
        assert len([w for w in caught if issubclass(w.category, ResourceWarning)]) == 1
    assert _settled(root, before) == before
    assert (root / "keep.txt").read_bytes() == b"the complete previous file"
    assert _path_threads() == []


@pytest.mark.parametrize("kind", ["path", "uri"])
def test_a_text_write_that_completes_still_commits(root, server, kind):
    target = _targets(server)[kind]
    with target("done.txt").open("w", newline="\n") as handle:
        handle.write("all of it\n")
    assert (root / "done.txt").read_bytes() == b"all of it\n"
    target("again.txt").write_text("through write_text", newline="\n")
    assert (root / "again.txt").read_bytes() == b"through write_text"
    assert _path_threads() == []


# -- copy(overwrite=True), unlink and move -------------------------------------------------


@pytest.mark.parametrize("kind", ["path", "uri"])
def test_copy_with_overwrite_replaces_the_servers_file(root, server, tmp_path_factory, kind):
    local = tmp_path_factory.mktemp("over") / "new.bin"
    local.write_bytes(b"new content, longer than the old")
    (root / "existing.bin").write_bytes(b"old")
    target = _targets(server)[kind]("existing.bin")
    with pytest.raises(FileExistsError):
        LocalPath(local).copy(target)
    assert (root / "existing.bin").read_bytes() == b"old"
    LocalPath(local).copy(target, overwrite=True)
    assert (root / "existing.bin").read_bytes() == b"new content, longer than the old"


@pytest.mark.parametrize("kind", ["path", "uri"])
def test_unlink_deletes_nothing_and_says_so(root, server, kind):
    target = _targets(server)[kind]("one.bin")
    for missing_ok in (False, True):
        with pytest.raises(NotImplementedError):
            target.unlink(missing_ok=missing_ok)
    assert (root / "one.bin").read_bytes() == b"x"


@pytest.mark.parametrize("kind", ["path", "uri"])
def test_move_from_tftp_is_not_supported_and_keeps_the_source(root, server, tmp_path_factory, kind):
    destination = tmp_path_factory.mktemp("moved") / "out.bin"
    source = _targets(server)[kind]("one.bin")
    with pytest.raises(NotImplementedError):
        source.move(LocalPath(destination))
    assert (root / "one.bin").read_bytes() == b"x"
    assert not destination.exists()  # refused before anything was copied
    assert not supports(source, Remove)


# -- names, hooks and modes ---------------------------------------------------------------


def test_as_uri_keeps_the_leading_slash(server):
    client = client_for(server)
    port = server.server_address[1]
    assert client.path("boot/x").as_uri() == "tftp://127.0.0.1:%d/boot/x" % port
    assert client.path("/boot/x").as_uri() == "tftp://127.0.0.1:%d//boot/x" % port
    assert tftp.TFTPURL.parse(client.path("/boot/x").as_uri()).filename == "/boot/x"
    assert tftp.TFTPURL.parse(client.path("boot/x").as_uri()).filename == "boot/x"


def test_a_clients_own_hook_fires_for_path_reads_and_writes(root, server):
    seen = []
    client = client_for(server, on_negotiated=lambda negotiated, peer: seen.append(peer))
    client.get("one.bin")
    assert len(seen) == 1
    assert client.path("one.bin").read_bytes() == b"x"
    assert len(seen) == 2
    client.path("hooked.bin").write_bytes(b"w")
    assert len(seen) == 3 and (root / "hooked.bin").read_bytes() == b"w"


def test_a_clients_own_hook_that_refuses_fails_the_open(root, server):
    def refuse(negotiated, peer):
        raise tftp.TFTPError(tftp.TFTPErrorCode.ACCESS_VIOLATION, "no")

    client = client_for(server, on_negotiated=refuse)
    with pytest.raises(OSError):
        client.path("one.bin").open("rb")
    assert _path_threads() == []


def test_a_path_validates_its_mode_at_construction(server):
    client = client_for(server)
    for bad in ("bogus", "mail"):
        with pytest.raises(ValueError):
            TFTPPath("x", client=client, mode=bad)
        with pytest.raises(ValueError):
            client.path("x").with_mode(bad)
    with pytest.raises(TypeError):
        TFTPPath("x", client=client, mode=None)
    assert TFTPPath("x", client=client, mode="NetAscii").transfer_mode == "netascii"


def test_a_uri_path_refuses_text_the_url_parser_refuses(root, spy_server):
    spy, base = spy_server()
    for text in ("x;mode=mail", "x;bogus", "x;mode=octet;mode=netascii"):
        with pytest.raises(tftp.TFTPValueError):
            URIPath(base + text).filename
        with pytest.raises(ValueError):
            tftp.TFTPURL.parse(base + text)
    assert spy.requests == []


# -- what the server reports, and what supports() says about it ---------------------------------


@pytest.mark.parametrize("kind", ["path", "uri"])
def test_stat_says_when_the_server_gave_no_size(root, make_server, kind):
    no_tsize = tftp.TFTPServerOptions(allowed=tftp.options.STANDARD_OPTIONS - {"tsize"})
    server = make_server(root, writable=True, overwrite=True, options=no_tsize)
    target = _targets(server)[kind]
    unknown = target("big.bin").stat()  # three blocks of 1428 and more: no tsize, no short first block
    assert unknown.size_known is False and unknown.st_size == 0
    small = target("one.bin").stat()  # the whole file fits in the first block, so its size is known
    assert small.size_known is True and small.st_size == 1


@pytest.mark.parametrize("kind", ["path", "uri"])
def test_stat_with_a_size_is_known(root, server, kind):
    known = _targets(server)[kind]("big.bin").stat()
    assert known.size_known is True and known.st_size == 300_001


def _listing_requests(spy):
    return [request for request in spy.requests if request.filename == "."]


def test_listing_support_follows_the_server_and_is_asked_once_per_client(spy_server):
    spy, base = spy_server(options=LISTING)
    port = int(base.rsplit(":", 1)[1].rstrip("/"))
    client = tftp.TFTPClient("127.0.0.1", port, timeout=0.5, retries=1)
    first = client.path("one.bin")
    for path in (first, client.path("sub", "nested.bin"), first / "x", first.with_mode("netascii"), first):
        assert supports(path, Listing) is True
    assert len(_listing_requests(spy)) == 1
    other = tftp.TFTPClient("127.0.0.1", port, timeout=0.5, retries=1)
    assert supports(other.path("one.bin"), Listing) is True
    assert len(_listing_requests(spy)) == 2  # another client asks for itself


def test_a_server_without_listing_is_not_listed_and_the_uri_paths_of_an_endpoint_ask_once(spy_server):
    spy, base = spy_server()
    path = URIPath(base + "one.bin")
    assert supports(path, Listing) is False
    assert supports(path / "x", Listing) is False and supports(path.parent, Listing) is False
    assert len(_listing_requests(spy)) == 1


def test_a_server_that_cannot_be_asked_leaves_listing_to_the_class():
    silent = FakePeer()
    try:
        path = tftp.TFTPClient("127.0.0.1", silent.port, timeout=0.1, retries=1).path("x")
        assert path._supports(Listing) is None  # the probe failed: no answer, and no exception
        assert supports(path, Listing) is True  # the class does list; a call will tell
    finally:
        silent.close()


def test_only_listing_depends_on_the_server(server):
    path = client_for(server).path("x")
    assert path._supports(Remove) is None
