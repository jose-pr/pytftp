"""pathlib-style access to TFTP files (``path`` extra: ``pip install tftp[path]``).

- :class:`TftpPath` -- a :mod:`pathlib_next` ``Path`` bound to a
  :class:`~tftp.Client`: ``client.path("boot/pxelinux.0").read_bytes()``.
- :class:`TftpUriPath` -- the ``tftp://`` scheme for ``pathlib_next.uri.UriPath``:
  ``UriPath("tftp://192.0.2.1/boot/x").copy("s3://bucket/x")``.

TFTP moves whole files and nothing else, so reading, writing, ``stat()``
(a size probe that transfers nothing), ``exists()`` and ``copy()``/``move()``
work, and listing, deleting, renaming and directories raise
``NotImplementedError``. Reads and writes stream through a bounded buffer.
"""

from __future__ import annotations

try:
    import pathlib_next
except ImportError as exc:  # pragma: no cover - exercised without the extra
    raise ImportError("tftp.path needs the 'path' extra: pip install 'tftp[path]'") from exc
del pathlib_next  # only checking that the extra is installed

from .local import TftpPath

__all__ = ["TftpPath", "TftpUriPath", "TftpBackend"]


def __getattr__(name: str):
    # TftpUriPath needs pathlib_next.uri, which needs uritools (pathlib-next's
    # own "uri" extra, included in ours); import it only when asked.
    if name in ("TftpUriPath", "TftpBackend"):
        from . import uri

        return getattr(uri, name)
    raise AttributeError(name)
