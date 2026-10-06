"""``pytftp get``, ``pytftp put`` and ``pytftp ls``.

``HOST`` may also be a ``tftp://host[:port]/file[;mode=netascii]`` URL, which
then names the remote file too and may carry transfer options, which a flag
overrides::

    pytftp get 192.0.2.1 boot/pxelinux.0 pxelinux.0
    pytftp get tftp://192.0.2.1/boot/pxelinux.0 pxelinux.0
    pytftp get "tftp://192.0.2.1/boot/pxelinux.0?blksize=1024&windowsize=8" pxelinux.0
    pytftp put tftp://192.0.2.1/incoming/log.txt log.txt
    pytftp ls 192.0.2.1 boot        # needs a server speaking x-list
"""

from __future__ import annotations

import json as _json
import os as _os
import sys as _sys
import time as _time
import typing as _ty

from .._text import escape
from .._uri import TFTPURL
from .common import ClientCmd, error, write_line

__all__ = ["Get", "Put", "Ls"]


def _basename(remote: str) -> str:
    return remote.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or "download"


def _is_url(text: str) -> bool:
    return text.lower().startswith("tftp://")


class Get(ClientCmd):
    """Download a file (RRQ)."""

    _parsername_ = "get"
    _parseraliases_ = ["download"]

    host: str
    "Server name or address, or a tftp:// URL naming the file"
    ("host",)

    remote: _ty.Optional[str] = None
    "File to request (with a URL: where to write it)"
    ("remote",)

    local: _ty.Optional[str] = None
    "Where to write it; '-' for stdout. Default: the remote file's basename"
    ("local",)

    def __call__(self) -> _ty.Optional[int]:
        mode = self.mode or "octet"
        if _is_url(self.host):
            url = TFTPURL.parse(self.host)
            client = self._client(url.host, url.port, url)
            remote, target = url.filename, self.remote or _basename(url.filename)
            mode = self.mode or url.mode
        else:
            if not self.remote:
                error("error: name the file to download (or give a tftp:// URL)")
                return 2
            client = self._client(self.host)
            remote, target = self.remote, self.local or _basename(self.remote)

        def work() -> _ty.Any:
            if target == "-":
                result = client.download(remote, _sys.stdout.buffer, mode=mode)
                _sys.stdout.buffer.flush()
                self.json_out = False  # stdout holds the file
                return result
            try:
                return client.download(remote, target, mode=mode)
            except OSError as exc:
                if exc.filename is None:
                    raise
                # The file that failed is the temporary one beside the target: name the target.
                raise OSError(exc.errno, exc.strerror, target) from exc

        result, status = self._transfer(client, work)
        if status:
            return status
        self._report(result)
        return None


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
        url = TFTPURL.parse(self.host) if _is_url(self.host) else None
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


class Ls(ClientCmd):
    """List a directory (pytftp's x-list extension: a pytftp server with listing allowed)."""

    _parsername_ = "ls"
    _parseraliases_ = ["list"]

    host: str
    "Server name or address, or a tftp:// URL naming the directory"
    ("host",)

    remote: str = ""
    "Directory to list; default: the server's root"
    ("remote",)

    def __call__(self) -> _ty.Optional[int]:
        if _is_url(self.host):
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
            write_line(_json.dumps([entry._asdict() for entry in entries], indent=2))
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
