"""Request handlers beyond the local filesystem.

- :class:`FilesystemBackend` -- a directory (what ``TFTPServer("/srv/tftp")`` serves).
- :class:`MemoryBackend` -- a dictionary of files.
- :class:`HTTPBackend` -- a TFTP-to-HTTP(S) gateway (``urllib``, no extra
  dependency); ``PUT`` for uploads.
- :class:`UpstreamBackend` -- a terminating proxy to another TFTP server, each
  side negotiating its own options.
- :class:`Pipe` -- the bounded, backpressured byte pipe they use to feed a
  transfer from a worker thread; usable for your own handlers.
"""

from __future__ import annotations

from .filesystem import FilesystemBackend
from .http import HTTPBackend
from .memory import MemoryBackend, normalize_name
from .pipe import Pipe
from .proxy import UpstreamBackend

__all__ = ["FilesystemBackend", "MemoryBackend", "HTTPBackend", "UpstreamBackend", "Pipe", "normalize_name"]
