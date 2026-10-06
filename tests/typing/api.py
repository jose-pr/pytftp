"""Static-typing conformance for the public API. Never executed.

This file is the documented API (``src/tftp/AGENTS.md`` and the README) used as a consumer
uses it, with ``assert_type`` on the results that matter, so a checker proves the promises the
shipped annotations make. A line that must be an error carries ``# type: ignore[code]``: the
checker's ``warn_unused_ignores`` turns a call that stops being refused into an error here.

Running it, with the consumer's configuration and no cache::

    python -m mypy --no-incremental --config-file tests/typing/consumer.ini tests/typing/api.py

``--no-incremental`` is not optional: two invocations on one configuration share
``.mypy_cache``, and a package read back from it can differ from one checked afresh.

``assert_type`` comes from ``typing_extensions`` because ``typing.assert_type`` is 3.11+ and 3.9
is the floor. Nothing has to install it: mypy resolves the name from its bundled typeshed, and
this file never runs.
"""

from __future__ import annotations

import io
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

from typing_extensions import assert_type

import tftp
from tftp import (
    AsyncTFTPClient,
    AsyncTFTPServer,
    FileNotFound,
    RemoteError,
    TFTPClient,
    TFTPError,
    TFTPErrorCode,
    TFTPServer,
    TFTPServerLimits,
    TFTPServerOptions,
    TFTPURL,
    TransferResult,
    download,
    download_url,
    upload,
)
from tftp.backends import CaseInsensitive, FilesystemBackend, PerClient, Remap
from pktcap import DissectorRegistry, Dissected, PcapWriter, PcapngWriter
from tftp.capture import (
    PacketEvent,
    TFTPLayer,
    combine_hooks,
    dissect_tftp,
    register_tftp_dissector,
    trace_to,
)
from tftp.client import ProgressFunction, RemoteStat, SinkLike, SourceLike
from tftp.listing import ListEntry
from tftp.relay import TFTPRelay, Upstream, UpstreamLike, by_prefix, by_subnet
from tftp.server import (
    AsyncTFTPHandler,
    AsyncTFTPReader,
    AsyncTFTPWriter,
    PortRange,
    TFTPHandler,
    TFTPReader,
    TFTPRequestContext,
    TFTPWriter,
    ThreadedHandler,
)

# -- the client ---------------------------------------------------------------------------------


def client_results() -> None:
    client = TFTPClient("192.0.2.1", blksize=1428, windowsize=16)
    assert_type(client.download("pxelinux.0", "pxelinux.0"), TransferResult)
    assert_type(client.download("pxelinux.0", io.BytesIO()), TransferResult)
    assert_type(client.upload("logs/boot.txt", b"ok\n"), TransferResult)
    assert_type(client.upload("logs/boot.txt", io.BytesIO(b"ok\n")), TransferResult)
    assert_type(client.get("pxelinux.cfg/default"), bytes)
    assert_type(client.put("logs/boot.txt", b"ok\n"), TransferResult)
    assert_type(client.size("pxelinux.0"), Optional[int])
    assert_type(client.stat("pxelinux.0"), RemoteStat)
    assert_type(client.listdir(""), List[ListEntry])

    result = client.download("x", "x")
    assert_type(result.filename, str)
    assert_type(result.duration, float)

    # Anything else is refused where it is written, not when it runs.
    client.download("x", 3)  # type: ignore[arg-type]
    client.upload("x", object())  # type: ignore[arg-type]
    TFTPClient("192.0.2.1", 69, 2.0)  # type: ignore  # options are keyword-only


def client_conveniences() -> None:
    def progress(done: int, total: Optional[int]) -> None: ...

    callback: ProgressFunction = progress
    sink: SinkLike = io.BytesIO()
    source: SourceLike = b"data"
    assert_type(download("192.0.2.1", "pxelinux.0", sink, progress=callback), TransferResult)
    assert_type(upload("192.0.2.1", "logs/boot.txt", source), TransferResult)
    assert_type(download_url("tftp://192.0.2.1/pxelinux.0", "pxelinux.0"), TransferResult)


def failures() -> None:
    try:
        TFTPClient("192.0.2.1").get("missing")
    except FileNotFound as exc:
        assert_type(exc.code, TFTPErrorCode)
        assert_type(exc.message, str)
    except RemoteError as exc:
        assert_type(exc.code, TFTPErrorCode)
    except TFTPError as exc:
        assert_type(exc.message, str)


# -- URLs ---------------------------------------------------------------------------------------


def urls() -> None:
    url = TFTPURL.parse("tftp://192.0.2.1/images/vmlinuz?blksize=1428;windowsize=16")
    assert_type(url, TFTPURL)
    assert_type(url.host, str)
    assert_type(url.port, int)
    assert_type(url.filename, str)
    assert_type(url.mode, str)
    assert_type(str(url), str)
    assert_type(TFTPURL.try_parse("not a url"), Optional[TFTPURL])
    TFTPURL.parse(3)  # type: ignore[arg-type]


# -- a server, a handler ------------------------------------------------------------------------


class Menu:
    """A handler is two methods; the contract is a ``Protocol``, so no base class is needed."""

    def open_read(self, context: TFTPRequestContext) -> TFTPReader:
        assert_type(context.filename, str)
        return io.BytesIO(b"menu")

    def open_write(self, context: TFTPRequestContext, size: Optional[int]) -> TFTPWriter:
        return io.BytesIO()


class Blocking:
    """Without the hooks of the contract, it is refused."""

    def open_read(self, name: str) -> TFTPReader:
        return io.BytesIO(b"")


def servers() -> None:
    handler: TFTPHandler = Menu()
    assert_type(TFTPServer("/srv/tftp"), TFTPServer)
    TFTPServer(Menu(), host="127.0.0.1", port=0)
    TFTPServer(
        handler,
        writable=True,
        options=TFTPServerOptions(max_blksize=8192, max_windowsize=32),
        limits=TFTPServerLimits(max_idle=30.0),
        port_range="40000:40100",
        on_complete=lambda r: print(r.operation, r.filename),
    )
    TFTPServer("/srv/tftp", port_range=PortRange(40000, 40100))
    TFTPServer(Blocking())  # type: ignore[arg-type]
    TFTPServer("/srv/tftp", host=2130706433)  # type: ignore[arg-type]
    TFTPServer("/srv/tftp", ".", 69)  # type: ignore  # options are keyword-only

    with TFTPServer("/srv/tftp", port=6969) as server:
        assert_type(server.server_address, Optional[Tuple[Any, ...]])
        server.serve_forever()


# -- asyncio: the twins are visible to a checker through the lazy binding -----------------------


class AsyncMenu:
    async def open_read(self, context: TFTPRequestContext) -> AsyncTFTPReader:
        raise NotImplementedError

    async def open_write(self, context: TFTPRequestContext, size: Optional[int]) -> AsyncTFTPWriter:
        raise NotImplementedError


class AsyncSource:
    async def read(self, size: int) -> bytes:
        return b""


class AsyncSink:
    async def write(self, data: bytes) -> None: ...


async def asyncio_twins() -> None:
    client = AsyncTFTPClient("192.0.2.1")
    assert_type(await client.download("pxelinux.0", "pxelinux.0"), TransferResult)
    assert_type(await client.download("pxelinux.0", AsyncSink()), TransferResult)
    assert_type(await client.upload("logs/boot.txt", AsyncSource()), TransferResult)
    assert_type(await client.upload("logs/boot.txt", b"ok\n"), TransferResult)
    assert_type(await client.get("pxelinux.cfg/default"), bytes)
    assert_type(await client.size("pxelinux.0"), Optional[int])
    assert_type(await client.stat("pxelinux.0"), RemoteStat)
    assert_type(await client.listdir(""), List[ListEntry])
    pending = assert_type(client.get("x"), Coroutine[Any, Any, bytes])  # awaited, it is the bytes
    await pending

    async_handler: AsyncTFTPHandler = AsyncMenu()
    async with AsyncTFTPServer(async_handler, port=0) as server:
        await server.serve_forever()
    AsyncTFTPServer(ThreadedHandler(Menu()), host="::", port=0, timeout=2.0)
    AsyncTFTPServer("/srv/tftp", port_range=(40000, 40100))
    AsyncTFTPServer(Menu())  # type: ignore[arg-type]  # a bare synchronous handler needs ThreadedHandler
    AsyncTFTPServer("/srv/tftp", nonsense=1)  # type: ignore[call-arg]
    tftp.AsyncTFTPClient("192.0.2.1")


# -- the relay ----------------------------------------------------------------------------------


def relay() -> None:
    target: UpstreamLike = "10.0.0.5:70"
    assert_type(Upstream.parse(target), Upstream)
    TFTPRelay(by_subnet({"10.1.0.0/16": "10.1.0.5"}), host="::", port=69)
    TFTPRelay(by_prefix({"windows/": "wds.lan", "": ("default.lan", 69)}))
    TFTPRelay(by_prefix(["windows/=wds.lan", ("", "default.lan")]))
    TFTPRelay("upstream.lan")
    TFTPRelay(3)  # type: ignore[arg-type]


# -- handlers that compose, a policy copied, a result as a dictionary ---------------------------


def composed_handlers(directory: str, result: TransferResult, options: TFTPServerOptions) -> None:
    def make(path: str) -> FilesystemBackend:
        return FilesystemBackend(path, writable=True)

    handler = Remap(PerClient(directory, make, fallback=False), ["^/?pxelinux/=boot/", (r"[.]BIN$", ".bin")])
    TFTPServer(handler)
    TFTPServer(CaseInsensitive(directory, writable=True))
    assert_type(handler.rewrite("x"), str)
    assert_type(PerClient.directory(("10.0.0.1", 69)), str)
    PerClient(directory, make, False)  # type: ignore[call-arg]
    assert_type(result.to_dict(), Dict[str, Any])
    assert_type(options.replace(fit_mtu=True), TFTPServerOptions)
    hook = combine_hooks(print, None)
    assert_type(hook, Optional[Callable[[Any], None]])


def a_combined_hook_takes_an_event(event: PacketEvent) -> None:
    hook = combine_hooks(print)
    if hook is not None:
        hook(event)


def a_pktcap_writer_is_a_trace_hook(path: str) -> None:
    assert_type(trace_to(PcapWriter(path)), Callable[[PacketEvent], None])
    TFTPServer(".", trace=trace_to(PcapngWriter(path)))


def the_dissector_is_pktcaps_and_its_layer_a_value(data: bytes, registry: DissectorRegistry) -> None:
    assert_type(dissect_tftp(data), Dissected)
    register_tftp_dissector()
    register_tftp_dissector(registry, ports=(69, 6969))
    layer = TFTPLayer("RRQ", filename="f", mode="octet", options=(("blksize", "512"),))
    assert_type(layer.block, Optional[int])
    assert_type(layer.options, Optional[Tuple[Tuple[str, str], ...]])


# -- where a name lives -------------------------------------------------------------------------


def the_root_exports_the_common_task() -> None:
    assert_type(tftp.TFTPURL.parse("tftp://h/x"), TFTPURL)
    assert_type(tftp.TransferResult, type[TransferResult])
    assert_type(tftp.TFTPClient("h"), TFTPClient)
