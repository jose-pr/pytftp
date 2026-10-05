"""Files served from, and uploaded into, a dictionary."""

from __future__ import annotations

import io
import threading
from typing import Any, Dict, Mapping, Optional

from ..exceptions import TFTPError
from ..packet import TFTPErrorCode

__all__ = ["MemoryHandler", "normalize_name"]


def normalize_name(filename: str, backslash: bool = True) -> str:
    """``/a\\b//c`` -> ``a/b/c``: the key a request maps to."""
    name = filename.replace("\\", "/") if backslash else filename
    return "/".join(part for part in name.split("/") if part and part != ".")


class MemoryHandler:
    """Serves ``files`` (name -> bytes); uploads land in it when ``writable``.

    Names are normalised (:func:`normalize_name`), so ``/boot/x`` and
    ``boot\\x`` find the same entry. An upload replaces the entry only once
    it is complete. Safe to read and update ``files`` from other threads.
    """

    _tftp_fast_open_ = True

    def __init__(
        self,
        files: Optional[Mapping[str, bytes]] = None,
        *,
        writable: bool = False,
        overwrite: bool = True,
        max_upload: Optional[int] = None,
    ) -> None:
        self.files: Dict[str, bytes] = {normalize_name(k): bytes(v) for k, v in (files or {}).items()}
        self.writable = writable
        self.overwrite = overwrite
        self.max_upload = max_upload
        self._lock = threading.Lock()

    def open_read(self, context: Any) -> io.BytesIO:
        with self._lock:
            data = self.files.get(normalize_name(context.filename))
        if data is None:
            raise TFTPError(TFTPErrorCode.FILE_NOT_FOUND)
        return io.BytesIO(data)

    def open_write(self, context: Any, size: Optional[int]) -> "_MemoryUpload":
        if not self.writable:
            raise TFTPError(TFTPErrorCode.ACCESS_VIOLATION, "server is read-only")
        name = normalize_name(context.filename)
        if not name:
            raise TFTPError(TFTPErrorCode.FILE_NOT_FOUND)
        with self._lock:
            if name in self.files and not self.overwrite:
                raise TFTPError(TFTPErrorCode.FILE_EXISTS)
        if size is not None and self.max_upload is not None and size > self.max_upload:
            raise TFTPError(TFTPErrorCode.DISK_FULL, "file too large")
        return _MemoryUpload(self, name)

    def _store(self, name: str, data: bytes) -> None:
        with self._lock:
            if name in self.files and not self.overwrite:
                raise TFTPError(TFTPErrorCode.FILE_EXISTS)
            self.files[name] = data


class _MemoryUpload:
    _tftp_copies_ = True

    def __init__(self, owner: MemoryHandler, name: str) -> None:
        self._owner = owner
        self._name = name
        self._buffer = bytearray()

    def write(self, data) -> int:
        limit = self._owner.max_upload
        if limit is not None and len(self._buffer) + len(data) > limit:
            raise TFTPError(TFTPErrorCode.DISK_FULL, "file too large")
        self._buffer += data
        return len(data)

    def close(self) -> None:
        if self._buffer is not None:
            data, self._buffer = bytes(self._buffer), None  # type: ignore[assignment]
            self._owner._store(self._name, data)

    def abort(self) -> None:
        self._buffer = None  # type: ignore[assignment]
