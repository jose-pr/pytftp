"""A terminating TFTP proxy: an upstream TFTP server as a backend.

Each side is an independent transfer. The client's session with this server
and this server's session with the upstream negotiate their own block size,
window and timeout, and are joined by a bounded byte pipe -- nothing maps
block numbers between them. A slow client therefore slows the upstream
download instead of the proxy buffering the file.

Use it when the two sides need different settings (an old boot ROM at 512
bytes in front of a fast upstream at 8 KiB windows), or to put TFTP in front
of another TFTP server with policy in between. To forward packets unchanged,
use :class:`tftp.relay.TFTPRelay` instead.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Mapping, Optional, Tuple, Union

from ..exceptions import RemoteError, TFTPError
from ..packet import TFTPErrorCode
from ._pipe import Pipe

__all__ = ["UpstreamBackend"]

Upstream = Union[str, Tuple[str, int]]


class _PipeSink:
    """Blocking writer for the upstream client's thread (backpressure)."""

    copies_writes = True

    def __init__(self, pipe: Pipe, timeout: float) -> None:
        self._pipe = pipe
        self._timeout = timeout

    def write(self, data) -> int:
        self._pipe.put(data, timeout=self._timeout)
        return len(data)


class _PipeSource:
    """Blocking reader for the upstream client's thread."""

    def __init__(self, pipe: Pipe, timeout: float) -> None:
        self._pipe = pipe
        self._timeout = timeout
        self.size = pipe.size

    def read(self, n: int) -> bytes:
        return self._pipe.get(n, timeout=self._timeout)


def _relayable(exc: BaseException) -> TFTPError:
    """What to tell the downstream client about an upstream failure."""
    if isinstance(exc, RemoteError):
        return TFTPError(exc.code, exc.message)  # same code, same text
    if isinstance(exc, TFTPError):
        return TFTPError(TFTPErrorCode.NOT_DEFINED, "upstream: %s" % exc.message)
    return TFTPError(TFTPErrorCode.NOT_DEFINED, "upstream unreachable")


class UpstreamBackend:
    """Serve every request from an upstream TFTP server.

    :param upstream: ``"host"``, ``"host:port"``, ``("host", port)``, or a
        callable ``upstream(context)`` returning one of those -- route by
        client address, filename, the interface the request arrived on.
    :param client_options: keyword arguments for the upstream
        :class:`tftp.TFTPClient` (``blksize``, ``windowsize``, ``timeout``...).
    :param buffer: bytes buffered between the two transfers.
    :param stall_timeout: seconds one side may wait on the other before the
        transfer is abandoned.
    :param writable: forward WRQ upstream (otherwise ERROR 2).

    The upstream request is made in a server worker thread. An RRQ is
    answered only once the upstream has answered: its errors (file not
    found...) reach the client with the same code and text, and its ``tsize``
    is passed through. A WRQ's final ACK is sent only once the upstream has
    acknowledged the whole file.
    """

    def __init__(
        self,
        upstream: Union[Upstream, Callable[[Any], Upstream]],
        *,
        client_options: Optional[Mapping[str, Any]] = None,
        buffer: int = 1 << 20,
        stall_timeout: float = 30.0,
        writable: bool = False,
    ) -> None:
        self.upstream = upstream
        self.client_options = dict(client_options or {})
        self.buffer = buffer
        self.stall_timeout = stall_timeout
        self.writable = writable

    def _client(self, context: Any, on_negotiated: Callable[..., Any]):
        from ..client import TFTPClient

        target = self.upstream(context) if callable(self.upstream) else self.upstream
        if isinstance(target, tuple):
            host, port = target
        else:
            host, port = target, 69
        options = dict(self.client_options)
        options["on_negotiated"] = on_negotiated
        return TFTPClient(host, port, **options)

    def _start(self, context: Any, work: Callable[[Any], None], pipe: Pipe) -> Pipe:
        """Run ``work(client)`` in a thread; return once the upstream has answered."""
        answered = threading.Event()
        failure: list = []

        def on_negotiated(negotiated, peer) -> None:
            if negotiated.tsize is not None and pipe.size is None:
                pipe.size = negotiated.tsize
            answered.set()

        def run() -> None:
            try:
                work(self._client(context, on_negotiated))
            except BaseException as exc:
                failure.append(exc)
                if pipe._upload:
                    pipe.set_result(_relayable(exc))
                else:
                    pipe.finish(_relayable(exc))
            else:
                if pipe._upload:
                    pipe.set_result(None)
                else:
                    pipe.finish()
            finally:
                answered.set()

        threading.Thread(target=run, name="tftp-upstream", daemon=True).start()
        if not answered.wait(self.stall_timeout):
            pipe.abort()
            raise TFTPError(TFTPErrorCode.NOT_DEFINED, "upstream did not answer")
        if failure and not pipe._upload and pipe.size is None and not pipe._buffer:
            raise _relayable(failure[0])  # refused before any data: same ERROR
        if failure and pipe._upload:
            raise _relayable(failure[0])
        return pipe

    def open_read(self, context: Any) -> Pipe:
        pipe = Pipe(self.buffer)
        sink = _PipeSink(pipe, self.stall_timeout)
        return self._start(
            context, lambda client: client.download(context.filename, sink, mode=context.mode), pipe
        )

    def open_write(self, context: Any, size: Optional[int]) -> Pipe:
        if not self.writable:
            raise TFTPError(TFTPErrorCode.ACCESS_VIOLATION, "server is read-only")
        pipe = Pipe(self.buffer, size).for_upload()
        source = _PipeSource(pipe, self.stall_timeout)
        return self._start(
            context, lambda client: client.upload(context.filename, source, mode=context.mode), pipe
        )
