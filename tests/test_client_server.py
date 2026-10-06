"""The client against the server over real loopback sockets."""

from __future__ import annotations

import io
import os
import time

import pytest

import tftp
from conftest import client_for, needs_ipv6
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
    assert result.negotiated.tsize == len(encode(text))
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

    with pytest.raises(tftp.TFTPError):
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
