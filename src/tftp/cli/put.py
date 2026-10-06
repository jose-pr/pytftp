"""``pytftp put``: upload a file.

pytftp put 192.0.2.1 firmware.bin --blksize 1428 --windowsize 16
pytftp put tftp://192.0.2.1/incoming/log.txt log.txt
"""

from __future__ import annotations

import os as _os
import sys as _sys
import typing as _ty

from .._uri import TFTPURL
from ._client import ClientCmd, is_url
from ._common import error

__all__ = ["Put"]


class Put(ClientCmd):
    """Upload a file (WRQ)."""

    _parsername_ = "put"
    _parseraliases_ = ["upload"]

    host: str
    "Server name or address, or a tftp:// URL naming the remote file"
    ("host",)

    local: str
    "File to send; '-' for stdin"
    ("local",)

    remote: _ty.Optional[str] = None
    "Name to store it under. Default: the local file's basename"
    ("remote",)

    def __call__(self) -> _ty.Optional[int]:
        mode = self.mode or "octet"
        url = TFTPURL.parse(self.host) if is_url(self.host) else None
        if url is not None:
            remote: _ty.Optional[str] = url.filename
            mode = self.mode or url.mode
        else:
            remote = self.remote
        if self.local == "-":
            if not remote:
                error("error: a remote name is required when reading stdin")
                return 2
            source: _ty.Any = _sys.stdin.buffer
        else:
            if not _os.path.isfile(self.local):
                error("error: no such file: %s" % self.local)
                return 2
            source = self.local
            remote = remote or _os.path.basename(self.local)
        client = self._client(url.host, url.port, url) if url is not None else self._client(self.host)
        result, status = self._transfer(client, lambda: client.upload(remote, source, mode=mode))
        if status:
            return status
        self._report(result)
        return None
