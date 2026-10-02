"""Streaming file objects over TFTP, shared by :class:`TftpPath` and :class:`TftpUriPath`.

Each open file runs one transfer in a thread, joined to the caller by a
bounded :class:`~tftp.backends.Pipe`: reading never holds more than the
buffer in memory, and writing streams the upload as it is written. ``open()``
returns only once the server has answered the request, so a missing file or
a refused upload raises there, as with a local file.
"""

from __future__ import annotations

import errno
import io
import threading
from typing import Any, Callable, Optional

from ..backends.pipe import Pipe
from ..errors import (
    AccessViolation,
    DiskFull,
    FileAlreadyExists,
    FileNotFound,
    TftpError,
    TransferTimeout,
)

__all__ = ["os_error", "open_reader", "open_writer", "BUFFER", "STALL_TIMEOUT"]

#: Bytes buffered between the transfer thread and the caller.
BUFFER = 1 << 20
#: Seconds either side may wait for the other before giving up.
STALL_TIMEOUT = 60.0


def os_error(exc: BaseException, path: Any) -> OSError:
    """The ``OSError`` pathlib code expects for a TFTP failure."""
    if isinstance(exc, OSError) and not isinstance(exc, TftpError):
        return exc
    name = str(path)
    if isinstance(exc, FileNotFound):
        return FileNotFoundError(errno.ENOENT, exc.message, name)
    if isinstance(exc, AccessViolation):
        return PermissionError(errno.EACCES, exc.message, name)
    if isinstance(exc, FileAlreadyExists):
        return FileExistsError(errno.EEXIST, exc.message, name)
    if isinstance(exc, DiskFull):
        return OSError(errno.ENOSPC, exc.message, name)
    if isinstance(exc, TransferTimeout):
        return TimeoutError(errno.ETIMEDOUT, exc.message, name)
    if isinstance(exc, TftpError):
        error = OSError(errno.EIO, str(exc), name)
        error.__cause__ = exc
        return error
    return OSError(errno.EIO, str(exc), name)


class _Sink:
    """Blocking writer for the transfer thread (backpressure from the reader)."""

    _tftp_copies_ = True

    def __init__(self, pipe: Pipe) -> None:
        self._pipe = pipe

    def write(self, data) -> int:
        self._pipe.put(data, timeout=STALL_TIMEOUT)
        return len(data)


class _Source:
    """Blocking reader for the transfer thread (fed by the caller's writes)."""

    size: Optional[int] = None

    def __init__(self, pipe: Pipe) -> None:
        self._pipe = pipe

    def read(self, n: int) -> bytes:
        return self._pipe.get(n, timeout=STALL_TIMEOUT)


def _start(work: Callable[[Callable[..., Any]], Any], path: Any) -> "tuple[threading.Thread, list]":
    """Run ``work(on_negotiated)`` in a thread; return once the server answered."""
    answered = threading.Event()
    outcome: list = []  # [exception] on failure

    def run() -> None:
        try:
            work(lambda negotiated, peer: answered.set())
        except BaseException as exc:
            outcome.append(exc)
        finally:
            answered.set()

    thread = threading.Thread(target=run, name="tftp-path", daemon=True)
    thread.start()
    answered.wait()
    if outcome and not thread.is_alive():
        raise os_error(outcome[0], path)
    return thread, outcome


class _Reader(io.RawIOBase):
    def __init__(self, client_factory: Callable[..., Any], filename: str, mode: str, path: Any) -> None:
        self._path = path
        self._thread: Optional[threading.Thread] = None  # set once the server answered
        self._outcome: list = []
        self._pipe = Pipe(BUFFER)
        sink = _Sink(self._pipe)

        def work(on_negotiated: Callable[..., Any]) -> None:
            client = client_factory(on_negotiated=on_negotiated)
            try:
                client.download(filename, sink, mode=mode)
            except BaseException as exc:
                self._pipe.finish(exc)
                raise
            self._pipe.finish()

        self._thread, self._outcome = _start(work, path)

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        try:
            chunk = self._pipe.get(len(buffer), timeout=STALL_TIMEOUT)
        except (TftpError, BrokenPipeError) as exc:
            raise os_error(exc, self._path) from None
        n = len(chunk)
        buffer[:n] = chunk
        return n

    def close(self) -> None:
        if not self.closed and self._thread is not None:
            self._pipe.close()  # an unfinished download is abandoned
            self._thread.join(STALL_TIMEOUT)
        super().close()


class _Writer(io.RawIOBase):
    def __init__(self, client_factory: Callable[..., Any], filename: str, mode: str, path: Any) -> None:
        self._path = path
        self._thread: Optional[threading.Thread] = None  # set once the server answered
        self._outcome: list = []
        self._pipe = Pipe(BUFFER)
        source = _Source(self._pipe)

        def work(on_negotiated: Callable[..., Any]) -> None:
            client = client_factory(on_negotiated=on_negotiated, tsize=False)
            try:
                client.upload(filename, source, mode=mode)
            except BaseException:
                self._pipe.close()  # nobody reads any more: make write() fail now
                raise

        self._thread, self._outcome = _start(work, path)

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:
        if self._outcome:
            raise os_error(self._outcome[0], self._path)
        try:
            self._pipe.put(bytes(data), timeout=STALL_TIMEOUT)
        except (TftpError, BrokenPipeError, TimeoutError):
            self._thread.join(STALL_TIMEOUT)
            if self._outcome:
                raise os_error(self._outcome[0], self._path) from None
            raise
        return len(data)

    def close(self) -> None:
        """End the upload and wait for the server's final ACK; re-raise its failure."""
        if self.closed:
            return
        if self._thread is None:  # open() failed: nothing to finish
            super().close()
            return
        self._pipe.finish()
        self._thread.join()
        super().close()
        if self._outcome:
            raise os_error(self._outcome[0], self._path)


def open_reader(client_factory: Callable[..., Any], filename: str, mode: str, path: Any) -> io.BufferedReader:
    """A buffered binary reader streaming ``filename``."""
    return io.BufferedReader(_Reader(client_factory, filename, mode, path))


def open_writer(client_factory: Callable[..., Any], filename: str, mode: str, path: Any) -> io.BufferedWriter:
    """A buffered binary writer streaming an upload of ``filename``; closing completes it."""
    return io.BufferedWriter(_Writer(client_factory, filename, mode, path))
