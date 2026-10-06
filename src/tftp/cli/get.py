"""``pytftp get``: download a file.

``HOST`` may also be a ``tftp://host[:port]/file[;mode=netascii]`` URL, which
then names the remote file too and may carry transfer options, which a flag
overrides::

    pytftp get 192.0.2.1 boot/pxelinux.0 pxelinux.0
    pytftp get tftp://192.0.2.1/boot/pxelinux.0 pxelinux.0
    pytftp get "tftp://192.0.2.1/boot/pxelinux.0?blksize=1024&windowsize=8" pxelinux.0
"""

from __future__ import annotations

import sys as _sys
import typing as _ty

from .._uri import TFTPURL
from ._client import ClientCmd, basename, is_url

__all__ = ["Get"]


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
    "Where to write it; '-' for stdout (then no --json). Default: the remote file's basename"
    ("local",)

    def _target(self) -> str:
        """Where the file goes: the name given, else the remote file's basename."""
        if is_url(self.host):
            return self.remote or basename(TFTPURL.parse(self.host).filename)
        return self.local or basename(self.remote or "")

    def _check(self) -> None:
        super()._check()
        if is_url(self.host):
            if self.local is not None:
                raise ValueError(
                    "a tftp:// URL names the file: give only where to write it, and no third argument"
                )
        elif not self.remote:
            raise ValueError("name the file to download (or give a tftp:// URL)")
        if self.json_out and self._target() == "-":
            raise ValueError("--json cannot be used when the file goes to stdout ('-')")

    def __call__(self) -> _ty.Optional[int]:
        self._check()
        mode = self.mode or "octet"
        target = self._target()
        if is_url(self.host):
            url = TFTPURL.parse(self.host)
            client = self._client(url.host, url.port, url)
            remote = url.filename
            mode = self.mode or url.mode
        else:
            client = self._client(self.host)
            remote = self.remote or ""

        def work() -> _ty.Any:
            if target == "-":
                result = client.download(remote, _sys.stdout.buffer, mode=mode)
                _sys.stdout.buffer.flush()
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
        self._report(result, stdout_is_data=target == "-")
        return None
