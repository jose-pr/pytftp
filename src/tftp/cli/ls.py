"""``pytftp ls``: list a directory on a server speaking pytftp's x-list extension.

pytftp ls 192.0.2.1 boot
"""

from __future__ import annotations

import json as _json
import time as _time
import typing as _ty

from .._text import escape
from .._uri import TFTPURL
from ._client import ClientCmd, is_url
from ._common import write_line

__all__ = ["Ls"]


class Ls(ClientCmd):
    """List a directory (pytftp's x-list extension: a pytftp server with listing allowed)."""

    _parsername_ = "ls"
    _parseraliases_ = ["list"]

    host: str
    "Server name or address, or a tftp:// URL naming the directory"
    ("host",)

    remote: str = ""
    "Directory to list. Default: the server's root"
    ("remote",)

    def _check(self) -> None:
        super()._check()
        if is_url(self.host) and self.remote:
            raise ValueError("a tftp:// URL names the directory: give no second argument")

    def __call__(self) -> _ty.Optional[int]:
        self._check()
        if is_url(self.host):
            url = TFTPURL.parse(self.host)
            client = self._client(url.host, url.port, url)
            remote = url.filename
        else:
            client = self._client(self.host)
            remote = self.remote
        entries, status = self._transfer(client, lambda: client.listdir(remote))
        if status:
            return status
        if self.json_out:
            write_line(_json.dumps([entry._asdict() for entry in entries]))
            return None
        for entry in entries:
            when = (
                "-" if entry.mtime is None else _time.strftime("%Y-%m-%d %H:%M", _time.localtime(entry.mtime))
            )
            write_line(
                "%s %12d %16s %s%s"
                % (
                    "d" if entry.is_dir else "-",
                    entry.size,
                    when,
                    escape(entry.name),
                    "/" if entry.is_dir else "",
                )
            )
        return None
