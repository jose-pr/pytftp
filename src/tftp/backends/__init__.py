"""Request handlers beyond the local filesystem.

- :class:`FilesystemBackend` -- a directory (what ``TFTPServer("/srv/tftp")`` serves).
- :class:`MemoryBackend` -- a dictionary of files.
- :class:`HTTPBackend` -- a TFTP-to-HTTP(S) gateway (``urllib``, no extra
  dependency); ``PUT`` for uploads.
- :class:`UpstreamBackend` -- a terminating proxy to another TFTP server, each
  side negotiating its own options.
- :class:`Remap` -- rewrite the requested filenames, with regular expressions,
  before another handler sees them.
- :class:`PerClient` -- serve each client from a directory named for its address.
- :class:`CaseInsensitive` -- a :class:`FilesystemBackend` that finds files
  whatever the case of the requested name.
- :class:`Pipe` -- the bounded, backpressured byte pipe they use to feed a
  transfer from a worker thread; usable for your own handlers.
"""

from __future__ import annotations

from ._case import CaseInsensitive
from ._filesystem import FilesystemBackend
from ._http import HTTPBackend
from ._memory import MemoryBackend, normalize_name
from ._per_client import PerClient
from ._pipe import Pipe
from ._proxy import UpstreamBackend
from ._remap import Remap

__all__ = [
    "CaseInsensitive",
    "FilesystemBackend",
    "HTTPBackend",
    "MemoryBackend",
    "PerClient",
    "Pipe",
    "Remap",
    "UpstreamBackend",
    "normalize_name",
]
