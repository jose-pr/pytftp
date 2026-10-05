"""``tftp://`` URIs (RFC 3617).

    tftp://host/path/file
    tftp://[2001:db8::1]:6969/file;mode=netascii

The file name is everything after the ``/`` that ends the authority,
percent-decoded; ``mode`` defaults to ``octet``. The port, absent from
RFC 3617's grammar but universal in practice, defaults to 69.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple, Optional

if TYPE_CHECKING:
    from netimps import HostLike
from urllib.parse import quote, unquote, urlsplit

__all__ = ["TftpURL", "parse_url", "format_url", "download_url", "upload_url"]


class TftpURL(NamedTuple):
    host: str
    port: int
    filename: str
    mode: str = "octet"

    def __str__(self) -> str:
        return format_url(self.host, self.filename, self.port, self.mode)


def parse_url(url: str) -> TftpURL:
    """Split a ``tftp://`` URI. Raises ``ValueError`` for anything else."""
    parts = urlsplit(url)
    if parts.scheme.lower() != "tftp":
        raise ValueError("not a tftp:// URL: %r" % url)
    if not parts.hostname:
        raise ValueError("tftp:// URL without a host: %r" % url)
    path = parts.path
    mode = "octet"
    if ";" in path:
        path, _, params = path.partition(";")
        for param in params.split(";"):
            key, _, value = param.partition("=")
            if key.lower() == "mode":
                mode = value.lower()
            elif key:
                raise ValueError("unknown tftp:// parameter %r" % key)
    if mode not in ("octet", "netascii"):
        raise ValueError("unsupported mode %r in %r" % (mode, url))
    filename = unquote(path[1:] if path.startswith("/") else path)
    if not filename:
        raise ValueError("tftp:// URL without a file name: %r" % url)
    try:
        port = parts.port or 69
    except ValueError as exc:
        raise ValueError("bad port in %r" % url) from exc
    host = parts.hostname
    if parts.netloc.startswith("[") and "%" in parts.netloc:
        # urlsplit drops an IPv6 zone from .hostname on some versions.
        host = parts.netloc[1 : parts.netloc.index("]")]
    return TftpURL(host, port, filename, mode)


def format_url(host: "HostLike", filename: str, port: int = 69, mode: str = "octet") -> str:
    """The ``tftp://`` URI for a file; the inverse of :func:`parse_url`.

    ``host`` is a name or address string, an ``ipaddress`` address or
    interface, or a ``netimps.Host``.
    """
    from netimps import join_host

    authority = join_host(host, None if port == 69 else port)
    suffix = "" if mode == "octet" else ";mode=%s" % mode
    return "tftp://%s/%s%s" % (authority, quote(filename, safe="/"), suffix)


def download_url(url: str, dest: Any, *, progress: Optional[Any] = None, **client_options: Any):
    """Download the file a ``tftp://`` URI names; ``client_options`` go to ``Client``."""
    from .client import Client

    target = parse_url(url)
    return Client(target.host, target.port, **client_options).download(
        target.filename, dest, mode=target.mode, progress=progress
    )


def upload_url(url: str, source: Any, *, progress: Optional[Any] = None, **client_options: Any):
    """Upload to the file a ``tftp://`` URI names; ``client_options`` go to ``Client``."""
    from .client import Client

    target = parse_url(url)
    return Client(target.host, target.port, **client_options).upload(
        target.filename, source, mode=target.mode, progress=progress
    )
