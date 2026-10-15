"""pathlib-style access to TFTP files (``path`` extra: ``pip install tftp[path]``).

- :class:`TFTPPath` -- a :mod:`pathapi` ``Path`` bound to a
  :class:`~tftp.TFTPClient`: ``client.path("boot/pxelinux.0").read_bytes()``.
- :class:`TFTPURIPath` -- the ``tftp://`` scheme for ``uripath.URIPath``:
  ``URIPath("tftp://192.0.2.1/boot/x").copy("s3://bucket/x")``.

TFTP moves whole files and nothing else, so reading, writing, ``stat()``
(a size probe that transfers nothing), ``exists()`` and ``copy()`` work, and
listing, deleting, renaming, ``move()`` from TFTP and directories raise
``NotImplementedError``. Reads and writes stream through a bounded buffer; a
``with`` block over a write that raises, and a write never closed, abandon the upload.
"""

from __future__ import annotations

try:
    import pathapi
    import uripath
except ImportError as exc:  # pragma: no cover - exercised without the extra
    raise ImportError("tftp.path needs the 'path' extra: pip install 'tftp[path]'") from exc
del pathapi, uripath  # only checking that the extra is installed

from ._local import TFTPPath
from ._uri import TFTPURIPath

__all__ = ["TFTPPath", "TFTPURIPath"]
