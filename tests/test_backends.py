"""Memory, HTTP and upstream-TFTP backends, and the pipe between threads."""

from __future__ import annotations

import http.server
import io
import os
import threading

import pytest

import tftp
from conftest import client_for
from tftp.backends import HttpHandler, MemoryHandler, Pipe, UpstreamHandler
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
    handler = MemoryHandler({"/boot/a.bin": b"A" * 1000}, writable=True, overwrite=False)
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
        client_for(make_server(MemoryHandler())).put("x", b"y")


# -- HTTP ------------------------------------------------------------------------------


class _Http(http.server.BaseHTTPRequestHandler):
    store: dict = {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        body = self.store.get(self.path)
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self):
        if self.headers.get("Transfer-Encoding") == "chunked":
            data = bytearray()
            while True:
                size = int(self.rfile.readline().strip(), 16)
                if not size:
                    self.rfile.readline()
                    break
                data += self.rfile.read(size)
                self.rfile.readline()
        else:
            data = self.rfile.read(int(self.headers["Content-Length"]))
        self.store[self.path] = bytes(data)
        self.send_response(201)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture
def web():
    _Http.store = {"/images/kernel": os.urandom(200_000), "/images/a%20b": b"spaced"}
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Http)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:%d" % httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def test_http_gateway_download(web, make_server):
    server = make_server(HttpHandler(web + "/images", buffer=4096))
    client = client_for(server, windowsize=4)
    result_sink = io.BytesIO()
    result = client.download("kernel", result_sink)
    assert result_sink.getvalue() == _Http.store["/images/kernel"]
    assert result.negotiated.tsize == 200_000  # from Content-Length
    assert client.get("a b") == b"spaced"
    with pytest.raises(tftp.FileNotFound):
        client.get("missing")
    with pytest.raises(tftp.AccessViolation):
        client.get("../etc/passwd")


def test_http_gateway_upload(web, make_server):
    server = make_server(HttpHandler(web + "/up", writable=True))
    data = os.urandom(50_000)
    client_for(server).put("img.bin", data)
    assert _Http.store["/up/img.bin"] == data
    client_for(server, tsize=False).put("chunked.bin", b"x" * 3000)
    assert _Http.store["/up/chunked.bin"] == b"x" * 3000


def test_http_handler_arguments():
    with pytest.raises(ValueError):
        HttpHandler()
    with pytest.raises(ValueError):
        HttpHandler("http://x", url_for=lambda c: "http://y")


# -- upstream TFTP (terminating proxy) ---------------------------------------------


def test_proxy_bridges_different_block_and_window_sizes(root, make_server):
    upstream = make_server(root, options=tftp.ServerOptions(max_blksize=8192, max_windowsize=16))
    port = upstream.server_address[1]
    seen = []
    proxy = make_server(
        UpstreamHandler(
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
    proxy = make_server(UpstreamHandler("127.0.0.1:%d" % upstream.server_address[1]))
    result = client_for(proxy).download("513.bin", io.BytesIO())
    assert result.negotiated.tsize == 513
    with pytest.raises(tftp.FileNotFound):
        client_for(proxy).get("missing.bin")


def test_proxy_upload_and_netascii(root, make_server):
    upstream = make_server(root, writable=True)
    proxy = make_server(
        UpstreamHandler(lambda ctx: ("127.0.0.1", upstream.server_address[1]), writable=True, buffer=1024)
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
        handler = UpstreamHandler(
            ("127.0.0.1", silent.getsockname()[1]),
            client_options={"timeout": 0.1, "retries": 1},
            stall_timeout=2,
        )
        proxy = make_server(handler)
        with pytest.raises(tftp.RemoteError):
            client_for(proxy, timeout=1, retries=3).get("x")
