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
from ._common import error

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
    "Where to write it; '-' for stdout. Default: the remote file's basename"
    ("local",)

    def __call__(self) -> _ty.Optional[int]:
        mode = self.mode or "octet"
        if is_url(self.host):
            url = TFTPURL.parse(self.host)
            client = self._client(url.host, url.port, url)
            remote, target = url.filename, self.remote or basename(url.filename)
            mode = self.mode or url.mode
        else:
            if not self.remote:
                error("error: name the file to download (or give a tftp:// URL)")
                return 2
            client = self._client(self.host)
            remote, target = self.remote, self.local or basename(self.remote)

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
