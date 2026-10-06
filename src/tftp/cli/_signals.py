"""Stopping a running server or relay on a signal."""

from __future__ import annotations

import contextlib as _contextlib
import signal as _signal
import threading as _threading
import typing as _ty

__all__ = ["shutdown_on_signal"]


@_contextlib.contextmanager
def shutdown_on_signal(server: _ty.Any) -> _ty.Iterator[None]:
    """Stop ``server`` (``TFTPServer`` or ``TFTPRelay``) on Ctrl-C, Ctrl-Break and SIGTERM.

    A signal handler only runs when the main thread executes bytecode, and a
    loop blocked in ``select`` does not, on Windows even for Ctrl-C. The
    signal also writes a byte to the server's wake socket, which wakes the
    wait at once, so an idle server costs no polling. Nothing is installed
    outside the main thread. Handlers and the wake descriptor are restored on
    exit.
    """
    if _threading.current_thread() is not _threading.main_thread():
        yield
        return

    def stop(signum: int, frame: _ty.Any) -> None:
        server.shutdown()

    previous = {}
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        number = getattr(_signal, name, None)
        if number is not None:
            try:
                previous[number] = _signal.signal(number, stop)
            except (ValueError, OSError):
                pass
    try:
        # The server's own wake socket: non-blocking, and drained by its loop.
        wakeup = _signal.set_wakeup_fd(server._wake_w.fileno(), warn_on_full_buffer=False)
    except (ValueError, OSError):
        wakeup = None
    try:
        yield
    finally:
        if wakeup is not None:
            _signal.set_wakeup_fd(wakeup)
        for number, handler in previous.items():
            _signal.signal(number, handler)
