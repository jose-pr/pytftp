"""A TFTP-to-HTTP(S) gateway, on the standard library's ``urllib``.

Boot ROMs speak only TFTP; the images often live behind HTTP. Requests map to
``base_url + filename``; the body streams through a bounded :class:`Pipe`, so
a large image never sits in memory and a slow client slows the download.
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Iterator, Mapping, Optional
from urllib.parse import quote, urlsplit

from ..exceptions import TFTPError
from ..options._handler import read_decimal
from ..packet._enums import TFTPErrorCode
from .._loggers import BACKENDS as log
from ._memory import normalize_name
from ._pipe import Pipe

__all__ = ["HTTPBackend"]

#: Octets a download may be read ahead of what the transfer has taken, before it
#: has taken any: a request nobody has acknowledged costs the origin this and
#: the first window, not the whole buffer. The limit grows with what the
#: transfer takes, up to the backend's ``buffer``.
_FIRST_READ_AHEAD = 16 * 1024
#: Octets read from the origin at a time.
_CHUNK = 16 * 1024

_STATUS_CODES = {
    404: TFTPErrorCode.FILE_NOT_FOUND,
    410: TFTPErrorCode.FILE_NOT_FOUND,
    401: TFTPErrorCode.ACCESS_VIOLATION,
}
_STATUS_CODES[403] = TFTPErrorCode.ACCESS_VIOLATION
_STATUS_CODES[409] = TFTPErrorCode.FILE_EXISTS
_STATUS_CODES[413] = TFTPErrorCode.DISK_FULL
_STATUS_CODES[507] = TFTPErrorCode.DISK_FULL

_SCHEMES = ("http", "https")


def _tftp_error(exc: BaseException) -> TFTPError:
    """The ERROR a failure maps to; an ``HTTPError`` is an open response, closed here."""
    if isinstance(exc, TFTPError):
        return exc
    if isinstance(exc, urllib.error.HTTPError):
        exc.close()
        code = _STATUS_CODES.get(exc.code, TFTPErrorCode.NOT_DEFINED)
        return TFTPError(code, "upstream HTTP %d" % exc.code)
    return TFTPError(TFTPErrorCode.NOT_DEFINED, "upstream unreachable")


def _http_opener() -> urllib.request.OpenerDirector:
    """An opener that speaks ``http`` and ``https`` (proxies and redirects included) and nothing else.

    ``urllib.request.build_opener()`` also carries the ``file``, ``ftp`` and
    ``data`` handlers; a request for one of those schemes, or a redirect to
    one, fails here with ``URLError``.
    """
    opener = urllib.request.OpenerDirector()
    for name in (
        "ProxyHandler",
        "UnknownHandler",
        "HTTPHandler",
        "HTTPSHandler",  # absent when Python has no TLS
        "HTTPDefaultErrorHandler",
        "HTTPRedirectHandler",
        "HTTPErrorProcessor",
    ):
        handler = getattr(urllib.request, name, None)
        if handler is not None:
            opener.add_handler(handler())
    return opener


def _user_agent() -> str:
    from .. import __version__

    return "tftp/%s" % __version__


def _headers(defaults: Mapping[str, str], given: Mapping[str, str]) -> Dict[str, str]:
    """``defaults`` with ``given`` over them; header names are compared without case."""
    merged = {name.lower(): (name, value) for name, value in defaults.items()}
    merged.update((name.lower(), (name, value)) for name, value in given.items())
    return dict(merged.values())


class _ReadAhead(Pipe):
    """A download pipe whose capacity grows with what the transfer has taken.

    It starts at :data:`_FIRST_READ_AHEAD` and is ``first + taken`` after that,
    never above ``limit``: the transfer takes more only as the peer
    acknowledges, so the origin is read no faster than the peer proves it is
    listening.
    """

    def __init__(self, limit: int, size: Optional[int]) -> None:
        self._taken = 0
        super().__init__(limit, size)

    @property  # type: ignore[override]
    def capacity(self) -> int:
        return min(self._limit, _FIRST_READ_AHEAD + self._taken)

    @capacity.setter
    def capacity(self, value: int) -> None:
        self._limit = value

    def readinto(self, view) -> int:
        n = super().readinto(view)
        with self._lock:
            self._taken += n
            self._lock.notify_all()  # a producer waiting for room
        return n


class _AnnouncedUpload(Pipe):
    """An upload pipe that refuses more than the announced ``limit`` octets (ERROR 3).

    ``limit`` is ``None`` when no size was announced, or one that does not
    describe the octets received (netascii).
    """

    def __init__(self, capacity: int, size: Optional[int], limit: Optional[int]) -> None:
        super().__init__(capacity, size)
        self.limit = limit
        self._received = 0

    def write(self, data) -> int:
        limit = self.limit
        if limit is not None and self._received + len(data) > limit:
            raise TFTPError(TFTPErrorCode.DISK_FULL, "more octets than the announced size")
        n = super().write(data)
        self._received += n
        return n


class HTTPBackend:
    """Serve (and optionally accept, as ``PUT``) files from an HTTP server.

    :param base_url: prefix; the normalised filename is appended,
        percent-encoded (``http://images/pxe/`` + ``boot/x``). Its scheme must
        be ``http`` or ``https``.
    :param url_for: alternative to ``base_url``: ``url_for(context) -> str``,
        for routing by client, filename or anything else. A request whose URL
        is not ``http`` or ``https`` is refused with ERROR 2.
    :param writable: map WRQ to an HTTP ``PUT`` of the uploaded body.
    :param headers: sent with every request (``Authorization``...); they win
        over the default ``User-Agent``.
    :param timeout: seconds for connecting and for each read.
    :param buffer: bytes buffered between HTTP and TFTP per transfer.
    :param opener: a ``urllib.request.OpenerDirector`` (proxies, TLS context).
        The default opener speaks ``http`` and ``https`` only and follows
        redirects between them; with an opener of your own the scheme of
        ``base_url`` and of ``url_for``'s URLs is yours to choose.

    HTTP 404/410 become ERROR 1, 401/403 ERROR 2, 409 ERROR 6, 413/507
    ERROR 3, anything else ERROR 0; a ``Content-Length`` answers ``tsize``.
    The request is made in a server worker thread, never on the event loop.

    A download is read ahead of the transfer by a few KiB until the peer has
    acknowledged data, and by up to ``buffer`` octets after. An upload's
    ``PUT`` carries the announced ``tsize`` as its ``Content-Length`` (chunked
    without one, and for netascii) and sends the last octets only when the
    transfer is complete: more octets than announced fail the transfer with
    ERROR 3, fewer with ERROR 0, and the origin stores nothing.
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
        if opener is None and base_url is not None and urlsplit(base_url).scheme.lower() not in _SCHEMES:
            raise ValueError("base_url is an http or https URL, not %r" % base_url)
        self.base_url = base_url
        self.url_for = url_for
        self.writable = writable
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.buffer = buffer
        self._restricted = opener is None
        self._open = (opener or _http_opener()).open
        self._agent = _user_agent()
        self._refused_warned = False

    def url(self, context: Any) -> str:
        if self.url_for is not None:
            url = self.url_for(context)
            if self._restricted and urlsplit(url).scheme.lower() not in _SCHEMES:
                if not self._refused_warned:
                    self._refused_warned = True
                    log.warning("url_for returned a URL that is not http or https; refused: %r", url[:80])
                raise TFTPError(TFTPErrorCode.ACCESS_VIOLATION, "not an http or https URL")
            return url
        name = normalize_name(context.filename)
        if not name or any(part == ".." for part in name.split("/")):
            raise TFTPError(TFTPErrorCode.ACCESS_VIOLATION)
        # The name is the client's octets decoded with surrogateescape; encoding them the
        # same way sends the octets that arrived.
        return self.base_url.rstrip("/") + "/" + quote(name, errors="surrogateescape")  # type: ignore[union-attr]

    def open_read(self, context: Any) -> Pipe:
        request = urllib.request.Request(
            self.url(context), headers=_headers({"User-Agent": self._agent}, self.headers)
        )
        try:
            response = self._open(request, timeout=self.timeout)
        except Exception as exc:
            raise _tftp_error(exc) from exc
        length = response.headers.get("Content-Length")
        encoded = response.headers.get("Content-Encoding", "identity") != "identity"
        size = None if length is None or encoded else read_decimal(length.strip())
        pipe = _ReadAhead(self.buffer, size)
        read = getattr(response, "read1", response.read)

        def pump() -> None:
            try:
                with response:
                    while True:
                        chunk = read(_CHUNK)
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
        # A netascii upload is received decoded, so the announced size (the encoded one) does not describe it.
        announced = size if context.mode == "octet" else None
        pipe = _AnnouncedUpload(self.buffer, size, announced).for_upload()
        defaults = {"User-Agent": self._agent, "Content-Type": "application/octet-stream"}
        headers = _headers(defaults, self.headers)
        if announced is not None:
            headers["Content-Length"] = str(announced)

        def body() -> Iterator[bytes]:
            # With a Content-Length the last chunk is kept back until the transfer ends: the
            # origin then never holds an object the client was refused (ERROR 3) or that is
            # shorter than announced, and never sees a request after the announced octets.
            held = b""
            sent = 0
            while True:
                chunk = pipe.get(65536, timeout=self.timeout * 30)
                if not chunk:
                    if announced is not None and sent + len(held) != announced:
                        raise TFTPError(TFTPErrorCode.NOT_DEFINED, "fewer octets than the announced size")
                    if held:
                        yield held
                    return
                if announced is None:
                    yield chunk
                    continue
                if held:
                    yield held
                    sent += len(held)
                held = chunk

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
