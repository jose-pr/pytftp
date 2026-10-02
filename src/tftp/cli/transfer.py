"""``pytftp get`` and ``pytftp put``."""

from __future__ import annotations

import os as _os
import sys as _sys
import typing as _ty

from ..errors import TftpError
from .common import ClientCmd, error

__all__ = ["Get", "Put"]


def _basename(remote: str) -> str:
    return remote.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or "download"


class Get(ClientCmd):
    """Download a file (RRQ)."""

    _parsername_ = "get"
    _parseraliases_ = ["download"]

    host: str
    "Server name or address"
    ("host",)

    remote: str
    "File to request"
    ("remote",)

    local: _ty.Optional[str] = None
    "Where to write it; '-' for stdout. Default: the remote file's basename"
    ("local",)

    def __call__(self) -> "int | None":
        client = self._client(self.host)
        target = self.local or _basename(self.remote)
        try:
            if target == "-":
                result = client.download(self.remote, _sys.stdout.buffer, mode=self.mode)
                _sys.stdout.buffer.flush()
            else:
                result = client.download(self.remote, target, mode=self.mode)
        except TftpError as exc:
            error("error: %s" % exc)
            return 1
        if target == "-" and self.json_out:
            self.json_out = False  # stdout holds the file
        self._report(result)
        return None


class Put(ClientCmd):
    """Upload a file (WRQ)."""

    _parsername_ = "put"
    _parseraliases_ = ["upload"]

    host: str
    "Server name or address"
    ("host",)

    local: str
    "File to send; '-' for stdin"
    ("local",)

    remote: _ty.Optional[str] = None
    "Name to store it under. Default: the local file's basename"
    ("remote",)

    def __call__(self) -> "int | None":
        client = self._client(self.host)
        if self.local == "-":
            if not self.remote:
                error("error: a remote name is required when reading stdin")
                return 2
            source: _ty.Any = _sys.stdin.buffer
            remote = self.remote
        else:
            if not _os.path.isfile(self.local):
                error("error: no such file: %s" % self.local)
                return 2
            source = self.local
            remote = self.remote or _os.path.basename(self.local)
        try:
            result = client.upload(remote, source, mode=self.mode)
        except TftpError as exc:
            error("error: %s" % exc)
            return 1
        self._report(result)
        return None
