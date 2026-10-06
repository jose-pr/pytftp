"""Files served from, and uploaded into, a dictionary."""

from __future__ import annotations

import io
import threading
from typing import Any, Dict, Mapping, Optional

from ..exceptions import TFTPError
from ..packet import TFTPErrorCode

__all__ = ["MemoryBackend", "normalize_name"]

#: Default for ``max_upload``: octets one upload may hold.
DEFAULT_MAX_UPLOAD = 16 << 20
#: Default for ``max_entries``: names the backend may hold.
DEFAULT_MAX_ENTRIES = 1024


def _bound(name: str, value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("%s is an int or None, not %s" % (name, type(value).__name__))
    if value < 0:
        raise ValueError("%s is not negative, got %d" % (name, value))
    return value


def normalize_name(filename: str, backslash: bool = True) -> str:
    """``/a\\b//c`` -> ``a/b/c``: the key a request maps to."""
    name = filename.replace("\\", "/") if backslash else filename
    return "/".join(part for part in name.split("/") if part and part != ".")


class MemoryBackend:
    """Serves ``files`` (name -> bytes); uploads land in it when ``writable``.

    Names are normalised (:func:`normalize_name`), so ``/boot/x`` and
    ``boot\\x`` find the same entry. An upload replaces the entry only once
    it is complete. Safe to read and update ``files`` from other threads.

    Whatever an anonymous peer may upload is bounded. ``max_upload`` is the
    octets one upload may hold (default 16 MiB): an announced ``tsize`` above
    it is refused with ERROR 3 at the request, and an upload that grows past it
    fails with ERROR 3. ``max_entries`` is the names the backend may hold
    (default 1024, those in ``files`` included): an upload to a new name beyond
    it fails with ERROR 3, while a name that exists can still be replaced. The
    memory a writable backend can hold is at most the product of the two;
    ``None`` removes a bound.
    """

    opens_fast = True

    def __init__(
        self,
        files: Optional[Mapping[str, bytes]] = None,
        *,
        writable: bool = False,
        overwrite: bool = True,
        max_upload: Optional[int] = DEFAULT_MAX_UPLOAD,
        max_entries: Optional[int] = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self.files: Dict[str, bytes] = {normalize_name(k): bytes(v) for k, v in (files or {}).items()}
        self.writable = writable
        self.overwrite = overwrite
        self.max_upload = _bound("max_upload", max_upload)
        self.max_entries = _bound("max_entries", max_entries)
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
            self._admit(name)
        if size is not None and self.max_upload is not None and size > self.max_upload:
            raise TFTPError(TFTPErrorCode.DISK_FULL, "file too large")
        return _MemoryUpload(self, name)

    def _admit(self, name: str) -> None:
        """Refuse a name the backend may not (re)write; the caller holds the lock."""
        if name in self.files:
            if not self.overwrite:
                raise TFTPError(TFTPErrorCode.FILE_EXISTS)
        elif self.max_entries is not None and len(self.files) >= self.max_entries:
            raise TFTPError(TFTPErrorCode.DISK_FULL, "too many files")

    def _store(self, name: str, data: bytes) -> None:
        with self._lock:
            self._admit(name)
            self.files[name] = data


class _MemoryUpload:
    copies_writes = True

    def __init__(self, owner: MemoryBackend, name: str) -> None:
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
