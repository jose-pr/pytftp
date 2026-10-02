"""Request handlers beyond the local filesystem.

- :class:`MemoryHandler` -- a dictionary of files.
- :class:`HttpHandler` -- a TFTP-to-HTTP(S) gateway (``urllib``, no extra
  dependency); ``PUT`` for uploads.
- :class:`UpstreamHandler` -- a terminating proxy to another TFTP server, each
  side negotiating its own options.
- :class:`Pipe` -- the bounded, backpressured byte pipe they use to feed a
  transfer from a worker thread; usable for your own handlers.
"""

from __future__ import annotations

from .http import HttpHandler
from .memory import MemoryHandler, normalize_name
from .pipe import Pipe
from .proxy import UpstreamHandler

__all__ = ["MemoryHandler", "HttpHandler", "UpstreamHandler", "Pipe", "normalize_name"]
