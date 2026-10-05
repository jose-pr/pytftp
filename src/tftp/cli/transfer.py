"""``pytftp get``, ``pytftp put`` and ``pytftp ls``.

``HOST`` may also be a ``tftp://host[:port]/file[;mode=netascii]`` URL, which
then names the remote file too::

    pytftp get 192.0.2.1 boot/pxelinux.0 pxelinux.0
    pytftp get tftp://192.0.2.1/boot/pxelinux.0 pxelinux.0
    pytftp put tftp://192.0.2.1/incoming/log.txt log.txt
    pytftp ls 192.0.2.1 boot        # needs a server speaking x-list
"""

from __future__ import annotations

import json as _json
import os as _os
import sys as _sys
import time as _time
import typing as _ty

from ..exceptions import TFTPError
from ..uri import TFTPURL
from .common import ClientCmd, error

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

    def __call__(self) -> "int | None":
        mode = self.mode
        if _is_url(self.host):
            url = TFTPURL.parse(self.host)
            client = self._client(url.host, url.port)
            remote, target = url.filename, self.remote or _basename(url.filename)
            if mode == "octet":
                mode = url.mode
        else:
            if not self.remote:
                error("error: name the file to download (or give a tftp:// URL)")
                return 2
            client = self._client(self.host)
            remote, target = self.remote, self.local or _basename(self.remote)
        try:
            if target == "-":
                result = client.download(remote, _sys.stdout.buffer, mode=mode)
                _sys.stdout.buffer.flush()
                self.json_out = False  # stdout holds the file
            else:
                result = client.download(remote, target, mode=mode)
        except TFTPError as exc:
            error("error: %s" % exc)
            return 1
        finally:
            self._close_trace()
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

    def __call__(self) -> "int | None":
        mode = self.mode
        if _is_url(self.host):
            url = TFTPURL.parse(self.host)
            client = self._client(url.host, url.port)
            remote: _ty.Optional[str] = url.filename
            if mode == "octet":
                mode = url.mode
        else:
            client = self._client(self.host)
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
        try:
            result = client.upload(remote, source, mode=mode)
        except TFTPError as exc:
            error("error: %s" % exc)
            return 1
        finally:
            self._close_trace()
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

    def __call__(self) -> "int | None":
        if _is_url(self.host):
            url = TFTPURL.parse(self.host)
            client = self._client(url.host, url.port)
            remote = url.filename
        else:
            client = self._client(self.host)
            remote = self.remote
        try:
            entries = client.listdir(remote)
        except (TFTPError, OSError) as exc:
            error("error: %s" % exc)
            return 1
        finally:
            self._close_trace()
        if self.json_out:
            print(_json.dumps([entry._asdict() for entry in entries], indent=2))
            return None
        for entry in entries:
            when = (
                "-" if entry.mtime is None else _time.strftime("%Y-%m-%d %H:%M", _time.localtime(entry.mtime))
            )
            print(
                "%s %12d %16s %s%s"
                % ("d" if entry.is_dir else "-", entry.size, when, entry.name, "/" if entry.is_dir else "")
            )
        return None
