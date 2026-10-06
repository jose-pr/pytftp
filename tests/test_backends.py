"""Memory, HTTP and upstream-TFTP backends, and the pipe between threads."""

from __future__ import annotations

import http.server
import io
import os
import threading

import pytest

import tftp
from conftest import HttpStore, client_for
from tftp.backends import HTTPBackend, MemoryBackend, Pipe, UpstreamBackend
from tftp.exceptions import WouldBlock

# -- pipe ----------------------------------------------------------------------


def test_pipe_thread_to_transfer():
    pipe = Pipe(capacity=4)
    woken = []
    pipe.set_wakeup(lambda: woken.append(1))
    view = bytearray(10)
    with pytest.raises(WouldBlock):
        pipe.readinto(view)
    pipe.put(b"abc")
    assert woken and pipe.readinto(view) == 3 and bytes(view[:3]) == b"abc"
    pipe.finish()
    assert pipe.readinto(view) == 0


def test_pipe_backpressure_and_errors():
    pipe = Pipe(capacity=4)
    with pytest.raises(TimeoutError):
        pipe.put(b"123456789", timeout=0.05)  # consumer never reads
    pipe.finish(tftp.TFTPError(1, "gone"))
    with pytest.raises(tftp.TFTPError):
        while True:
            pipe.readinto(bytearray(10))
    closed = Pipe()
    closed.close()
    with pytest.raises(BrokenPipeError):
        closed.put(b"x")


def test_pipe_upload_close_waits_for_result():
    pipe = Pipe(capacity=8).for_upload()
    pipe.write(b"data")
    with pytest.raises(WouldBlock):
        pipe.write(b"more data!")  # full
    assert pipe.get(100) == b"data"
    with pytest.raises(WouldBlock):
        pipe.close()  # the worker has not reported yet
    assert pipe.get(100) == b""  # end of data reached the worker
    pipe.set_result(None)
    pipe.close()
    failed = Pipe().for_upload()
    failed.set_result(tftp.TFTPError(3, "full"))
    with pytest.raises(tftp.TFTPError):
        failed.close()


# -- memory ----------------------------------------------------------------------


def test_memory_handler(make_server):
    handler = MemoryBackend({"/boot/a.bin": b"A" * 1000}, writable=True, overwrite=False)
    server = make_server(handler)
    client = client_for(server)
    assert client.get("boot\\a.bin") == b"A" * 1000
    client.put("new/b.bin", b"B")
    assert handler.files["new/b.bin"] == b"B"
    with pytest.raises(tftp.FileAlreadyExists):
        client.put("new/b.bin", b"C")
    with pytest.raises(tftp.FileNotFound):
        client.get("nope")
    with pytest.raises(tftp.AccessViolation):
        client_for(make_server(MemoryBackend())).put("x", b"y")


def test_a_writable_memory_backend_is_bounded_by_default():
    handler = MemoryBackend(writable=True)
    assert (handler.max_upload, handler.max_entries) == (16 << 20, 1024)
    unbounded = MemoryBackend(writable=True, max_upload=None, max_entries=None)
    assert (unbounded.max_upload, unbounded.max_entries) == (None, None)
    for bad in (-1, "1", 1.5, True):
        with pytest.raises((TypeError, ValueError)):
            MemoryBackend(max_upload=bad)
        with pytest.raises((TypeError, ValueError)):
            MemoryBackend(max_entries=bad)


class _Announcing:
    """An upload source that announces ``size`` octets whatever it holds."""

    def __init__(self, size, data=b""):
        self.size, self.data = size, data

    def read(self, n):
        chunk, self.data = self.data[:n], self.data[n:]
        return chunk


def test_an_upload_announced_above_the_default_bound_is_refused_with_error_3(make_server):
    handler = MemoryBackend(writable=True)
    client = client_for(make_server(handler))
    with pytest.raises(tftp.DiskFull):  # the request is refused: no data is needed to cross the bound
        client.upload("big", _Announcing((16 << 20) + 1))
    assert handler.files == {}


@pytest.mark.parametrize("announce", [True, False])
def test_an_upload_past_max_upload_is_refused_with_error_3(make_server, announce):
    handler = MemoryBackend(writable=True, max_upload=1000)
    client = client_for(make_server(handler), tsize=announce, blksize=512)
    assert client.put("fits", b"x" * 1000).bytes == 1000
    with pytest.raises(tftp.DiskFull):
        client.put("over", b"x" * 1001)
    assert handler.files == {"fits": b"x" * 1000}


def test_the_1025th_name_is_refused_with_error_3(make_server):
    handler = MemoryBackend(writable=True)
    client = client_for(make_server(handler, dally=False, max_sessions=None))
    for number in range(1024):
        client.put("n%d" % number, b"x")
    assert len(handler.files) == 1024
    with pytest.raises(tftp.DiskFull):
        client.put("one-too-many", b"x")
    assert "one-too-many" not in handler.files and len(handler.files) == 1024
    client.put("n0", b"replaced")  # a name that exists is still replaced
    assert handler.files["n0"] == b"replaced"


def test_max_entries_counts_the_names_it_was_given_and_leaves_replacing_alone(make_server):
    handler = MemoryBackend({"a": b"1", "b": b"2"}, writable=True, max_entries=2)
    client = client_for(make_server(handler))
    with pytest.raises(tftp.DiskFull):
        client.put("c", b"3")
    client.put("a", b"replaced")
    assert handler.files == {"a": b"replaced", "b": b"2"}
    no_replace = MemoryBackend({"a": b"1"}, writable=True, overwrite=False, max_entries=1)
    with pytest.raises(tftp.FileAlreadyExists):
        client_for(make_server(no_replace)).put("a", b"x")


# -- HTTP ------------------------------------------------------------------------------


def test_http_gateway_download(web, make_server):
    server = make_server(HTTPBackend(web + "/images", buffer=4096))
    client = client_for(server, windowsize=4)
    result_sink = io.BytesIO()
    result = client.download("kernel", result_sink)
    assert result_sink.getvalue() == HttpStore.store["/images/kernel"]
    assert result.negotiated.tsize == 200_000  # from Content-Length
    assert client.get("a b") == b"spaced"
    with pytest.raises(tftp.FileNotFound):
        client.get("missing")
    with pytest.raises(tftp.AccessViolation):
        client.get("../etc/passwd")


def test_http_gateway_upload(web, make_server):
    server = make_server(HTTPBackend(web + "/up", writable=True))
    data = os.urandom(50_000)
    client_for(server).put("img.bin", data)
    assert HttpStore.store["/up/img.bin"] == data
    client_for(server, tsize=False).put("chunked.bin", b"x" * 3000)
    assert HttpStore.store["/up/chunked.bin"] == b"x" * 3000


def test_http_handler_arguments():
    with pytest.raises(ValueError):
        HTTPBackend()
    with pytest.raises(ValueError):
        HTTPBackend("http://x", url_for=lambda c: "http://y")


# -- HTTP gateway against a real HTTP/1.1 origin -------------------------------------------


class _Origin:
    """An HTTP/1.1 origin on loopback that records every request exactly as received.

    ``lax`` keeps reading requests from a connection whatever ``Connection: close``
    said, as a front end that pipelines would, so a request smuggled after a
    body shows up in ``requests``. Each record is ``{"line", "headers",
    "body", "complete"}``; ``stored`` holds the body of every complete PUT.
    """

    def __init__(self, lax=True, files=None, redirect=None):
        origin = self
        self.requests = []
        self.stored = {}
        self.files = files or {}
        self.redirect = redirect
        self.connections = 0

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def setup(self):
                super().setup()
                origin.connections += 1

            def _body(self):
                self.connection.settimeout(3.0)
                try:
                    if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                        body = bytearray()
                        while True:
                            size = int(self.rfile.readline().strip() or b"0", 16)
                            if size == 0:
                                self.rfile.readline()
                                return bytes(body), True
                            body += self.rfile.read(size)
                            self.rfile.readline()
                    length = int(self.headers.get("Content-Length", "0"))
                    body = self.rfile.read(length)
                    return body, len(body) == length
                except (OSError, ValueError):
                    return b"", False

            def _reply(self, status, body=b"", headers=()):
                try:
                    self.send_response(status)
                    for name, value in headers:
                        self.send_header(name, value)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    self.close_connection = True

            def do_PUT(self):
                body, complete = self._body()
                origin.requests.append(
                    {
                        "line": self.requestline,
                        "headers": list(self.headers.items()),
                        "body": body,
                        "complete": complete,
                    }
                )
                if complete:
                    origin.stored[self.path] = body
                self._reply(201 if complete else 400)
                self.close_connection = not (lax and complete)

            def do_GET(self):
                origin.requests.append(
                    {
                        "line": self.requestline,
                        "headers": list(self.headers.items()),
                        "body": b"",
                        "complete": True,
                    }
                )
                if origin.redirect is not None:
                    self._reply(302, headers=[("Location", origin.redirect(self.path))])
                    return
                body = origin.files.get(self.path)
                if body is None:
                    self._reply(404)
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    try:
                        for start in range(0, len(body), 8192):
                            self.wfile.write(body[start : start + 8192])
                    except OSError:
                        self.close_connection = True

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def wait_for(self, count, seconds=5.0):
        import time

        deadline = time.monotonic() + seconds
        while len(self.requests) < count and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.3)  # long enough for a request smuggled after the body to arrive too
        return self.requests

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def origin():
    made = []

    def make(**kwargs):
        made.append(_Origin(**kwargs))
        return made[-1]

    yield make
    for each in made:
        each.close()


def _headers(record):
    names = [name.lower() for name, _ in record["headers"]]
    assert len(names) == len(set(names)), "a header was sent twice: %r" % record["headers"]
    return {name.lower(): value for name, value in record["headers"]}


class _Source:
    """An upload source that announces ``announced`` octets (or none) and supplies ``data``."""

    def __init__(self, announced, data):
        if announced is not None:
            self.size = announced
        self.data = data

    def read(self, n):
        chunk, self.data = self.data[:n], self.data[n:]
        return chunk


def _put(make_server, backend, announced, data):
    client = client_for(make_server(backend, timeout=1.0), tsize=announced is not None)
    return client.upload("f.bin", _Source(announced, data))


def test_a_gateway_upload_is_the_request_the_origin_recorded(origin, make_server):
    web = origin()
    result = _put(make_server, HTTPBackend(web.base + "/up", writable=True, timeout=3), 50, b"h" * 50)
    assert result.bytes == 50
    (record,) = web.wait_for(1)
    assert record["line"] == "PUT /up/f.bin HTTP/1.1"
    headers = _headers(record)
    assert headers["content-length"] == "50" and "transfer-encoding" not in headers
    assert headers["content-type"] == "application/octet-stream"
    assert headers["user-agent"] == "tftp/%s" % tftp.__version__
    assert headers["connection"] == "close"
    assert record["body"] == b"h" * 50 and web.stored == {"/up/f.bin": b"h" * 50}


def test_an_upload_with_more_octets_than_announced_fails_and_the_origin_keeps_nothing(origin, make_server):
    web = origin(lax=True)
    smuggled = b"DELETE /everything HTTP/1.1\r\nHost: origin\r\nContent-Length: 0\r\n\r\n"
    with pytest.raises(tftp.DiskFull):
        _put(make_server, HTTPBackend(web.base + "/up", writable=True, timeout=3), 5, b"hello" + smuggled)
    records = web.wait_for(1)
    assert [r["line"] for r in records] == ["PUT /up/f.bin HTTP/1.1"]  # no second request, kept alive or not
    assert not records[0]["complete"] and web.stored == {}


def test_an_upload_with_fewer_octets_than_announced_fails_and_the_origin_keeps_nothing(origin, make_server):
    web = origin(lax=True)
    with pytest.raises(tftp.TFTPError):
        _put(make_server, HTTPBackend(web.base + "/up", writable=True, timeout=3), 50, b"hello")
    records = web.wait_for(1)
    assert len(records) == 1 and not records[0]["complete"] and web.stored == {}


def test_an_upload_that_announced_nothing_is_chunked(origin, make_server):
    web = origin()
    assert (
        _put(make_server, HTTPBackend(web.base + "/up", writable=True, timeout=3), None, b"hello").bytes == 5
    )
    (record,) = web.wait_for(1)
    headers = _headers(record)
    assert headers["transfer-encoding"] == "chunked" and "content-length" not in headers
    assert record["body"] == b"hello" and web.stored == {"/up/f.bin": b"hello"}


def test_a_netascii_upload_is_chunked_whatever_it_announced(origin, make_server):
    web = origin()
    server = make_server(HTTPBackend(web.base + "/up", writable=True, timeout=3), timeout=1.0)
    client_for(server).upload("f.bin", io.BytesIO(b"one\ntwo\n"), mode="netascii")
    (record,) = web.wait_for(1)
    headers = _headers(record)
    assert headers["transfer-encoding"] == "chunked" and "content-length" not in headers
    assert len(web.stored) == 1


def test_a_header_the_caller_gives_wins_over_the_default_user_agent(origin, make_server):
    web = origin(files={"/images/a": b"abc"})
    server = make_server(HTTPBackend(web.base + "/images", headers={"user-agent": "boot-farm/1"}))
    assert client_for(server).get("a") == b"abc"
    assert _headers(web.requests[0])["user-agent"] == "boot-farm/1"
    default = make_server(HTTPBackend(web.base + "/images"))
    assert client_for(default).get("a") == b"abc"
    assert _headers(web.requests[1])["user-agent"] == "tftp/%s" % tftp.__version__


def test_a_name_that_is_not_utf8_is_sent_as_the_octets_it_was(origin, make_server, caplog):
    import logging

    web = origin()
    server = make_server(HTTPBackend(web.base + "/images"))
    with caplog.at_level(logging.INFO):
        with pytest.raises(tftp.FileNotFound):  # the origin answers 404
            client_for(server).get("caf\udce9.bin")
    assert web.requests[0]["line"] == "GET /images/caf%E9.bin HTTP/1.1"
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


def test_a_failed_request_closes_the_response(origin):
    import gc
    import warnings

    web = origin()
    backend = HTTPBackend(web.base)
    context = type("Context", (), {"filename": "missing.bin", "mode": "octet", "options": {}})()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(tftp.TFTPError) as raised:
            backend.open_read(context)
        assert raised.value.code == tftp.TFTPErrorCode.FILE_NOT_FOUND
        raised = None
        gc.collect()
        gc.collect()
    assert [str(w.message) for w in caught if issubclass(w.category, ResourceWarning)] == []


def test_content_length_is_read_as_ascii_digits():
    class Response:
        def __init__(self, length):
            self.headers = {"Content-Length": length}

        def read(self, n=-1):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

    class Opener:
        def __init__(self, length):
            self.length = length

        def open(self, request, timeout=None):
            return Response(self.length)

    context = type("Context", (), {"filename": "x", "mode": "octet", "options": {}})()
    assert HTTPBackend("http://origin/", opener=Opener("512")).open_read(context).size == 512
    assert HTTPBackend("http://origin/", opener=Opener("²")).open_read(context).size is None
    assert HTTPBackend("http://origin/", opener=Opener("-1")).open_read(context).size is None


# -- what the gateway may fetch ----------------------------------------------------------------


@pytest.mark.parametrize(
    "base", ["file:///srv/images", "ftp://host/images", "data:text/plain,abc", "//host/images", "images", ""]
)
def test_a_gateway_refuses_a_base_url_that_is_not_http_or_https(base):
    with pytest.raises(ValueError):
        HTTPBackend(base)


def test_a_gateway_accepts_http_and_https_in_any_case():
    HTTPBackend("http://origin/x")
    HTTPBackend("HTTPS://origin/x")


def test_a_mapped_url_that_is_not_http_is_refused(make_server, tmp_path):
    (tmp_path / "secret.txt").write_bytes(b"read through file:")
    mapped = HTTPBackend(url_for=lambda context: (tmp_path / "secret.txt").as_uri())
    with pytest.raises(tftp.AccessViolation):
        client_for(make_server(mapped)).get("secret.txt")


class _FTPListener:
    """Counts the connections made to it: the gateway must not make one."""

    def __init__(self):
        import socket

        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.sock.settimeout(0.1)
        self.port = self.sock.getsockname()[1]
        self.accepted = 0
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        import socket

        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except (socket.timeout, OSError):
                continue
            self.accepted += 1
            conn.close()

    def close(self):
        self._stop = True
        self._thread.join(5)
        self.sock.close()


@pytest.mark.parametrize("scheme", ["ftp", "file", "data"])
def test_a_redirect_to_another_scheme_is_not_followed(origin, make_server, tmp_path, scheme):
    import time

    (tmp_path / "secret.txt").write_bytes(b"read through a redirect")
    listener = _FTPListener()
    try:
        target = {
            "ftp": "ftp://127.0.0.1:%d/secret.txt" % listener.port,
            "file": (tmp_path / "secret.txt").as_uri(),
            "data": "data:text/plain,read%20through%20a%20redirect",
        }[scheme]
        web = origin(redirect=lambda path: target)
        server = make_server(HTTPBackend(web.base + "/images"))
        with pytest.raises(tftp.TFTPError):
            client_for(server).get("secret.txt")
        time.sleep(0.3)
        assert listener.accepted == 0
    finally:
        listener.close()


def test_a_redirect_between_http_origins_is_still_followed(origin, make_server):
    second = origin(files={"/internal-only": b"served by the second origin"})
    front = origin(redirect=lambda path: second.base + "/internal-only")
    assert (
        client_for(make_server(HTTPBackend(front.base + "/images"))).get("a")
        == b"served by the second origin"
    )


def test_an_opener_of_the_callers_own_chooses_the_schemes(origin, make_server, tmp_path):
    import urllib.request

    (tmp_path / "secret.txt").write_bytes(b"read through file:")
    served = HTTPBackend(tmp_path.as_uri(), opener=urllib.request.build_opener())
    assert client_for(make_server(served)).get("secret.txt") == b"read through file:"
    listener = _FTPListener()
    try:
        web = origin(redirect=lambda path: "ftp://127.0.0.1:%d/x" % listener.port)
        backend = HTTPBackend(web.base + "/images", opener=urllib.request.build_opener())
        with pytest.raises(tftp.TFTPError):  # the listener is not an FTP server, but it was reached
            client_for(make_server(backend)).get("x")
        import time

        deadline = time.monotonic() + 5
        while not listener.accepted and time.monotonic() < deadline:
            time.sleep(0.02)
        assert listener.accepted >= 1
    finally:
        listener.close()


# -- an unacknowledged request costs the origin a window, not the buffer ----------------------


class _CountingResponse:
    def __init__(self, response, owner):
        self._response, self._owner = response, owner
        self.headers = response.headers

    def read1(self, n=-1):
        data = self._response.read1(n)
        self._owner.pulled += len(data)
        return data

    def read(self, n=-1):
        data = self._response.read(n)
        self._owner.pulled += len(data)
        return data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._response.close()


class _CountingOpener:
    """The standard opener, counting the octets the gateway has pulled from each response."""

    def __init__(self):
        import urllib.request

        self.pulled = 0
        self._inner = urllib.request.build_opener()

    def open(self, request, timeout=None):
        return _CountingResponse(self._inner.open(request, timeout=timeout), self)


def _gateway_threads():
    return [t for t in threading.enumerate() if t.name.startswith("tftp-http-get")]


def test_a_request_nobody_acknowledges_pulls_one_window_from_the_origin(origin, make_server):
    import socket
    import time

    body = os.urandom(4 << 20)
    web = origin(files={"/images/big": body})
    opener = _CountingOpener()
    server = make_server(HTTPBackend(web.base + "/images", opener=opener), timeout=1.0)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as raw:
        raw.bind(("127.0.0.1", 0))
        raw.settimeout(5)
        raw.sendto(tftp.packet.encode_request(tftp.TFTPOpcode.RRQ, "big"), server.server_address[:2])
        first, _ = raw.recvfrom(2048)  # DATA 1, never acknowledged
        assert tftp.decode(first).block == 1
        previous, stable = -1, 0
        deadline = time.monotonic() + 10
        while stable < 5 and time.monotonic() < deadline:  # the pump is blocked: the count stops moving
            time.sleep(0.1)
            stable = stable + 1 if opener.pulled == previous else 0
            previous = opener.pulled
        assert 0 < opener.pulled <= 64 * 1024
        assert len(web.requests) == 1
        assert _gateway_threads()
    server.close()  # the session ends, which ends the pump
    deadline = time.monotonic() + 5
    while _gateway_threads() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert _gateway_threads() == []


def test_the_read_ahead_grows_as_the_peer_takes_data(origin, make_server):
    body = os.urandom(3 << 20)
    web = origin(files={"/images/big": body})
    server = make_server(HTTPBackend(web.base + "/images"), timeout=1.0)
    assert client_for(server, windowsize=8, blksize=1428).get("big") == body


# -- upstream TFTP (terminating proxy) ---------------------------------------------


def test_proxy_bridges_different_block_and_window_sizes(root, make_server):
    upstream = make_server(root, options=tftp.TFTPServerOptions(max_blksize=8192, max_windowsize=16))
    port = upstream.server_address[1]
    seen = []
    proxy = make_server(
        UpstreamBackend(
            ("127.0.0.1", port),
            client_options={"blksize": 8192, "windowsize": 16, "timeout": 0.5},
            buffer=2048,  # far smaller than the file: forces backpressure
        ),
        on_complete=seen.append,
    )
    sink = io.BytesIO()
    result = client_for(proxy, blksize=None).download("big.bin", sink)  # lock-step 512
    assert sink.getvalue() == (root / "big.bin").read_bytes()
    assert result.negotiated.blksize == 512 and result.negotiated.windowsize == 1


def test_proxy_passes_tsize_and_errors(root, make_server):
    upstream = make_server(root)
    proxy = make_server(UpstreamBackend("127.0.0.1:%d" % upstream.server_address[1]))
    result = client_for(proxy).download("513.bin", io.BytesIO())
    assert result.negotiated.tsize == 513
    with pytest.raises(tftp.FileNotFound):
        client_for(proxy).get("missing.bin")


def test_proxy_upload_and_netascii(root, make_server):
    upstream = make_server(root, writable=True)
    proxy = make_server(
        UpstreamBackend(lambda ctx: ("127.0.0.1", upstream.server_address[1]), writable=True, buffer=1024)
    )
    data = os.urandom(40_000)
    client_for(proxy, windowsize=8).put("through.bin", data)
    assert (root / "through.bin").read_bytes() == data
    with pytest.raises(tftp.FileAlreadyExists):
        client_for(proxy).put("through.bin", b"again")  # upstream's refusal, relayed
    text = (root / "text.txt").read_bytes()
    assert client_for(proxy).get("text.txt", mode="netascii") == text


def test_proxy_upstream_down(make_server):
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as silent:
        silent.bind(("127.0.0.1", 0))
        handler = UpstreamBackend(
            ("127.0.0.1", silent.getsockname()[1]),
            client_options={"timeout": 0.1, "retries": 1},
            stall_timeout=2,
        )
        proxy = make_server(handler)
        with pytest.raises(tftp.RemoteError):
            client_for(proxy, timeout=1, retries=3).get("x")


# -- the names a Windows host refuses ---------------------------------------------------------

#: One file under several names, device names, and the names ``os.path.isreserved`` adds to the classic ones.
_WINDOWS_REFUSED = [
    "one.bin.",
    "one.bin ",
    "one.bin...  ",
    "sub./nested.bin",
    "sub /nested.bin",
    "NUL",
    "nul.txt",
    "NUL ",
    "NUL .txt",
    "PRN ",
    "AUX.",
    "sub/CON",
    "COM1",
    "LPT9.log",
    "COM¹",
    "LPT²",
    "CONIN$",
    "conout$",
    "one.bin:stream",
    "x:y",
    "C:",
    "c:/windows/system.ini",
    "a*b",
]


@pytest.fixture(params=["list", "platform"])
def windows_rules(request, monkeypatch):
    """The Windows branch of ``resolve`` on any host: with the explicit list, or with the platform's test."""
    from tftp.backends import _filesystem as filesystem  # the module that reads _WINDOWS

    monkeypatch.setattr(filesystem, "_WINDOWS", True)
    if request.param == "list":
        monkeypatch.delattr(os.path, "isreserved", raising=False)
    elif not hasattr(os.path, "isreserved"):
        pytest.skip("os.path.isreserved needs Python 3.13 on Windows")
    return request.param


@pytest.mark.parametrize("name", _WINDOWS_REFUSED)
def test_a_name_windows_would_alias_or_treat_as_a_device_is_refused(root, windows_rules, name):
    with pytest.raises(tftp.TFTPError) as info:
        tftp.FilesystemBackend(root).resolve(name)
    assert info.value.code == tftp.TFTPErrorCode.ACCESS_VIOLATION


@pytest.mark.parametrize(
    "name", ["one.bin", "sub/nested.bin", "a.b.c", "CLOCK$", "console.txt", "com10", ".hidden"]
)
def test_an_ordinary_name_is_still_served_under_the_windows_rules(root, windows_rules, name):
    (root / "sub" / "nested.bin").write_bytes(b"n")
    assert os.path.dirname(tftp.FilesystemBackend(root).resolve(name)).startswith(os.path.realpath(root))


@pytest.mark.skipif(os.name == "nt", reason="the file system drops the trailing dot")
def test_windows_rules_do_not_apply_elsewhere(root, monkeypatch):
    from tftp.backends import _filesystem as filesystem  # the module that reads _WINDOWS

    monkeypatch.setattr(filesystem, "_WINDOWS", False)
    assert tftp.FilesystemBackend(root).resolve("NUL.").endswith("NUL.")


@pytest.mark.skipif(os.name != "nt", reason="Windows file names")
def test_one_file_is_reachable_under_one_name_through_a_server(root, make_server):
    client = client_for(make_server(root, writable=True, overwrite=True))
    assert client.get("one.bin") == b"x"
    for name in ("one.bin.", "one.bin ", "CONOUT$", "NUL "):
        with pytest.raises(tftp.RemoteError) as info:
            client.get(name)
        assert info.value.code == tftp.TFTPErrorCode.ACCESS_VIOLATION, name
    with pytest.raises(tftp.RemoteError):
        client.put("CONOUT$", b"y")
    assert (root / "one.bin").read_bytes() == b"x"
    assert not (root / "CONOUT$").exists()


def test_a_name_that_is_not_utf8_reaches_the_backend_as_the_octets_the_client_sent():
    name = b"caf\xe9-\xff".decode("utf-8", "surrogateescape")
    server = tftp.TFTPServer(MemoryBackend({name: b"latin"}), host="127.0.0.1", port=0, timeout=0.5).start()
    try:
        assert client_for(server).get(name) == b"latin"
        with pytest.raises(tftp.FileNotFound):
            client_for(server).get("café-ÿ")
    finally:
        server.close()
