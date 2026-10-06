"""The client against the server over real loopback sockets."""

from __future__ import annotations

import errno
import io
import os
import sys
import time

import pytest

import tftp
from conftest import BAD_FIRST_ANSWERS, FakePeer, client_for, needs_ipv6
from tftp.netascii import encode

NAMES = ["empty.bin", "one.bin", "511.bin", "512.bin", "513.bin", "1428x3.bin", "big.bin"]
SHAPES = [
    pytest.param({"blksize": None}, id="rfc1350"),
    pytest.param({"blksize": 1428}, id="blksize1428"),
    pytest.param({"blksize": 8, "tsize": False}, id="blksize8"),
    pytest.param({"blksize": 65464}, id="blksize-max"),
    pytest.param({"blksize": 1024, "windowsize": 4}, id="window4"),
    pytest.param({"blksize": 512, "windowsize": 64}, id="window64"),
]


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("name", NAMES)
def test_download(root, make_server, name, shape):
    if shape.get("blksize") == 8 and name == "big.bin":
        pytest.skip("37,500 lock-step round trips add nothing over the smaller files")
    server = make_server(root)
    expected = (root / name).read_bytes()
    result_sink = io.BytesIO()
    result = client_for(server, **shape).download(name, result_sink)
    assert result_sink.getvalue() == expected
    assert result.is_ok and result.bytes == len(expected) and result.operation == "read"
    if shape.get("tsize", True) and shape.get("blksize") is not None:
        assert result.negotiated.tsize == (len(expected) or None)


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("name", ["empty.bin", "512.bin", "513.bin", "big.bin"])
def test_upload(root, make_server, name, shape):
    if shape.get("blksize") == 8 and name == "big.bin":
        pytest.skip("37,500 lock-step round trips add nothing over the smaller files")
    server = make_server(root, writable=True)
    data = (root / name).read_bytes()
    result = client_for(server, **shape).upload("up-" + name, data)
    assert (root / ("up-" + name)).read_bytes() == data
    assert result.is_ok and result.bytes == len(data) and result.operation == "write"


def test_download_to_path_and_failure_cleans_up(root, make_server, tmp_path_factory):
    server = make_server(root)
    out = tmp_path_factory.mktemp("out")
    client = client_for(server)
    client.download("big.bin", out / "big.bin")
    assert (out / "big.bin").read_bytes() == (root / "big.bin").read_bytes()
    with pytest.raises(tftp.RemoteError):
        client.download("missing.bin", out / "missing.bin")
    assert not (out / "missing.bin").exists()


def test_upload_from_path_and_file(root, make_server, tmp_path_factory):
    server = make_server(root, writable=True)
    src = tmp_path_factory.mktemp("src") / "data.bin"
    src.write_bytes(os.urandom(5000))
    client = client_for(server)
    client.upload("from-path.bin", src)
    with open(src, "rb") as handle:
        client.upload("from-file.bin", handle)
    assert (root / "from-path.bin").read_bytes() == src.read_bytes()
    assert (root / "from-file.bin").read_bytes() == src.read_bytes()


@needs_ipv6
@pytest.mark.parametrize("host", ["::1", "127.0.0.1"])
def test_dual_stack_listener(root, make_server, host):
    server = make_server(root, host="::")
    if host == "127.0.0.1" and not server.is_dual_stack:
        pytest.skip("this platform does not allow dual-stack sockets")
    result = client_for(server, host=host).download("513.bin", io.BytesIO())
    assert result.bytes == 513
    assert result.peer[0] == host


@needs_ipv6
def test_ipv6_only_listener(root, make_server):
    server = make_server(root, host="::1")
    assert client_for(server, host="::1").get("one.bin") == b"x"


def test_ipv4_listener_and_v6_client_family(root, make_server):
    server = make_server(root, host="127.0.0.1")
    import socket

    assert client_for(server, family=socket.AF_INET).get("one.bin") == b"x"


def test_netascii_both_ways(root, make_server):
    server = make_server(root, writable=True)
    text = (root / "text.txt").read_bytes()
    client = client_for(server, blksize=8)
    raw = io.BytesIO()
    result = client.download("text.txt", raw, mode="netascii")
    assert raw.getvalue() == text
    assert result.bytes == len(encode(text))
    assert result.negotiated.tsize is None  # a netascii size needs the whole file read
    client.upload("text-up.txt", text, mode="netascii")
    assert (root / "text-up.txt").read_bytes() == text


def test_mode_aliases_and_bad_mode(root, make_server):
    server = make_server(root)
    client = client_for(server)
    assert client.get("one.bin", mode="binary") == b"x"
    with pytest.raises(ValueError):
        client.get("one.bin", mode="mail")


def test_negotiation_is_clamped_by_server_policy(root, make_server):
    server = make_server(root, options=tftp.TFTPServerOptions(max_blksize=1000, max_windowsize=2))
    result = client_for(server, blksize=65464, windowsize=50).download("big.bin", io.BytesIO())
    assert (result.negotiated.blksize, result.negotiated.windowsize) == (1000, 2)


def test_fractional_timeout_negotiates_utimeout(root, make_server):
    server = make_server(root, options=tftp.Profile.HPA.server)
    result = client_for(server, timeout=0.25, utimeout=True).download("one.bin", io.BytesIO())
    assert result.negotiated.options.get("utimeout") == "250000"
    plain = make_server(root)  # extension not allowed by default: left out
    result = client_for(plain, timeout=0.25, utimeout=True).download("one.bin", io.BytesIO())
    assert "utimeout" not in result.negotiated.options


def test_progress_reports_bytes_and_total(root, make_server):
    server = make_server(root)
    seen = []
    client_for(server).download(
        "big.bin", io.BytesIO(), progress=lambda done, total: seen.append((done, total))
    )
    assert seen[-1] == (300_001, 300_001)
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)


def test_on_complete_reports_every_transfer(root, make_server):
    results = []
    server = make_server(root, on_complete=results.append)
    client = client_for(server)
    client.get("one.bin")
    with pytest.raises(tftp.RemoteError):
        client.get("missing.bin")
    deadline = time.monotonic() + 2
    while len(results) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert [r.is_ok for r in results] == [True, False]
    assert results[1].error.code == tftp.TFTPErrorCode.FILE_NOT_FOUND


@pytest.mark.parametrize(
    "name,code",
    [
        ("missing.bin", tftp.TFTPErrorCode.FILE_NOT_FOUND),
        ("sub", tftp.TFTPErrorCode.FILE_NOT_FOUND),
        ("../outside", tftp.TFTPErrorCode.ACCESS_VIOLATION),
        ("sub/../../outside", tftp.TFTPErrorCode.ACCESS_VIOLATION),
        ("..\\outside", tftp.TFTPErrorCode.ACCESS_VIOLATION),
    ],
)
def test_download_errors(root, make_server, name, code):
    server = make_server(root)
    with pytest.raises(tftp.RemoteError) as info:
        client_for(server).get(name)
    assert info.value.code == code


def test_absolute_and_backslash_names_stay_inside_root(root, make_server):
    server = make_server(root)
    client = client_for(server)
    assert client.get("/sub/nested.bin") == b"nested"
    assert client.get("\\sub\\nested.bin") == b"nested"


def test_upload_policy(root, make_server):
    readonly = make_server(root)
    with pytest.raises(tftp.RemoteError) as info:
        client_for(readonly).put("new.bin", b"x")
    assert info.value.code == tftp.TFTPErrorCode.ACCESS_VIOLATION

    writable = make_server(root, writable=True)
    with pytest.raises(tftp.RemoteError) as info:
        client_for(writable).put("one.bin", b"y")
    assert info.value.code == tftp.TFTPErrorCode.FILE_EXISTS
    assert (root / "one.bin").read_bytes() == b"x"

    overwriting = make_server(root, writable=True, overwrite=True)
    client_for(overwriting).put("one.bin", b"y")
    assert (root / "one.bin").read_bytes() == b"y"

    no_create = make_server(root, writable=True, create=False)
    with pytest.raises(tftp.RemoteError) as info:
        client_for(no_create).put("brand-new.bin", b"z")
    assert info.value.code == tftp.TFTPErrorCode.FILE_NOT_FOUND


def test_failed_upload_leaves_nothing_behind(root, make_server):
    server = make_server(root, writable=True)

    class Failing(io.RawIOBase):
        def __init__(self):
            self.calls = 0

        def readable(self):
            return True

        def readinto(self, buffer):
            self.calls += 1
            if self.calls > 3:
                raise OSError("source went away")
            buffer[: len(buffer)] = b"a" * len(buffer)
            return len(buffer)

    with pytest.raises(OSError, match="source went away"):
        client_for(server, blksize=512, tsize=False).upload("partial.bin", Failing())
    deadline = time.monotonic() + 2
    while server.active_sessions and time.monotonic() < deadline:
        time.sleep(0.01)
    assert sorted(p.name for p in root.iterdir() if "partial" in p.name) == []


def test_max_upload(root, make_server):
    handler = tftp.FilesystemBackend(root, writable=True, max_upload=1000)
    server = make_server(handler)
    with pytest.raises(tftp.RemoteError) as info:
        client_for(server).put("big-up.bin", os.urandom(2000))
    assert info.value.code == tftp.TFTPErrorCode.DISK_FULL
    with pytest.raises(tftp.RemoteError):
        client_for(server, tsize=False).put("big-up.bin", os.urandom(2000))
    assert not (root / "big-up.bin").exists()
    client_for(server).put("small-up.bin", b"ok")
    assert (root / "small-up.bin").read_bytes() == b"ok"


def test_custom_handler_serves_generated_content(make_server):
    class Generated:
        def open_read(self, context):
            body = ("hello %s from %s" % (context.filename, context.peer[0])).encode()
            return io.BytesIO(body)

        def open_write(self, context, size):
            raise tftp.TFTPError(tftp.TFTPErrorCode.ACCESS_VIOLATION, "no uploads here")

    server = make_server(Generated())
    client = client_for(server)
    assert client.get("menu.cfg") == b"hello menu.cfg from 127.0.0.1"
    with pytest.raises(tftp.RemoteError) as info:
        client.put("x", b"y")
    assert info.value.message == "no uploads here"


def test_handler_bug_becomes_error_0(make_server):
    class Buggy:
        def open_read(self, context):
            raise ZeroDivisionError

    server = make_server(Buggy())
    with pytest.raises(tftp.RemoteError) as info:
        client_for(server).get("x")
    assert info.value.code == tftp.TFTPErrorCode.NOT_DEFINED


def test_timeout_against_silent_port():
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as silent:
        silent.bind(("127.0.0.1", 0))
        client = tftp.TFTPClient("127.0.0.1", silent.getsockname()[1], timeout=0.05, retries=2)
        with pytest.raises(tftp.TransferTimeoutError):
            client.get("x")


def test_many_concurrent_clients(root, make_server):
    import threading

    server = make_server(root)
    expected = (root / "big.bin").read_bytes()
    errors = []

    def fetch():
        try:
            assert client_for(server, windowsize=4).get("big.bin") == expected
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=fetch) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []


def test_server_lifecycle(root):
    server = tftp.TFTPServer(root, host="127.0.0.1", port=0)
    with server:
        server.start()
        with pytest.raises(RuntimeError):
            server.start()
        assert client_for(server).get("one.bin") == b"x"
    with pytest.raises(RuntimeError):
        server.serve_forever()
    assert "active" in repr(server)


def test_server_stats(root, make_server):
    server = make_server(root, writable=True)
    client = client_for(server)
    client.get("513.bin")
    client.put("stats-up.bin", b"x" * 100)
    with pytest.raises(tftp.FileNotFound):
        client.get("missing")
    deadline = time.monotonic() + 3
    while server.stats["completed"] < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    snapshot = server.stats_snapshot()
    assert snapshot["requests"] == 3 and snapshot["refused"] == 1
    assert (snapshot["started"], snapshot["completed"], snapshot["failed"]) == (2, 2, 0)
    assert snapshot["bytes_sent"] == 513 and snapshot["bytes_received"] == 100
    assert "active" in snapshot


def test_size_probe(root, make_server):
    results = []
    server = make_server(root, on_complete=results.append)
    client = client_for(server)
    assert client.size("big.bin") == 300_001
    assert client.size("one.bin") == 1
    with pytest.raises(tftp.FileNotFound):
        client.size("missing")
    plain = make_server(root, options=tftp.TFTPServerOptions(allowed=()))  # no tsize: DATA 1 instead
    assert client_for(plain).size("511.bin") == 511  # fits in the first block
    assert client_for(plain).size("big.bin") is None
    # The probe never completes a transfer on the server.
    deadline = time.monotonic() + 2
    while len(results) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not any(r.is_ok for r in results if r.filename == "big.bin")


def test_size_probes_count_as_declined(root, make_server):
    server = make_server(root)
    client = client_for(server)
    client.size("big.bin")
    client.get("one.bin")
    deadline = time.monotonic() + 3
    while server.stats["declined"] + server.stats["completed"] < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert (server.stats["declined"], server.stats["completed"], server.stats["failed"]) == (1, 1, 0)


def test_operands_of_download_and_upload_are_named_dst_and_src(root, make_server, tmp_path_factory):
    server = make_server(root, writable=True)
    out = tmp_path_factory.mktemp("out")
    client = client_for(server)
    client.download("one.bin", dst=out / "one.bin")
    assert (out / "one.bin").read_bytes() == (root / "one.bin").read_bytes()
    client.upload("named.bin", src=b"payload")
    assert (root / "named.bin").read_bytes() == b"payload"


def test_a_one_shot_upload_passes_src_to_the_client_as_the_source_address(root, make_server):
    server = make_server(root, writable=True, on_complete=lambda result: seen.append(result.peer))
    seen = []
    port = server.server_address[1]
    tftp.upload("127.0.0.1", "oneshot.bin", b"data", port=port, src=("127.0.0.1", 0), timeout=0.5)
    tftp.download("127.0.0.1", "oneshot.bin", io.BytesIO(), port=port, src=("127.0.0.1", 0), timeout=0.5)
    assert (root / "oneshot.bin").read_bytes() == b"data"
    assert wait_until(lambda: len(seen) == 2)
    assert {peer[0] for peer in seen} == {"127.0.0.1"}


def wait_until(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        time.sleep(0.01)
    return predicate()


_BAD_TYPE = [
    {"port": "69"},
    {"port": 69.0},
    {"port": True},
    {"retries": 1.5},
    {"retries": True},
    {"retries": "5"},
    {"timeout": "1"},
    {"timeout": True},
    {"max_timeout": "8"},
    {"deadline": "3"},
    {"deadline": True},
    {"backoff": "2"},
    {"family": "6"},
    {"family": 2.0},
    {"src": "127.0.0.1"},
    {"src": 5},
    {"src": ("127.0.0.1", "0")},
    {"src": ("127.0.0.1", True)},
    {"src": (None, 0)},
    {"src": (12345, 0)},
]
_BAD_VALUE = [
    {"port": 0},
    {"port": -1},
    {"port": 70000},
    {"retries": -1},
    {"timeout": float("nan")},
    {"timeout": float("inf")},
    {"deadline": 0},
    {"deadline": -1},
    {"deadline": float("nan")},
    {"family": 99},
    {"src": ("127.0.0.1",)},
    {"src": ("127.0.0.1", 0, 0)},
    {"src": ("127.0.0.1", 70000)},
    {"src": ("127.0.0.1", -1)},
    {"backoff": 0.5},
    {"backoff": float("nan")},
]


def _client_classes():
    from tftp import AsyncTFTPClient

    return pytest.mark.parametrize("cls", [tftp.TFTPClient, AsyncTFTPClient])


@_client_classes()
@pytest.mark.parametrize("bad", _BAD_TYPE, ids=repr)
def test_a_wrong_type_for_a_client_argument_is_refused_at_construction(cls, bad):
    with pytest.raises(TypeError):
        cls("127.0.0.1", **bad)


@_client_classes()
@pytest.mark.parametrize("bad", _BAD_VALUE, ids=repr)
def test_a_wrong_value_for_a_client_argument_is_refused_at_construction(cls, bad):
    with pytest.raises(ValueError):
        cls("127.0.0.1", **bad)


@_client_classes()
def test_the_constructor_accepts_what_a_transfer_takes_and_resolves_nothing(cls):
    import socket

    client = cls(
        "no-such-host.invalid",
        6969,
        timeout=2,
        retries=0,
        deadline=30,
        family=socket.AF_INET6,
        src=["::1", 0],
        backoff=1,
        max_timeout=16,
    )
    assert (client.port, client.retries, client.deadline, client.backoff) == (6969, 0, 30, 1)
    assert client.src == ("::1", 0)
    assert cls("127.0.0.1", deadline=None, src=None, family=socket.AF_INET).src is None


def test_a_port_in_the_host_text_still_overrides_a_valid_port_argument():
    assert tftp.TFTPClient("h:70", 69)._target() == ("h", 70)


# -- the first answer and the time limit ----------------------------------------------------------


class CountingSource(io.BytesIO):
    """A source that counts what the transfer reads from it."""

    reads = 0

    def readinto(self, view) -> int:
        CountingSource.reads += 1
        return super().readinto(view)


@pytest.mark.parametrize("is_read, answer", [pytest.param(r, a, id=i) for i, r, a in BAD_FIRST_ANSWERS])
def test_a_first_answer_a_server_may_not_send_is_a_protocol_error(is_read, answer):
    CountingSource.reads = 0
    with FakePeer(lambda data: [answer]) as peer:
        client = tftp.TFTPClient("127.0.0.1", peer.port, timeout=1.0, retries=1)
        started = time.monotonic()
        with pytest.raises(tftp.TFTPProtocolError):
            if is_read:
                client.get("f")
            else:
                client.upload("f", CountingSource(b"payload"))
        assert time.monotonic() - started < 5, "the client waited for a transfer that cannot start"
        is_error = answer[:2] == b"\x00\x05"
        assert wait_until(lambda: len(peer.seen) >= 2 or is_error), "the server was told nothing"
        sent = [tftp.decode(data) for _, data in peer.seen[1:]]
    assert not any(isinstance(p, tftp.DataPacket) for p in sent), "an upload began after a bad first answer"
    assert CountingSource.reads == 0, "the source was read for an upload that never started"
    if not is_error:
        assert isinstance(sent[0], tftp.ErrorPacket) and sent[0].code == tftp.TFTPErrorCode.ILLEGAL_OPERATION


@pytest.mark.parametrize("call", ["get", "size", "stat"])
def test_the_time_limit_bounds_every_call_that_waits_for_a_server(call):
    with FakePeer() as peer:
        client = tftp.TFTPClient("127.0.0.1", peer.port, timeout=3.0, retries=3, deadline=0.5)
        started = time.monotonic()
        with pytest.raises(tftp.TransferTimeoutError):
            getattr(client, call)("f")
        elapsed = time.monotonic() - started
    # A second of margin over the 0.5 s limit; the wait unbounded by it is 3 s a request.
    assert 0.4 <= elapsed < 1.5


def test_the_time_limit_is_one_start_across_the_fallback_to_a_request_without_options():
    from tftp.packet import encode_error

    def script(data):
        request = tftp.decode(data)
        if isinstance(request, tftp.RequestPacket) and request.options:
            return [(0.8, encode_error(tftp.TFTPErrorCode.OPTION_REFUSED))]
        return []

    with FakePeer(script) as peer:
        client = tftp.TFTPClient("127.0.0.1", peer.port, timeout=3.0, retries=3, deadline=1.5)
        started = time.monotonic()
        with pytest.raises(tftp.TransferTimeoutError):
            client.get("f")
        elapsed = time.monotonic() - started
    # 0.8 s in the first request, so 0.7 s are left for the second; a fresh limit would end it near 2.3 s.
    assert 1.4 <= elapsed < 2.3


# -- the destination file, local failures and sizes ------------------------------------------------


def _data_for_ever(data):
    from tftp.packet import encode_data

    packet = tftp.decode(data)
    if isinstance(packet, tftp.RequestPacket):
        return [encode_data(1, b"x" * 512)]
    if isinstance(packet, tftp.AckPacket):
        return [encode_data(packet.block + 1, b"x" * 512)]
    return []


OLD = b"the previous good copy"


def _beside(directory):
    return sorted(p.name for p in directory.iterdir())


@pytest.fixture
def dest(tmp_path):
    """A destination that already holds a file, in a directory of its own."""
    directory = tmp_path / "out"
    directory.mkdir()
    path = directory / "important.cfg"
    path.write_bytes(OLD)
    return path


def _refused(code):
    from tftp.packet import encode_error

    return lambda data: [encode_error(code)]


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda port: ("127.0.0.1", port, {}, "f"), id="server-says-not-found"),
        pytest.param(lambda port: (None, port, {}, "f"), id="host-is-none"),
        pytest.param(lambda port: ("h:+70", port, {}, "f"), id="host-cannot-be-parsed"),
        pytest.param(lambda port: ("127.0.0.1", 1, {"timeout": 0.1, "retries": 1}, "f"), id="nobody-answers"),
    ],
)
def test_a_failed_download_to_a_path_leaves_the_file_that_was_there(dest, build):
    with FakePeer(_refused(tftp.TFTPErrorCode.FILE_NOT_FOUND)) as peer:
        host, port, options, name = build(peer.port)
        client = tftp.TFTPClient(host, port, **dict({"timeout": 0.5, "retries": 1}, **options))
        with pytest.raises(Exception):
            client.download(name, dest)
    assert dest.read_bytes() == OLD
    assert _beside(dest.parent) == ["important.cfg"]


def test_a_download_to_a_path_replaces_the_file_on_success(root, make_server, dest):
    server = make_server(root)
    result = client_for(server).download("big.bin", dest)
    assert dest.read_bytes() == (root / "big.bin").read_bytes() and result.bytes == 300_001
    assert _beside(dest.parent) == ["important.cfg"]


def test_a_download_to_a_new_path_is_created_with_the_modes_open_gives(root, make_server, tmp_path):
    server = make_server(root)
    client_for(server).download("one.bin", tmp_path / "fresh.bin")
    plain = tmp_path / "plain.bin"
    plain.write_bytes(b"")
    if os.name == "posix":
        assert (tmp_path / "fresh.bin").stat().st_mode & 0o777 == plain.stat().st_mode & 0o777


def test_a_download_that_fails_while_data_arrives_leaves_the_file_and_no_temporary(dest):
    with FakePeer(_data_for_ever) as peer:
        client = tftp.TFTPClient("127.0.0.1", peer.port, timeout=0.5, retries=1, blksize=None, deadline=0.3)
        with pytest.raises(tftp.TransferTimeoutError):
            client.download("f", dest)
        assert len(peer.seen) > 2, "no data had arrived when the transfer failed"
    assert dest.read_bytes() == OLD
    assert _beside(dest.parent) == ["important.cfg"]


def test_a_download_into_a_directory_that_does_not_exist_sends_nothing(tmp_path):
    with FakePeer() as peer:
        client = tftp.TFTPClient("127.0.0.1", peer.port, timeout=0.2, retries=1)
        with pytest.raises(FileNotFoundError):
            client.download("f", tmp_path / "missing" / "x.bin")
        assert peer.seen == []
    assert _beside(tmp_path) == []


def test_a_download_to_a_device_is_written_in_place(root, make_server, tmp_path):
    server = make_server(root)
    result = client_for(server).download("big.bin", os.devnull)
    assert result.bytes == 300_001
    assert [n for n in os.listdir(".") if n.endswith(".part")] == []


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no named pipes here")
def test_a_download_to_a_named_pipe_is_written_in_place(root, make_server, tmp_path_factory):
    import threading

    where = tmp_path_factory.mktemp("fifo")
    pipe = where / "pipe"
    os.mkfifo(pipe)
    got = []
    reader = threading.Thread(target=lambda: got.append(pipe.open("rb").read()), daemon=True)
    reader.start()
    client_for(make_server(root)).download("one.bin", pipe)
    reader.join(5)
    assert got == [b"x"] and _beside(where) == ["pipe"]


def test_a_download_over_a_file_that_cannot_be_replaced_leaves_it_and_no_temporary(root, make_server, dest):
    server = make_server(root)
    with open(dest, "rb"):  # on Windows a file another handle has open cannot be replaced
        try:
            client_for(server).download("one.bin", dest)
            replaced = True
        except OSError as exc:
            replaced = False
            assert not isinstance(exc, tftp.TFTPError)
    assert replaced is (sys.platform != "win32")
    assert dest.read_bytes() == (OLD if not replaced else b"x")
    assert _beside(dest.parent) == ["important.cfg"]


class _DiskFull:
    def write(self, data):
        raise OSError(errno.ENOSPC, "No space left on device")


def test_a_local_failure_is_raised_as_itself_and_the_server_is_told():
    from tftp.packet import encode_data

    with FakePeer(lambda data: [encode_data(1, b"x" * 512)]) as peer:
        client = tftp.TFTPClient("127.0.0.1", peer.port, timeout=0.5, retries=1, blksize=None)
        with pytest.raises(OSError) as info:
            client.download("f", _DiskFull())
        assert info.value.errno == errno.ENOSPC and not isinstance(info.value, tftp.TFTPError)
        assert wait_until(lambda: len(peer.seen) >= 2)
        told = tftp.decode(peer.seen[1][1])
    assert isinstance(told, tftp.ErrorPacket) and told.code == tftp.TFTPErrorCode.DISK_FULL


def test_a_failing_source_is_raised_as_itself():
    class Broken(io.RawIOBase):
        def readinto(self, view):
            raise OSError(errno.EIO, "input/output error")

    with FakePeer(lambda data: [b"\x00\x04\x00\x00"]) as peer:
        client = tftp.TFTPClient("127.0.0.1", peer.port, timeout=0.5, retries=1, blksize=None)
        with pytest.raises(OSError) as info:
            client.upload("f", Broken())
    assert info.value.errno == errno.EIO and not isinstance(info.value, tftp.TFTPError)


def test_max_size_ends_a_download_the_server_does_not_end():
    sink = io.BytesIO()
    with FakePeer(_data_for_ever) as peer:
        client = tftp.TFTPClient("127.0.0.1", peer.port, timeout=0.5, retries=1, blksize=None, max_size=700)
        with pytest.raises(tftp.TransferTooLargeError):
            client.download("f", sink)
        assert wait_until(lambda: len(peer.seen) >= 3)
        told = tftp.decode(peer.seen[2][1])
    assert isinstance(told, tftp.ErrorPacket) and told.code == tftp.TFTPErrorCode.DISK_FULL
    assert len(sink.getvalue()) == 512  # the block that would pass the bound was not written


def test_max_size_per_call_overrides_the_clients_and_get_takes_it():
    with FakePeer(_data_for_ever) as peer:
        client = tftp.TFTPClient("127.0.0.1", peer.port, timeout=0.5, retries=1, blksize=None)
        with pytest.raises(tftp.TransferTooLargeError):
            client.get("f", max_size=1000)


def test_max_size_refuses_a_size_the_server_announces_before_any_data_moves():
    from tftp.packet import encode_oack

    answer = lambda data: [encode_oack({"tsize": "1000000000"})]
    with FakePeer(answer) as peer:
        client = tftp.TFTPClient("127.0.0.1", peer.port, timeout=0.5, retries=1, blksize=None, max_size=1000)
        with pytest.raises(tftp.TransferTooLargeError):
            client.get("f")
        assert wait_until(lambda: len(peer.seen) >= 2)
        told = tftp.decode(peer.seen[1][1])
    assert isinstance(told, tftp.ErrorPacket) and told.code == tftp.TFTPErrorCode.DISK_FULL
    assert len(peer.seen) == 2  # no ACK 0 went first


def test_a_server_sending_more_than_its_tsize_is_a_protocol_error():
    from tftp.packet import encode_data, encode_oack

    def script(data):
        packet = tftp.decode(data)
        if isinstance(packet, tftp.RequestPacket):
            return [encode_oack({"tsize": "10"})]
        if isinstance(packet, tftp.AckPacket) and packet.block == 0:
            return [encode_data(1, b"x" * 512)]
        return []

    with FakePeer(script) as peer:
        client = tftp.TFTPClient("127.0.0.1", peer.port, timeout=0.5, retries=1, blksize=None)
        with pytest.raises(tftp.TFTPProtocolError):
            client.get("f")


def test_a_listing_is_bounded_and_the_server_is_told(root, make_server):
    from test_listing import LISTING

    server = make_server(root, options=LISTING)
    client = client_for(server)
    with pytest.raises(tftp.TransferTooLargeError):
        client.listdir(max_size=10)
    assert wait_until(lambda: server.stats_snapshot()["failed"] >= 1)
    assert client.listdir()  # the default bound is 16 MiB


def test_max_size_is_validated_when_the_client_is_built():
    with pytest.raises(TypeError):
        tftp.TFTPClient("127.0.0.1", max_size="9")
    with pytest.raises(ValueError):
        tftp.TFTPClient("127.0.0.1", max_size=0)


def test_the_size_announced_for_an_upload_is_the_size_of_what_is_sent(spy_server, tmp_path):
    import gzip

    from tftp.client._core import _source_size

    payload = b"A" * 100_000
    plain = tmp_path / "plain.gz"
    with gzip.open(plain, "wb") as handle:
        handle.write(payload)
    with gzip.open(plain, "rb") as handle:
        assert _source_size(handle) == len(payload) != plain.stat().st_size
    spy, base = spy_server()
    port = int(base.rsplit(":", 1)[1].rstrip("/"))
    with gzip.open(plain, "rb") as handle:
        tftp.TFTPClient("127.0.0.1", port, timeout=0.5).upload("up.bin", handle)
    assert spy.requests[-1].options["tsize"] == str(len(payload))
    with open(plain, "rb") as handle:
        assert _source_size(handle) == plain.stat().st_size
    handle = io.BufferedReader(io.BytesIO(b"abc"))
    assert _source_size(handle) == 3

    class Unsized:
        def readinto(self, view):
            return 0

    assert _source_size(Unsized()) is None


# -- a transfer that is abandoned, and progress for the first packet ------------------------------


def test_a_download_abandoned_by_its_caller_tells_the_server():
    class Stop(BaseException):
        pass

    calls = []

    def progress(done, total):
        calls.append(done)
        if len(calls) == 2:
            raise Stop

    with FakePeer(_data_for_ever) as peer:
        client = tftp.TFTPClient("127.0.0.1", peer.port, timeout=5, retries=1, blksize=None)
        with pytest.raises(Stop):
            client.download("f", io.BytesIO(), progress=progress)
        assert wait_until(lambda: any(tftp.decode(d).__class__ is tftp.ErrorPacket for _, d in peer.seen))
        told = [p for p in (tftp.decode(d) for _, d in peer.seen) if isinstance(p, tftp.ErrorPacket)]
    assert told[0].code == tftp.TFTPErrorCode.NOT_DEFINED


def test_progress_is_reported_for_a_file_that_arrives_in_its_first_packet(root, make_server):
    server = make_server(root)
    calls = []
    plain = {"blksize": None, "tsize": False, "timeout_option": False}  # no OACK: the answer is DATA 1
    client_for(server, **plain).download("one.bin", io.BytesIO(), progress=lambda d, t: calls.append((d, t)))
    assert calls == [(1, None)]


def test_the_largest_block_size_arrives_in_full_between_this_library_s_own_peers(root, make_server):
    """Judged by what arrived: every octet, from full-size datagrams, with nothing resent."""
    payload = os.urandom(3 * 65464 + 17)
    (root / "largest.bin").write_bytes(payload)
    server = make_server(root, writable=True, overwrite=True, timeout=2)
    client = client_for(server, blksize=65464, timeout=2)
    sink = io.BytesIO()
    down = client.download("largest.bin", sink)
    assert down.negotiated.blksize == 65464 and sink.getvalue() == payload
    assert down.retransmits == 0
    up = client.upload("largest-up.bin", payload)
    assert up.negotiated.blksize == 65464 and up.retransmits == 0
    assert (root / "largest-up.bin").read_bytes() == payload


# -- a result as a dictionary -------------------------------------------------------------------


def test_a_result_is_a_dictionary_of_json_types(root, make_server):
    import json

    server = make_server(root, options=tftp.TFTPServerOptions(max_blksize=1000))
    result = client_for(server, blksize=4000, windowsize=4).download("big.bin", io.BytesIO())
    record = result.to_dict()
    assert json.loads(json.dumps(record)) == record
    assert list(record) == [
        "ok",
        "operation",
        "filename",
        "mode",
        "peer",
        "bytes",
        "blocks",
        "retransmits",
        "duration",
        "blksize",
        "windowsize",
        "tsize",
        "options",
        "error",
    ]
    assert record["ok"] is True and record["error"] is None
    assert (record["operation"], record["filename"], record["mode"]) == ("read", "big.bin", "octet")
    assert record["bytes"] == (root / "big.bin").stat().st_size  # the file's size, not the result's own
    assert record["peer"][0] == "127.0.0.1" and isinstance(record["peer"][1], int)
    assert (record["blksize"], record["windowsize"]) == (1000, 4)
    assert record["tsize"] == record["bytes"] and record["options"]["blksize"] == "1000"
    record["options"]["blksize"] = "changed"
    assert result.negotiated.options["blksize"] == "1000"


def test_a_failed_result_names_its_error():
    failure = tftp.RemoteError.from_code(1, "no such file")
    result = tftp.TransferResult(
        "f",
        "write",
        "netascii",
        ("10.0.0.2", 7000),
        ("10.0.0.1", 69),
        0,
        0,
        2,
        0.25,
        tftp.options.Negotiated(),
        failure,
    )
    record = result.to_dict()
    assert record["ok"] is False and record["error"] == str(failure) and "no such file" in record["error"]
    assert record["peer"] == ["10.0.0.2", 7000] and record["retransmits"] == 2 and record["duration"] == 0.25
