"""A TFTP-to-HTTP(S) gateway, on the standard library's ``urllib``.

Boot ROMs speak only TFTP; the images often live behind HTTP. Requests map to
``base_url + filename``; the body streams through a bounded :class:`Pipe`, so
a large image never sits in memory and a slow client slows the download.
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Iterator, Optional
from urllib.parse import quote

from ..exceptions import TFTPError
from ..packet import TFTPErrorCode
from .memory import normalize_name
from .pipe import Pipe

__all__ = ["HttpHandler"]

_STATUS_CODES = {
    404: TFTPErrorCode.FILE_NOT_FOUND,
    410: TFTPErrorCode.FILE_NOT_FOUND,
    401: TFTPErrorCode.ACCESS_VIOLATION,
}
_STATUS_CODES[403] = TFTPErrorCode.ACCESS_VIOLATION
_STATUS_CODES[409] = TFTPErrorCode.FILE_EXISTS
_STATUS_CODES[413] = TFTPErrorCode.DISK_FULL
_STATUS_CODES[507] = TFTPErrorCode.DISK_FULL


def _tftp_error(exc: BaseException) -> TFTPError:
    if isinstance(exc, TFTPError):
        return exc
    if isinstance(exc, urllib.error.HTTPError):
        code = _STATUS_CODES.get(exc.code, TFTPErrorCode.NOT_DEFINED)
        return TFTPError(code, "upstream HTTP %d" % exc.code)
    return TFTPError(TFTPErrorCode.NOT_DEFINED, "upstream unreachable")


class HttpHandler:
    """Serve (and optionally accept, as ``PUT``) files from an HTTP server.

    :param base_url: prefix; the normalised filename is appended,
        percent-encoded (``http://images/pxe/`` + ``boot/x``).
    :param url_for: alternative to ``base_url``: ``url_for(context) -> str``,
        for routing by client, filename or anything else.
    :param writable: map WRQ to an HTTP ``PUT`` of the uploaded body.
    :param headers: sent with every request (``Authorization``...).
    :param timeout: seconds for connecting and for each read.
    :param buffer: bytes buffered between HTTP and TFTP per transfer.
    :param opener: a ``urllib.request.OpenerDirector`` (proxies, TLS context).

    HTTP 404/410 become ERROR 1, 401/403 ERROR 2, 409 ERROR 6, 413/507
    ERROR 3, anything else ERROR 0; a ``Content-Length`` answers ``tsize``.
    The request is made in a server worker thread, never on the event loop.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        url_for: Optional[Callable[[Any], str]] = None,
        writable: bool = False,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 10.0,
        buffer: int = 1 << 20,
        opener: Optional[urllib.request.OpenerDirector] = None,
    ) -> None:
        if (base_url is None) == (url_for is None):
            raise ValueError("give exactly one of base_url and url_for")
        self.base_url = base_url
        self.url_for = url_for
        self.writable = writable
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.buffer = buffer
        self._open = (opener or urllib.request.build_opener()).open

    def url(self, context: Any) -> str:
        if self.url_for is not None:
            return self.url_for(context)
        name = normalize_name(context.filename)
        if not name or any(part == ".." for part in name.split("/")):
            raise TFTPError(TFTPErrorCode.ACCESS_VIOLATION)
        return self.base_url.rstrip("/") + "/" + quote(name)  # type: ignore[union-attr]

    def open_read(self, context: Any) -> Pipe:
        request = urllib.request.Request(self.url(context), headers=self.headers)
        try:
            response = self._open(request, timeout=self.timeout)
        except Exception as exc:
            raise _tftp_error(exc) from exc
        length = response.headers.get("Content-Length")
        encoded = response.headers.get("Content-Encoding", "identity") != "identity"
        size = int(length) if length and length.isdigit() and not encoded else None
        pipe = Pipe(self.buffer, size)

        def pump() -> None:
            try:
                with response:
                    while True:
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        pipe.put(chunk, timeout=self.timeout * 30)
                pipe.finish()
            except Exception as exc:
                pipe.finish(_tftp_error(exc))

        threading.Thread(target=pump, name="tftp-http-get", daemon=True).start()
        return pipe

    def open_write(self, context: Any, size: Optional[int]) -> Pipe:
        if not self.writable:
            raise TFTPError(TFTPErrorCode.ACCESS_VIOLATION, "server is read-only")
        url = self.url(context)
        pipe = Pipe(self.buffer, size).for_upload()
        headers = dict(self.headers)
        if size is not None:
            headers["Content-Length"] = str(size)

        def body() -> Iterator[bytes]:
            while True:
                chunk = pipe.get(65536, timeout=self.timeout * 30)
                if not chunk:
                    return
                yield chunk

        def push() -> None:
            try:
                request = urllib.request.Request(url, data=body(), headers=headers, method="PUT")
                with self._open(request, timeout=self.timeout):
                    pass
                pipe.set_result(None)
            except Exception as exc:
                pipe.set_result(_tftp_error(exc))

        threading.Thread(target=push, name="tftp-http-put", daemon=True).start()
        return pipe
