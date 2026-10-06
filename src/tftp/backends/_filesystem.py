"""Files served from, and uploaded into, a directory."""

from __future__ import annotations

import os
import shutil
import sys
from typing import BinaryIO, Optional, Union

from ..exceptions import TFTPError
from ..listing import DirectoryListing
from ..packet._enums import TFTPErrorCode
from ..server._handler import AtomicWriter, TFTPRequestContext, TFTPWriter

__all__ = ["FilesystemBackend"]

_WINDOWS = sys.platform == "win32"
#: Read buffer of a served file: an open file costs this much until it ends,
#: and a request nothing follows up is held open for a while.
_READ_BUFFER = 16 * 1024
#: What ``os.path.isreserved`` (Python 3.13 on Windows) reads, for the Pythons without it:
#: the DOS device names, the superscript digits Windows treats as digits, and the console devices.
_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"]
    + ["%s%s" % (device, digit) for device in ("COM", "LPT") for digit in "123456789¹²³"]
)
#: Characters a Windows file name cannot hold; ``:`` opens an alternate data stream.
_NOT_IN_A_NAME = frozenset('<>:"|?*\\') | frozenset(map(chr, range(32)))


def _refused_on_windows(part: str) -> bool:
    """A name component Windows would alias, treat as a device or read as a stream.

    A trailing dot or space is dropped by the file system, so ``f.`` and ``f ``
    open ``f``; a device name opens the device whatever its extension or
    padding.
    """
    if part[-1] in ". " or not _NOT_IN_A_NAME.isdisjoint(part):
        return True
    isreserved = getattr(os.path, "isreserved", None)
    if isreserved is not None:
        return bool(isreserved(part))
    return part.partition(".")[0].rstrip(" ").upper() in _RESERVED


class FilesystemBackend:
    """Serves the files under ``root``.

    :param root: the directory served. Requests can never reach outside it:
        ``..`` components are refused, and the resolved path (symlinks
        included) must still be inside ``root``.
    :param writable: accept WRQ at all.
    :param create: a WRQ may create a file that does not exist.
    :param overwrite: a WRQ may replace a file that exists.
    :param backslash: treat ``\\`` in filenames as a separator, as Windows
        boot loaders send them (``\\boot\\bcd``).
    :param max_upload: refuse uploads announced (``tsize``) or grown larger
        than this many bytes, with ERROR 3.

    Uploads go through :class:`AtomicWriter`, so a failed or partial upload
    leaves no trace, and directories are never created.
    """

    opens_fast = True  # local files: the server opens them inline

    def __init__(
        self,
        root: Union[str, "os.PathLike[str]"],
        *,
        writable: bool = False,
        create: bool = True,
        overwrite: bool = False,
        backslash: bool = True,
        max_upload: Optional[int] = None,
    ) -> None:
        self.root = os.path.realpath(os.fspath(root))
        if not os.path.isdir(self.root):
            raise NotADirectoryError(self.root)
        self.writable = writable
        self.create = create
        self.overwrite = overwrite
        self.backslash = backslash
        self.max_upload = max_upload

    def resolve(self, filename: str) -> str:
        """The local path for ``filename``, or :class:`TFTPError` (2) if it escapes."""
        name = filename.replace("\\", "/") if self.backslash else filename
        parts = []
        for part in name.split("/"):
            if part in ("", "."):
                continue
            if part == ".." or "\0" in part:
                raise TFTPError(TFTPErrorCode.ACCESS_VIOLATION)
            if _WINDOWS and _refused_on_windows(part):
                raise TFTPError(TFTPErrorCode.ACCESS_VIOLATION)
            parts.append(part)
        if not parts:
            raise TFTPError(TFTPErrorCode.FILE_NOT_FOUND)
        path = os.path.join(self.root, *parts)
        real = os.path.realpath(path)
        try:
            inside = os.path.commonpath([real, self.root]) == self.root
        except ValueError:  # different drives on Windows
            inside = False
        if not inside:
            raise TFTPError(TFTPErrorCode.ACCESS_VIOLATION)
        return real

    def open_read(self, context: TFTPRequestContext) -> BinaryIO:
        if context.listing and _is_root(context.filename, self.backslash):
            path = self.root
        else:
            path = self.resolve(context.filename)
        if context.listing and os.path.isdir(path):
            return DirectoryListing(path, root=self.root)
        if not os.path.isfile(path):
            raise TFTPError(TFTPErrorCode.FILE_NOT_FOUND)
        return open(path, "rb", buffering=_READ_BUFFER)

    def open_write(self, context: TFTPRequestContext, size: Optional[int]) -> TFTPWriter:
        if not self.writable:
            raise TFTPError(TFTPErrorCode.ACCESS_VIOLATION, "server is read-only")
        path = self.resolve(context.filename)
        if os.path.isdir(path):
            raise TFTPError(TFTPErrorCode.ACCESS_VIOLATION)
        exists = os.path.exists(path)
        if exists and not self.overwrite:
            raise TFTPError(TFTPErrorCode.FILE_EXISTS)
        if not exists and not self.create:
            raise TFTPError(TFTPErrorCode.FILE_NOT_FOUND)
        directory = os.path.dirname(path)
        if not os.path.isdir(directory):
            raise TFTPError(TFTPErrorCode.FILE_NOT_FOUND, "directory not found")
        if size is not None:
            if self.max_upload is not None and size > self.max_upload:
                raise TFTPError(TFTPErrorCode.DISK_FULL, "file too large")
            try:
                if shutil.disk_usage(directory).free < size:
                    raise TFTPError(TFTPErrorCode.DISK_FULL)
            except OSError:
                pass
        writer = AtomicWriter(path, overwrite=self.overwrite)
        if self.max_upload is not None:
            return _Capped(writer, self.max_upload)
        return writer


def _is_root(filename: str, backslash: bool) -> bool:
    name = filename.replace("\\", "/") if backslash else filename
    return all(part in ("", ".") for part in name.split("/"))


class _Capped:
    """Refuses to grow an upload past ``limit`` bytes."""

    copies_writes = True

    def __init__(self, inner: AtomicWriter, limit: int) -> None:
        self._inner = inner
        self._left = limit

    def write(self, data: Union[bytes, bytearray, memoryview]) -> int:
        self._left -= len(data)
        if self._left < 0:
            raise TFTPError(TFTPErrorCode.DISK_FULL, "file too large")
        return self._inner.write(data)

    def close(self) -> None:
        self._inner.close()

    def abort(self) -> None:
        self._inner.abort()
