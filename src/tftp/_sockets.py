"""Socket buffer sizing for windowed transfers -- TFTP's arithmetic over netimps."""

from __future__ import annotations

import logging
import socket
from typing import Tuple

__all__ = ["fit_window"]

log = logging.getLogger("tftp")

#: Upper bound for buffer growth; the kernel may grant less (Linux rmem_max).
_MAX_BUFFER = 8 << 20


def fit_window(sock: socket.socket, blksize: int, windowsize: int) -> Tuple[int, int]:
    """Grow the socket buffers to hold two windows of DATA; returns what was granted.

    Default UDP buffers are small (64 KiB on Windows), so a window of large
    blocks overflows them and the tail of every window is dropped, costing a
    full timeout each time. The kernel may grant less than asked, which is
    logged rather than assumed away.
    """
    if windowsize <= 1 and blksize <= 8192:
        return (0, 0)
    from netimps import set_buffer_size

    want = min(2 * max(windowsize, 1) * (blksize + 64), _MAX_BUFFER)
    try:
        granted = set_buffer_size(sock, receive=want, send=want)
    except OSError:
        return (0, 0)
    if min(granted) < want:
        log.debug("asked for %d-byte socket buffers, granted %s", want, granted)
    return granted
