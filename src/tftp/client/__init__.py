"""The clients: :class:`TFTPClient` (blocking) and :class:`AsyncTFTPClient` (asyncio).

Siblings over one private base: the same arguments, the same options and the
same results, one waiting on sockets and one on the event loop. The one-shot
functions :func:`download` and :func:`upload` use the blocking client.
"""

from __future__ import annotations

from ._asyncio import AsyncTFTPClient
from ._core import MODES, RemoteStat
from ._sync import TFTPClient, download, upload

__all__ = ["AsyncTFTPClient", "MODES", "RemoteStat", "TFTPClient", "download", "upload"]
