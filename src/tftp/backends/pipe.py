"""A bounded byte pipe between a worker thread and a transfer.

The transfer side never blocks: it raises :class:`WouldBlock` when the pipe
is empty (reading) or full (writing) and is woken through ``set_wakeup``
when that changes -- the contract a server transfer understands. The worker
side may block (``put``/``get`` with a timeout), since it runs in its own
thread. Errors travel through it: a producer's :meth:`fail` surfaces on the
consumer's next read, and vice versa.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from ..exceptions import TFTPError
from ..exceptions import WouldBlock

__all__ = ["Pipe"]


class Pipe:
    """A bounded FIFO of bytes with one producer and one consumer.

    :param capacity: bytes buffered at most; a producer blocks (or gets
        ``WouldBlock``) beyond it. This is the backpressure that keeps a slow
        consumer from making a fast producer buffer a whole file.
    :param size: the total size, when the producer knows it in advance; a
        server answers ``tsize`` from it.
    """

    copies_writes = True  # write() copies its argument

    def __init__(self, capacity: int = 1 << 20, size: Optional[int] = None) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self.size = size
        self._buffer = bytearray()
        self._lock = threading.Condition()
        self._eof = False  # producer finished
        self._closed = False  # consumer finished (or gave up)
        self._error: Optional[BaseException] = None
        self._wakeup: Optional[Callable[[], None]] = None
        #: Upload mode: set once the worker has reported through set_result().
        self._done = threading.Event()
        self._result_error: Optional[BaseException] = None

    # -- wiring ---------------------------------------------------------------

    def set_wakeup(self, callback: Callable[[], None]) -> None:
        """Called (from any thread) when the non-blocking side can proceed."""
        self._wakeup = callback

    def _wake(self) -> None:
        callback = self._wakeup
        if callback is not None:
            callback()

    # -- producer side --------------------------------------------------------

    def put(self, data, timeout: Optional[float] = None) -> None:
        """Blocking write for a worker thread; raises if the consumer is gone."""
        view = memoryview(data)
        while view:
            with self._lock:
                if not self._lock.wait_for(
                    lambda: self._closed or len(self._buffer) < self.capacity, timeout
                ):
                    raise TimeoutError("pipe consumer stalled")
                if self._closed:
                    raise BrokenPipeError("consumer closed the pipe")
                room = self.capacity - len(self._buffer)
                self._buffer += view[:room]
                view = view[room:]
                self._lock.notify_all()
            self._wake()

    def write(self, data) -> int:
        """Non-blocking write for a transfer: all of ``data`` or WouldBlock."""
        with self._lock:
            if self._error is not None:
                raise self._error
            if self._closed:
                raise BrokenPipeError("consumer closed the pipe")
            if self._buffer and len(self._buffer) + len(data) > self.capacity:
                raise WouldBlock()
            self._buffer += data
            self._lock.notify_all()
        return len(data)

    def finish(self, error: Optional[BaseException] = None) -> None:
        """The producer is done (or failed with ``error``)."""
        with self._lock:
            self._eof = True
            if error is not None and self._error is None:
                self._error = error
            self._lock.notify_all()
        self._wake()

    fail = finish

    # -- consumer side --------------------------------------------------------

    def readinto(self, view) -> int:
        """Non-blocking read for a transfer: some bytes, 0 at the end, or WouldBlock."""
        with self._lock:
            if self._buffer:
                n = min(len(view), len(self._buffer))
                view[:n] = self._buffer[:n]
                del self._buffer[:n]
                self._lock.notify_all()
            elif self._error is not None:
                raise self._error
            elif self._eof:
                return 0
            else:
                raise WouldBlock()
        self._wake()
        return n

    def get(self, n: int = 65536, timeout: Optional[float] = None) -> bytes:
        """Blocking read for a worker thread; ``b""`` at the end."""
        with self._lock:
            if not self._lock.wait_for(lambda: self._buffer or self._eof or self._error, timeout):
                raise TimeoutError("pipe producer stalled")
            if self._buffer:
                chunk = bytes(self._buffer[:n])
                del self._buffer[:n]
                self._lock.notify_all()
            elif self._error is not None:
                raise self._error
            else:
                chunk = b""
        if chunk:
            self._wake()
        return chunk

    def read(self, n: int = 65536) -> bytes:
        """Blocking ``read`` so a worker thread can treat the pipe as a file."""
        return self.get(n)

    # -- ending -----------------------------------------------------------------

    def close(self) -> None:
        """The transfer side is done.

        Download pipe (the transfer reads): the producer's next ``put``
        raises, which stops a worker that is still fetching.

        Upload pipe (:meth:`for_upload`, the transfer writes): marks the end
        of the data, then raises :class:`WouldBlock` until the worker calls
        :meth:`set_result` -- so the client's final ACK waits until the data
        really reached its destination -- and re-raises the worker's error if
        it had one.
        """
        if self._upload:
            with self._lock:
                self._eof = True
                self._lock.notify_all()
            if not self._done.is_set():
                raise WouldBlock()
            if self._result_error is not None:
                raise self._result_error
            return
        with self._lock:
            self._closed = True
            self._lock.notify_all()

    def abort(self, error: Optional[BaseException] = None) -> None:
        """Give up from the transfer side; the worker's next call raises."""
        with self._lock:
            self._closed = True
            self._eof = True
            if self._error is None:
                self._error = error or TFTPError(0, "transfer aborted")
            self._lock.notify_all()

    # -- upload mode --------------------------------------------------------------

    _upload = False

    def for_upload(self) -> "Pipe":
        """Make this an upload pipe, whose close() waits for :meth:`set_result`."""
        self._upload = True
        return self

    def set_result(self, error: Optional[BaseException] = None) -> None:
        """Worker side of an upload: finished, successfully or with ``error``."""
        self._result_error = error
        self._done.set()
        self._wake()
